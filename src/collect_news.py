#!/usr/bin/env python3
"""뉴스 수집기 (이슈 #7).

허용된 경로만 쓴다 (docs/source_policy.md 의 긱뉴스 항목 참조).
  rss  : 긱뉴스가 공개한 Atom 피드(https://news.hada.io/rss/news)를 1회 요청한다.
         요청은 1회뿐이고 실패해도 재시도하지 않는다. 로그인·API·페이지 크롤링은 하지 않는다.
  csv  : 이미 수동으로 모은 목록(date,title,url)을 읽는다. 네트워크를 쓰지 않는다.

출력 (--out-dir, 기본 data/processed):
  documents.csv      공통 규격(src/schema.py)의 수집 문서 목록
  texts/<id>.txt     피드가 준 요약 텍스트 (analysis_scope=summary 인 문서만, 재배포 금지라 .gitignore 대상)
  collect_log.json   성공·제외·실패 건수와 사유

사용 예:
  python src/collect_news.py rss --from 2026-10-01 --to 2026-10-31
  python src/collect_news.py csv data/sample/geeknews_2026-09_input.csv --from 2026-09-01 --to 2026-09-30

종료 코드: 0 정상 / 1 수집 실패(피드를 받지 못함 등) / 2 입력 오류
"""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import DOCUMENT_COLUMNS, STATUS_ANALYZED, STATUS_EXCLUDED  # noqa: E402

SOURCE_NAME = "GeekNews"
FEED_URL = "https://news.hada.io/rss/news"
USER_AGENT = "policy-tech-trend-monitor/0.1 (graduate course project; manual run)"
TIMEOUT_SECONDS = 20
ATOM = "{http://www.w3.org/2005/Atom}"
KST = dt.timezone(dt.timedelta(hours=9))
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def normalize_url(url):
    """docs/data_dictionary.md §5 의 URL 정규화 규칙."""
    parts = urlsplit(url.strip())
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = "%s:%d" % (host, port)
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if k not in TRACKING_PARAMS)
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(text):
    return sha256(re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip())


def make_document_id(source_document_id, url):
    if source_document_id:
        return "news-" + sha256(SOURCE_NAME + "|" + source_document_id)[:12]
    return "news-" + sha256(normalize_url(url))[:12]


def html_to_text(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(value):
    """'2026-09-01' 또는 ISO 8601 문자열에서 한국 표준시 기준 날짜를 얻는다. 못 읽으면 None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            return dt.date.fromisoformat(value)
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(KST)
    return stamp.date()


def parse_atom(xml_bytes):
    """Atom 피드를 [{title, url, source_document_id, published, text}] 로 바꾼다."""
    root = ET.fromstring(xml_bytes)
    items = []
    for entry in root.iter(ATOM + "entry"):
        link = ""
        for el in entry.findall(ATOM + "link"):
            if el.get("rel", "alternate") == "alternate":
                link = el.get("href", "")
                break
        entry_id = (entry.findtext(ATOM + "id") or "").strip()
        m = re.search(r"[?&]id=(\d+)", entry_id)
        items.append({
            "title": (entry.findtext(ATOM + "title") or "").strip(),
            "url": link or entry_id,
            "source_document_id": m.group(1) if m else "",
            "published": entry.findtext(ATOM + "published") or entry.findtext(ATOM + "updated") or "",
            "text": html_to_text(entry.findtext(ATOM + "content")),
        })
    return items


def fetch_feed(url=FEED_URL):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read()


def read_input_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{"title": (r.get("title") or "").strip(), "url": (r.get("url") or "").strip(),
                 "source_document_id": "", "published": (r.get("date") or "").strip(), "text": ""}
                for r in csv.DictReader(f)]


def build_documents(items, collected_at, date_from=None, date_to=None):
    """항목을 documents.csv 행으로 바꾼다. 반환: (행 목록, {document_id: 요약 텍스트})."""
    rows, texts = [], {}
    first_by_key = {}
    for item in items:
        title, url = item["title"], item["url"]
        published = parse_date(item["published"])
        text = item["text"]
        scope = "summary" if text else "title"
        row = dict.fromkeys(DOCUMENT_COLUMNS, "")
        row.update({
            "source_type": "news", "source_name": SOURCE_NAME,
            "source_document_id": item["source_document_id"], "title": title,
            "original_url": url, "published_date": published.isoformat() if published else "",
            "collected_at": collected_at, "analysis_scope": scope,
        })
        if not title or not re.match(r"https?://", url):
            row.update(document_id=make_document_id(item["source_document_id"], url or title),
                       status=STATUS_EXCLUDED, exclusion_reason="no_text")
            row["content_hash"] = content_hash(title)
            rows.append(row)
            continue
        row["document_id"] = make_document_id(item["source_document_id"], url)
        row["content_hash"] = content_hash(title + "\n" + text if text else title)
        if published is None:
            row.update(status=STATUS_EXCLUDED, exclusion_reason="no_published_date")
        elif (date_from and published < date_from) or (date_to and published > date_to):
            row.update(status=STATUS_EXCLUDED, exclusion_reason="out_of_period")
        else:
            row["status"] = STATUS_ANALYZED
            # 중복 제거 순서: 출처 원문 ID -> 정규화 URL -> content_hash. 먼저 수집한 문서가 대표.
            keys = [("sid", item["source_document_id"]), ("url", normalize_url(url)),
                    ("hash", row["content_hash"])]
            rep = next((first_by_key[k] for k in keys if k[1] and k in first_by_key), None)
            if rep:
                row.update(status=STATUS_EXCLUDED, exclusion_reason="duplicate", duplicate_of=rep)
            else:
                for k in keys:
                    if k[1]:
                        first_by_key[k] = row["document_id"]
                if scope == "summary":
                    texts[row["document_id"]] = text
        rows.append(row)
    return rows, texts


def write_outputs(out_dir, rows, texts, log):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "documents.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DOCUMENT_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    if texts:
        (out_dir / "texts").mkdir(exist_ok=True)
        for doc_id, text in texts.items():
            (out_dir / "texts" / (doc_id + ".txt")).write_text(text, encoding="utf-8")
    (out_dir / "collect_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")


def summarize(rows):
    counts = {"analyzed": 0, "excluded": 0, "failed": 0}
    reasons = {}
    for r in rows:
        counts[r["status"]] += 1
        if r["exclusion_reason"]:
            reasons[r["exclusion_reason"]] = reasons.get(r["exclusion_reason"], 0) + 1
    return counts, reasons


def main(argv=None):
    p = argparse.ArgumentParser(description="긱뉴스 피드 또는 수동 수집 CSV에서 뉴스 documents.csv를 만든다.")
    p.add_argument("mode", choices=["rss", "csv"])
    p.add_argument("input", nargs="?", help="csv 모드의 입력 파일 (date,title,url)")
    p.add_argument("--from", dest="date_from", help="수집 시작일 YYYY-MM-DD (포함)")
    p.add_argument("--to", dest="date_to", help="수집 종료일 YYYY-MM-DD (포함)")
    p.add_argument("--out-dir", default="data/processed")
    p.add_argument("--feed-url", default=FEED_URL)
    args = p.parse_args(argv)

    date_from, date_to = parse_date(args.date_from), parse_date(args.date_to)
    if (args.date_from and not date_from) or (args.date_to and not date_to):
        print("--from/--to는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2
    collected_at = dt.datetime.now(KST).replace(microsecond=0).isoformat()
    log = {"source": SOURCE_NAME, "mode": args.mode, "collected_at": collected_at,
           "period": [args.date_from or "", args.date_to or ""]}

    try:
        if args.mode == "csv":
            if not args.input:
                print("csv 모드에는 입력 파일이 필요합니다.", file=sys.stderr)
                return 2
            items = read_input_csv(args.input)
            log["input"] = str(args.input)
        else:
            items = parse_atom(fetch_feed(args.feed_url))
            log["input"] = args.feed_url
    except OSError as e:  # 네트워크·파일 오류. 재시도하지 않는다.
        reason = "fetch_error" if args.mode == "rss" else "input_error"
        log.update(result="failed", failure_reason=reason, detail=str(e))
        write_outputs(Path(args.out_dir), [], {}, log)
        print("수집 실패 (%s): %s" % (reason, e), file=sys.stderr)
        return 1 if args.mode == "rss" else 2
    except ET.ParseError as e:
        log.update(result="failed", failure_reason="parse_error", detail=str(e))
        write_outputs(Path(args.out_dir), [], {}, log)
        print("수집 실패 (parse_error): %s" % e, file=sys.stderr)
        return 1

    rows, texts = build_documents(items, collected_at, date_from, date_to)
    counts, reasons = summarize(rows)
    log.update(result="ok", total=len(rows), status_counts=counts, exclusion_reasons=reasons)
    write_outputs(Path(args.out_dir), rows, texts, log)
    print("수집 완료: 전체 %d건 / 분석 %d / 제외 %d %s -> %s" % (
        len(rows), counts["analyzed"], counts["excluded"], reasons or "", Path(args.out_dir) / "documents.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
