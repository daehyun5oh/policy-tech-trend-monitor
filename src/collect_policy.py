"""정책 자료 수집기 (이슈 #8, 담당 B)

`config/sources.csv` 에서 수집이 허용된 정책 출처를 읽어 원문을 가져오고,
`docs/data_dictionary.md` 규격의 `documents.csv` 와 실행 기록을 만든다.

정제·식별자·중복 제거 규칙은 `src/normalize.py` 에 있다 (이슈 #9).
이 파일은 "어디서 어떻게 가져오는가"만 담당한다.

허용된 경로만 쓴다. `access_check_status` 가 `terms_ok` 또는 `terms_ok_local`
인 출처만 수집한다 (기획안 §11).

이 파일은 `rss` 경로를 맡는다. `html_list`(미국·중국 5곳)는 사이트마다 구조가
달라 `src/collect_html.py` 로 분리했다(이슈 #37). 두 수집기는 같은 정제 모듈
(`src/normalize.py`)과 같은 `documents.csv` 규격을 쓰고, 서로의 행을 지우지 않는다.

사용법
    python src/collect_policy.py --dry-run
    python src/collect_policy.py
    python src/collect_policy.py --source-id policy-kr-001 --limit 5
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize as nz  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCES_CSV = ROOT / "config" / "sources.csv"
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"

KST = timezone(timedelta(hours=9))
COLLECTABLE_STATUS = {"terms_ok", "terms_ok_local"}
USER_AGENT = "policy-tech-trend-monitor/0.1 (research prototype)"


# ──────────────────────────── 출처 목록 ────────────────────────────

def load_sources(source_id: str | None = None) -> list[dict]:
    """수집이 허용된 정책 출처만 고른다."""
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

    body, error = fetch(list_url, delay)
    log = {
        "source_id": source["source_id"],
        "source_name": source_name,
        "list_url": list_url,
        "access_method": "rss",
        "redistribution": source.get("redistribution", ""),
        "window": list(window),
        "fetched": body is not None,
        "error": error,
        "entries_in_feed": 0,
        "entries_seen": 0,
        "documents": 0,
        "excluded": {},
    }
    if body is None:
        log["note"] = "요청 실패. 재시도하지 않았다"
        return [], log

    feed = feedparser.parse(body)
    entries = feed.entries[:limit] if limit else feed.entries
    log["entries_in_feed"] = len(feed.entries)
    log["entries_seen"] = len(entries)

    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    documents = []

    for entry in entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue

        raw_body = ""
        if entry.get("content"):
            raw_body = entry["content"][0].get("value", "")
        raw_body = raw_body or entry.get("summary", "")

        documents.append(nz.build_document(
            source_type="policy",
            source_name=source_name,
            title=entry.get("title") or "",
            original_url=link,
            raw_body=raw_body,
            published_raw=entry.get("published") or entry.get("pubDate") or "",
            collected_at=collected_at,
            window=window,
        ))

    for row in documents:
        reason = row["exclusion_reason"]
        if reason:
            log["excluded"][reason] = log["excluded"].get(reason, 0) + 1
    log["documents"] = len(documents)
    return documents, log


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

    merged, added = nz.merge_documents(existing, documents)
    nz.write_documents(DOCUMENTS_CSV, merged)

    problems = nz.validate_documents(merged)

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    log_path = PROCESSED_DIR / f"collect_log_{stamp}.json"
    log_path.write_text(json.dumps({
        "run_at": datetime.now(KST).isoformat(timespec="seconds"),
        "sources": logs,
        "documents_total": len(merged),
        "documents_added": added,
        "raw_files_written": raw_written,
        "validation_problems": problems,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"added": added, "total": len(merged), "raw": raw_written,
            "log": log_path.name, "problems": problems}


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
    ap.add_argument("--data-dir", help="raw/ 와 processed/ 를 둘 폴더. 기본은 저장소의 data/")
    args = ap.parse_args(argv)
    if args.data_dir:  # 다른 폴더에 쓰고 읽는다(monitor.py 의 작업 폴더). 기본은 저장소의 data/
        global RAW_DIR, PROCESSED_DIR, DOCUMENTS_CSV
        RAW_DIR = Path(args.data_dir) / "raw"
        PROCESSED_DIR = Path(args.data_dir) / "processed"
        DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"

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
        if method == "rss":
            runnable.append(s)
            mark = "실행"
        else:
            skipped.append(s)
            mark = "건너뜀"
        print(f"  [{mark}] {s['source_id']} {s['source_name']} "
              f"({method}, {s['access_check_status']}, {s['redistribution']})")

    if skipped:
        print(f"\n건너뛴 {len(skipped)}곳은 access_method 가 rss 가 아닙니다. "
              f"html_list 출처는 src/collect_html.py 로 수집합니다.")

    if args.dry_run:
        print("\n--dry-run 이므로 요청하지 않았습니다.")
        return 0
    if not runnable:
        print("\n실행할 수 있는 출처가 없습니다.", file=sys.stderr)
        return 1

    existing = nz.read_documents(DOCUMENTS_CSV)
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
            print(f"  피드 {log['entries_in_feed']}건 중 {log['entries_seen']}건 처리")
            if log["excluded"]:
                print(f"  제외: {log['excluded']}")
        all_docs.extend(docs)
        logs.append(log)

    dup = nz.dedupe(all_docs, existing)
    if dup["duplicate"]:
        print(f"\n중복 제외 {dup['duplicate']}건")

    result = save(all_docs, existing, logs)
    analyzed = sum(1 for r in all_docs if r["status"] == "analyzed")
    print(f"\n분석 대상 {analyzed}건 / 새로 추가 {result['added']}건 "
          f"/ documents.csv 총 {result['total']}건")
    print(f"원문 저장 {result['raw']}건 -> data/raw/")
    print(f"실행 기록 -> data/processed/{result['log']}")

    if result["problems"]:
        print(f"\n규격 검증에서 문제 {len(result['problems'])}건 (완료기준 C2):")
        for p in result["problems"][:10]:
            print(f"  - {p}")
        if len(result["problems"]) > 10:
            print(f"  ... 그 밖 {len(result['problems']) - 10}건. 실행 기록 참조")
        return 2

    print("규격 검증 통과 (완료기준 C2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
