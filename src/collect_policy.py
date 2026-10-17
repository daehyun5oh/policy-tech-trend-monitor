"""정책 자료 수집기 (이슈 #8, 담당 B)

`config/sources.csv` 에서 수집이 허용된 정책 출처를 읽어 원문을 가져오고,
`docs/data_dictionary.md` 규격의 `documents.csv` 와 실행 기록을 만든다.

허용된 경로만 쓴다. `access_check_status` 가 `terms_ok` 또는 `terms_ok_local`
인 출처만 수집한다 (기획안 §11).

현재 구현된 접근 방식은 `rss` 뿐이다. `html_list`(미국·중국 5곳)는 사이트마다
구조가 달라 이슈 #37 로 분리했다.

사용법
    python src/collect_policy.py --dry-run
    python src/collect_policy.py
    python src/collect_policy.py --source-id policy-kr-001 --limit 5
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
SOURCES_CSV = ROOT / "config" / "sources.csv"
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"

KST = timezone(timedelta(hours=9))

COLLECTABLE_STATUS = {"terms_ok", "terms_ok_local"}

# data_dictionary.md §2 의 필드 순서
DOCUMENT_FIELDS = [
    "document_id", "source_type", "source_name", "source_document_id",
    "title", "original_url", "published_date", "collected_at",
    "analysis_scope", "content_hash", "status", "exclusion_reason",
    "duplicate_of",
]

# data_dictionary.md §5 의 추적용 질의 매개변수
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid",
}

USER_AGENT = "policy-tech-trend-monitor/0.1 (research prototype)"


# ──────────────────────────── 공통 유틸 ────────────────────────────

def normalize_url(url: str) -> str:
    """data_dictionary.md §5 의 URL 정규화 규칙."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    query.sort(key=lambda kv: kv[0])
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def content_hash(text: str) -> str:
    """data_dictionary.md §5 의 content_hash 계산 규칙."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_document_id(source_type: str, source_name: str,
                     source_document_id: str, normalized_url: str) -> str:
    """data_dictionary.md §5 의 document_id 생성 규칙."""
    if source_document_id:
        seed = f"{source_name}|{source_document_id}"
    else:
        seed = normalized_url
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"{source_type}-{digest}"


def parse_published_date(raw: str) -> str | None:
    """발행일을 YYYY-MM-DD 로 바꾼다. 확인 불가면 None.

    과기정통부 RSS 는 pubDate 를 `2026.10.15` 로 내려준다 (RFC 822 아님).
    """
    if not raw:
        return None
    text = raw.strip()
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


HWP_JSON_RE = re.compile(r"<!--\s*\[data-hwpjson\].*?-->", re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
CONTACT_RE = re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")


def clean_text(raw: str) -> str:
    """본문에서 분석에 쓸 텍스트만 남긴다.

    과기정통부 RSS 의 `content:encoded` 에는 한글(HWP) 프로그램이 남긴
    `<!--[data-hwpjson]{...}-->` 블록이 섞여 들어온다.

    주의: 이 블록은 피드에서 `&lt;!--[data-hwpjson]` 처럼 이스케이프된 상태로
    온다. 태그를 먼저 지우고 나중에 이스케이프를 풀면 블록이 되살아나므로,
    **이스케이프를 먼저 풀고** 제거해야 한다. 엔티티가 두 겹으로 싸여 있는
    경우가 있어 더 이상 바뀌지 않을 때까지 되돌린다.

    전화번호 형태(담당자 연락처)는 기획안 §11 에 따라 지운다.
    """
    text = raw
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    # 닫는 --> 가 있는 경우
    text = HWP_JSON_RE.sub(" ", text)
    # 닫는 --> 없이 잘려 온 경우: 시작 지점부터 끝까지 버린다
    cut = text.find("<!--[data-hwpjson]")
    if cut != -1:
        text = text[:cut]

    text = COMMENT_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = CONTACT_RE.sub(" ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_source_document_id(url: str) -> str:
    """출처가 제공하는 원문 식별자를 URL 에서 꺼낸다.

    과기정통부는 `nttSeqNo` 를 쓴다. 없으면 빈 문자열.
    """
    query = dict(parse_qsl(urlsplit(url).query))
    for key in ("nttSeqNo", "newsId", "list_no", "id"):
        if query.get(key):
            return f"{key}={query[key]}"
    return ""


# ──────────────────────────── 출처 목록 ────────────────────────────

def load_sources(source_id: str | None = None) -> list[dict]:
    with SOURCES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    selected = []
    for row in rows:
        if row.get("source_type") != "policy":
            continue
        if source_id and row.get("source_id") != source_id:
            continue
        if row.get("access_check_status") not in COLLECTABLE_STATUS:
            continue
        selected.append(row)
    return selected


def load_existing_documents() -> list[dict]:
    if not DOCUMENTS_CSV.exists():
        return []
    with DOCUMENTS_CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ──────────────────────────── 수집 ────────────────────────────

def fetch(url: str, delay: float, timeout: int = 20) -> tuple[str | None, str | None]:
    """한 번만 요청한다. 실패 시 재시도하지 않는다 (기획안 §11)."""
    time.sleep(delay)
    try:
        res = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        res.raise_for_status()
        res.encoding = res.encoding or "utf-8"
        return res.text, None
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: {exc}"


def collect_rss(source: dict, window: tuple[str, str], delay: float,
                limit: int | None) -> tuple[list[dict], dict]:
    """RSS 출처 한 곳을 수집한다. (문서 목록, 실행 기록) 을 돌려준다."""
    source_name = source["source_name"]
    list_url = source["list_url"]
    collect_from, collect_to = window

    body, error = fetch(list_url, delay)
    log = {
        "source_id": source["source_id"],
        "source_name": source_name,
        "list_url": list_url,
        "access_method": "rss",
        "fetched": body is not None,
        "error": error,
        "entries_seen": 0,
        "documents": 0,
        "excluded": {},
    }
    if body is None:
        log["note"] = "요청 실패. 재시도하지 않았다"
        return [], log

    feed = feedparser.parse(body)
    entries = feed.entries[:limit] if limit else feed.entries
    log["entries_seen"] = len(entries)
    log["entries_in_feed"] = len(feed.entries)

    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    documents = []

    for entry in entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not link:
            continue

        normalized = normalize_url(link)
        sdid = extract_source_document_id(link)
        doc_id = make_document_id("policy", source_name, sdid, normalized)

        published = parse_published_date(entry.get("published") or entry.get("pubDate") or "")

        raw_body = ""
        if entry.get("content"):
            raw_body = entry["content"][0].get("value", "")
        raw_body = raw_body or entry.get("summary", "")
        text = clean_text(raw_body)

        scope = "title_body" if text else "title"
        analyzed_text = f"{title}\n\n{text}" if text else title

        row = {
            "document_id": doc_id,
            "source_type": "policy",
            "source_name": source_name,
            "source_document_id": sdid,
            "title": title,
            "original_url": link,
            "published_date": published or "",
            "collected_at": collected_at,
            "analysis_scope": scope,
            "content_hash": content_hash(analyzed_text),
            "status": "analyzed",
            "exclusion_reason": "",
            "duplicate_of": "",
            "_normalized_url": normalized,
            "_text": analyzed_text,
        }

        if not title:
            row["status"] = "excluded"
            row["exclusion_reason"] = "no_text"
        elif published is None:
            row["status"] = "excluded"
            row["exclusion_reason"] = "no_published_date"
        elif not (collect_from <= published <= collect_to):
            row["status"] = "excluded"
            row["exclusion_reason"] = "out_of_period"

        documents.append(row)

    for row in documents:
        if row["exclusion_reason"]:
            log["excluded"][row["exclusion_reason"]] = log["excluded"].get(row["exclusion_reason"], 0) + 1
    log["documents"] = len(documents)
    return documents, log


# ──────────────────────────── 중복 제거 ────────────────────────────

def dedupe(documents: list[dict], existing: list[dict]) -> dict:
    """data_dictionary.md §5 의 중복 제거 순서를 적용한다.

    1) 같은 출처 안에서 source_document_id 일치
    2) 정규화한 URL 일치
    3) content_hash 일치
    """
    seen_sdid: dict[tuple[str, str], str] = {}
    seen_url: dict[str, str] = {}
    seen_hash: dict[str, str] = {}

    for row in existing:
        if row.get("status") != "analyzed":
            continue
        did = row["document_id"]
        if row.get("source_document_id"):
            seen_sdid[(row["source_name"], row["source_document_id"])] = did
        seen_url[normalize_url(row["original_url"])] = did
        seen_hash[row["content_hash"]] = did

    counts = {"duplicate": 0}
    for row in documents:
        if row["status"] != "analyzed":
            continue
        rep = None
        key = (row["source_name"], row["source_document_id"])
        if row["source_document_id"] and key in seen_sdid:
            rep = seen_sdid[key]
        elif row["_normalized_url"] in seen_url:
            rep = seen_url[row["_normalized_url"]]
        elif row["content_hash"] in seen_hash:
            rep = seen_hash[row["content_hash"]]

        if rep and rep != row["document_id"]:
            row["status"] = "excluded"
            row["exclusion_reason"] = "duplicate"
            row["duplicate_of"] = rep
            counts["duplicate"] += 1
            continue

        if row["source_document_id"]:
            seen_sdid[key] = row["document_id"]
        seen_url[row["_normalized_url"]] = row["document_id"]
        seen_hash[row["content_hash"]] = row["document_id"]

    return counts


# ──────────────────────────── 저장 ────────────────────────────

def save(documents: list[dict], existing: list[dict], logs: list[dict]) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    raw_written = 0
    for row in documents:
        if row["status"] != "analyzed":
            continue
        (RAW_DIR / f"{row['document_id']}.txt").write_text(row["_text"], encoding="utf-8")
        raw_written += 1

    existing_ids = {r["document_id"] for r in existing}
    merged = list(existing)
    added = 0
    for row in documents:
        if row["document_id"] in existing_ids:
            continue
        merged.append({k: row.get(k, "") for k in DOCUMENT_FIELDS})
        existing_ids.add(row["document_id"])
        added += 1

    with DOCUMENTS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DOCUMENT_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in merged:
            writer.writerow({k: row.get(k, "") for k in DOCUMENT_FIELDS})

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    log_path = PROCESSED_DIR / f"collect_log_{stamp}.json"
    log_path.write_text(json.dumps({
        "run_at": datetime.now(KST).isoformat(timespec="seconds"),
        "sources": logs,
        "documents_total": len(merged),
        "documents_added": added,
        "raw_files_written": raw_written,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"added": added, "total": len(merged), "raw": raw_written, "log": log_path.name}


# ──────────────────────────── 실행 ────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # 윈도 콘솔 기본 인코딩(cp949)에서 한글이 깨지지 않도록 고정한다
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="정책 자료 수집기 (B 담당)")
    ap.add_argument("--source-id", help="특정 출처만 수집 (예: policy-kr-001)")
    ap.add_argument("--from", dest="date_from", help="수집 시작일 YYYY-MM-DD. 기본값은 sources.csv")
    ap.add_argument("--to", dest="date_to", help="수집 종료일 YYYY-MM-DD. 기본값은 sources.csv")
    ap.add_argument("--limit", type=int, help="출처당 처리할 항목 수 상한")
    ap.add_argument("--delay", type=float, default=1.0, help="요청 간 대기 시간(초). 기본 1.0")
    ap.add_argument("--dry-run", action="store_true", help="수집 대상만 보여주고 요청하지 않는다")
    args = ap.parse_args(argv)

    sources = load_sources(args.source_id)
    if not sources:
        print("수집 가능한 정책 출처가 없습니다.", file=sys.stderr)
        print("config/sources.csv 의 access_check_status 가 terms_ok 또는 "
              "terms_ok_local 인 행이 있어야 합니다.", file=sys.stderr)
        return 1

    print(f"수집 대상 {len(sources)}곳")
    runnable, skipped = [], []
    for s in sources:
        method = s.get("access_method") or "(미지정)"
        mark = "실행" if method == "rss" else "건너뜀"
        if method == "rss":
            runnable.append(s)
        else:
            skipped.append(s)
        print(f"  [{mark}] {s['source_id']} {s['source_name']} "
              f"({method}, {s['access_check_status']}, {s['redistribution']})")

    if skipped:
        print(f"\n건너뛴 {len(skipped)}곳은 access_method 가 rss 가 아닙니다. "
              f"html_list 수집기는 이슈 #37 입니다.")

    if args.dry_run:
        print("\n--dry-run 이므로 요청하지 않았습니다.")
        return 0
    if not runnable:
        print("\n실행할 수 있는 출처가 없습니다.", file=sys.stderr)
        return 1

    existing = load_existing_documents()
    print(f"\n기존 documents.csv: {len(existing)}건")

    all_docs, logs = [], []
    for s in runnable:
        window = (args.date_from or s.get("collect_from") or "0001-01-01",
                  args.date_to or s.get("collect_to") or "9999-12-31")
        print(f"\n[{s['source_id']}] {s['source_name']}  기간 {window[0]} ~ {window[1]}")
        docs, log = collect_rss(s, window, args.delay, args.limit)
        if log["error"]:
            print(f"  요청 실패: {log['error']}")
        else:
            print(f"  피드 {log.get('entries_in_feed', 0)}건 중 {log['entries_seen']}건 처리")
            if log["excluded"]:
                print(f"  제외: {log['excluded']}")
        all_docs.extend(docs)
        logs.append(log)

    dup = dedupe(all_docs, existing)
    if dup["duplicate"]:
        print(f"\n중복 제외 {dup['duplicate']}건")

    result = save(all_docs, existing, logs)
    analyzed = sum(1 for r in all_docs if r["status"] == "analyzed")
    print(f"\n분석 대상 {analyzed}건 / 새로 추가 {result['added']}건 "
          f"/ documents.csv 총 {result['total']}건")
    print(f"원문 저장 {result['raw']}건 -> data/raw/")
    print(f"실행 기록 -> data/processed/{result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
