#!/usr/bin/env python3
"""뉴스 수집기 (이슈 #7).

허용된 경로만 쓴다 (docs/source_policy.md 의 뉴스 출처 항목 참조).
  rss  : config/sources.csv 에서 source_type=news, access_method=rss, 상태 terms_ok 인 출처의
         공개 피드(Atom 또는 RSS 2.0)를 출처마다 1회씩 요청한다. 실패해도 재시도하지 않고,
         출처 사이에 간격을 둔다. 로그인·API·페이지 크롤링은 하지 않는다.
  csv  : 이미 수동으로 모은 목록(date,title,url)을 읽는다 (긱뉴스 샘플). 네트워크를 쓰지 않는다.

누적 (rss 모드): 피드는 최근 기사만 주므로 한 번 수집으로는 기간을 채울 수 없다. 그래서 수집할 때마다
  받은 항목을 --raw-dir(기본 data/raw)의 news_items.csv 에 쌓고(이미 본 항목은 처음 수집한 시각을 유지),
  documents.csv 는 그 누적본 전체로 다시 만든다. 이렇게 며칠 모으면 일간·주간 추이를 볼 수 있다.
  --no-accumulate 를 주면 이번에 받은 항목만 쓴다. csv 모드는 누적하지 않는다.
  documents.csv 에 뉴스가 아닌 문서(정책 등)가 있으면 그대로 보존한다.

출력 (--out-dir, 기본 data/processed):
  documents.csv      공통 규격(src/schema.py)의 수집 문서 목록 (출처 전체를 한 파일에 모음)
  texts/<id>.txt     피드가 준 요약·본문 텍스트 (analysis_scope 가 title_summary/title_body 인 문서만,
                     재배포 제한이 있어 .gitignore 대상)
  collect_log.json   출처별 성공·제외·실패 건수와 사유

사용 예:
  python src/collect_news.py rss --from 2026-10-01 --to 2026-10-31
  python src/collect_news.py rss --sources news-002,news-004
  python src/collect_news.py csv data/sample/geeknews_2026-09_input.csv --from 2026-09-01 --to 2026-09-30

종료 코드: 0 정상 / 1 수집 실패(피드를 받지 못한 출처가 있음) / 2 입력 오류
"""
import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import DOCUMENT_COLUMNS, DOCUMENT_OPTIONAL_COLUMNS, STATUS_ANALYZED, STATUS_EXCLUDED  # noqa: E402

ALL_COLUMNS = DOCUMENT_COLUMNS + DOCUMENT_OPTIONAL_COLUMNS

CSV_SOURCE_NAME = "GeekNews"
USER_AGENT = "policy-tech-trend-monitor/0.1 (graduate course project; manual run)"
TIMEOUT_SECONDS = 20
SOURCE_GAP_SECONDS = 2
ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"
KST = dt.timezone(dt.timedelta(hours=9))
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
DEFAULT_SOURCES_FILE = Path(__file__).resolve().parent.parent / "config" / "sources.csv"


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


def make_document_id(source_name, source_document_id, url):
    if source_document_id:
        return "news-" + sha256(source_name + "|" + source_document_id)[:12]
    return "news-" + sha256(normalize_url(url))[:12]


def html_to_text(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(value):
    """'2026-09-01', ISO 8601, RFC 822('Sat, 17 Oct 2026 10:49:18 +0900'),
    시간대 없는 '2026-10-15 18:03:54'(한국 표준시로 간주)에서 한국 표준시 기준 날짜를 얻는다.
    못 읽으면 None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            return dt.date.fromisoformat(value)
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            stamp = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(KST)
    return stamp.date()


def _atom_items(root):
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
            "full_text": False,
        })
    return items


def _rss_items(root):
    items = []
    for item in root.iter("item"):
        description = html_to_text(item.findtext("description"))
        body = html_to_text(item.findtext(CONTENT_ENCODED))
        # content:encoded 가 description 보다 확실히 길면 본문을 준 것으로 본다.
        full = len(body) > max(len(description) * 1.5, 400)
        items.append({
            "title": html_to_text(item.findtext("title")),
            "url": (item.findtext("link") or "").strip(),
            "source_document_id": "",
            "published": (item.findtext("pubDate") or "").strip(),
            "text": body if full else description,
            "full_text": full,
        })
    return items


def parse_feed(xml_bytes):
    """Atom 또는 RSS 2.0 피드를 [{title, url, source_document_id, published, text, full_text}] 로 바꾼다."""
    root = ET.fromstring(xml_bytes)
    return _atom_items(root) if root.tag == ATOM + "feed" else _rss_items(root)


def parse_atom(xml_bytes):  # 이전 이름 호환
    return parse_feed(xml_bytes)


def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read()


def read_input_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{"title": (r.get("title") or "").strip(), "url": (r.get("url") or "").strip(),
                 "source_document_id": "", "published": (r.get("date") or "").strip(), "text": "",
                 "full_text": False}
                for r in csv.DictReader(f)]


def load_news_sources(path, wanted=None):
    """sources.csv 에서 수집 가능한 뉴스 RSS 출처를 읽는다. wanted 가 있으면 그 ID만."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    chosen = [r for r in rows if r["source_type"] == "news" and r["access_method"] == "rss"
              and r["access_check_status"] == "terms_ok" and r["list_url"]]
    if wanted:
        missing = set(wanted) - {r["source_id"] for r in chosen}
        if missing:
            raise ValueError("수집 가능한 뉴스 RSS 출처가 아님: " + ", ".join(sorted(missing)))
        chosen = [r for r in chosen if r["source_id"] in wanted]
    return chosen


RAW_COLUMNS = ["source_name", "source_document_id", "title", "url", "published", "text", "full_text", "first_collected_at"]


def item_key(item):
    name = item.get("source_name") or CSV_SOURCE_NAME
    if item.get("source_document_id"):
        return (name, "sid", item["source_document_id"])
    return (name, "url", normalize_url(item["url"]) if item.get("url") else item.get("title", ""))


def load_raw_items(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [{"source_name": r["source_name"], "source_document_id": r["source_document_id"], "title": r["title"],
                 "url": r["url"], "published": r["published"], "text": r["text"], "full_text": r["full_text"] == "1",
                 "collected_at": r["first_collected_at"]} for r in csv.DictReader(f)]


def merge_raw_items(old, new, collected_at):
    """이미 있는 항목은 그대로 두고(처음 수집한 시각 유지) 새 항목만 뒤에 붙인다. 반환: (전체, 새로 늘어난 수)."""
    seen = {item_key(i) for i in old}
    merged, added = list(old), 0
    for it in new:
        k = item_key(it)
        if k in seen:
            continue
        seen.add(k)
        merged.append(dict(it, collected_at=collected_at))
        added += 1
    return merged, added


def save_raw_items(path, items):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RAW_COLUMNS, lineterminator="\n")
        w.writeheader()
        for i in items:
            w.writerow({"source_name": i.get("source_name") or CSV_SOURCE_NAME,
                        "source_document_id": i.get("source_document_id", ""), "title": i["title"], "url": i["url"],
                        "published": i["published"], "text": i["text"], "full_text": "1" if i.get("full_text") else "0",
                        "first_collected_at": i["collected_at"]})


def build_documents(items, collected_at, date_from=None, date_to=None, source_name=CSV_SOURCE_NAME):
    """항목을 documents.csv 행으로 바꾼다. 항목에 source_name 이 있으면 그것을 쓴다.
    반환: (행 목록, {document_id: 요약·본문 텍스트})."""
    rows, texts = [], {}
    first_by_key = {}
    for item in items:
        name = item.get("source_name") or source_name
        title, url = item["title"], item["url"]
        published = parse_date(item["published"])
        text = item["text"]
        scope = ("title_body" if item.get("full_text") else "title_summary") if text else "title"
        row = dict.fromkeys(ALL_COLUMNS, "")
        row.update({
            "source_type": "news", "source_name": name,
            "source_document_id": item["source_document_id"], "title": title,
            "original_url": url, "published_date": published.isoformat() if published else "",
            "collected_at": item.get("collected_at") or collected_at, "analysis_scope": scope,
        })
        if not title or not re.match(r"https?://", url):
            row.update(document_id=make_document_id(name, item["source_document_id"], url or title),
                       status=STATUS_EXCLUDED, exclusion_reason="no_text")
            row["content_hash"] = content_hash(title)
            rows.append(row)
            continue
        row["document_id"] = make_document_id(name, item["source_document_id"], url)
        row["content_hash"] = content_hash(title + "\n" + text if text else title)
        if published is None:
            row.update(status=STATUS_EXCLUDED, exclusion_reason="no_published_date")
        elif (date_from and published < date_from) or (date_to and published > date_to):
            row.update(status=STATUS_EXCLUDED, exclusion_reason="out_of_period")
        else:
            row["status"] = STATUS_ANALYZED
            row["text_chars"] = str(len(text)) if scope != "title" else "0"
            # 중복 제거 순서: 같은 출처의 원문 ID -> 정규화 URL -> content_hash. 먼저 수집한 문서가 대표.
            keys = [("sid", name, item["source_document_id"]), ("url", normalize_url(url)),
                    ("hash", row["content_hash"])]
            rep = next((first_by_key[k] for k in keys if k[-1] and k in first_by_key), None)
            if rep:
                row.update(status=STATUS_EXCLUDED, exclusion_reason="duplicate", duplicate_of=rep, text_chars="")
            else:
                for k in keys:
                    if k[-1]:
                        first_by_key[k] = row["document_id"]
                if scope != "title":
                    texts[row["document_id"]] = text
        rows.append(row)
    return rows, texts


def read_other_rows(path):
    """기존 documents.csv 에서 뉴스가 아닌 행(정책 등)을 읽는다. 다른 담당자의 결과를 지우지 않기 위해서다."""
    if not Path(path).exists():
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [r for r in csv.DictReader(f) if r.get("source_type") != "news"]
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def write_outputs(out_dir, rows, texts, log):
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = list(rows) + [{k: (r.get(k) or "") for k in ALL_COLUMNS} for r in read_other_rows(out_dir / "documents.csv")]
    with open(out_dir / "documents.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ALL_COLUMNS, lineterminator="\n")
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


def collect_rss(sources, fetch=fetch_feed, gap=SOURCE_GAP_SECONDS):
    """출처마다 피드를 1회 요청한다. 반환: (항목 목록, 출처별 로그). 한 출처가 실패해도 나머지는 계속한다."""
    items, per_source = [], []
    for i, src in enumerate(sources):
        if i and gap:
            time.sleep(gap)
        entry = {"source_id": src["source_id"], "source_name": src["source_name"], "url": src["list_url"]}
        try:
            got = parse_feed(fetch(src["list_url"]))
        except ET.ParseError as e:
            entry.update(result="failed", failure_reason="parse_error", detail=str(e))
        except OSError as e:  # 네트워크 오류. 재시도하지 않는다.
            entry.update(result="failed", failure_reason="fetch_error", detail=str(e))
        else:
            for it in got:
                it["source_name"] = src["source_name"]
            items.extend(got)
            entry.update(result="ok", fetched=len(got))
        per_source.append(entry)
    return items, per_source


def main(argv=None):
    p = argparse.ArgumentParser(description="뉴스 피드 또는 수동 수집 CSV에서 뉴스 documents.csv를 만든다.")
    p.add_argument("mode", choices=["rss", "csv"])
    p.add_argument("input", nargs="?", help="csv 모드의 입력 파일 (date,title,url)")
    p.add_argument("--from", dest="date_from", help="수집 시작일 YYYY-MM-DD (포함)")
    p.add_argument("--to", dest="date_to", help="수집 종료일 YYYY-MM-DD (포함)")
    p.add_argument("--out-dir", default="data/processed")
    p.add_argument("--raw-dir", default="data/raw", help="rss 모드에서 받은 항목을 누적하는 폴더 (기본 data/raw, Git 제외)")
    p.add_argument("--no-accumulate", action="store_true", help="누적하지 않고 이번에 받은 항목만 사용")
    p.add_argument("--sources-file", default=str(DEFAULT_SOURCES_FILE))
    p.add_argument("--sources", help="rss 모드에서 수집할 source_id 목록(쉼표 구분). 기본은 수집 가능한 전체")
    args = p.parse_args(argv)

    date_from, date_to = parse_date(args.date_from), parse_date(args.date_to)
    if (args.date_from and not date_from) or (args.date_to and not date_to):
        print("--from/--to는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2
    collected_at = dt.datetime.now(KST).replace(microsecond=0).isoformat()
    log = {"mode": args.mode, "collected_at": collected_at, "period": [args.date_from or "", args.date_to or ""]}
    out_dir = Path(args.out_dir)
    failed = False

    if args.mode == "csv":
        if not args.input:
            print("csv 모드에는 입력 파일이 필요합니다.", file=sys.stderr)
            return 2
        try:
            items = read_input_csv(args.input)
        except OSError as e:
            log.update(result="failed", failure_reason="input_error", detail=str(e))
            write_outputs(out_dir, [], {}, log)
            print("수집 실패 (input_error): %s" % e, file=sys.stderr)
            return 2
        log["sources"] = [{"source_name": CSV_SOURCE_NAME, "result": "ok", "input": str(args.input), "fetched": len(items)}]
    else:
        wanted = [s.strip() for s in args.sources.split(",")] if args.sources else None
        try:
            sources = load_news_sources(args.sources_file, wanted)
        except (OSError, ValueError, KeyError) as e:
            print("출처 목록을 읽을 수 없습니다: %s" % e, file=sys.stderr)
            return 2
        if not sources:
            print("수집 가능한 뉴스 RSS 출처가 없습니다 (%s)." % args.sources_file, file=sys.stderr)
            return 2
        items, per_source = collect_rss(sources)
        log["sources"] = per_source
        for s in per_source:
            if s["result"] != "ok":
                failed = True
                print("수집 실패 [%s %s] (%s): %s" % (s["source_id"], s["source_name"], s["failure_reason"], s["detail"]),
                      file=sys.stderr)

    if args.mode == "rss" and not args.no_accumulate:
        raw_path = Path(args.raw_dir) / "news_items.csv"
        merged, added = merge_raw_items(load_raw_items(raw_path), items, collected_at)
        save_raw_items(raw_path, merged)
        log["accumulated"] = {"store": str(raw_path), "added": added, "total": len(merged)}
        items = merged
    rows, texts = build_documents(items, collected_at, date_from, date_to)
    counts, reasons = summarize(rows)
    by_source = {}
    for r in rows:
        by_source.setdefault(r["source_name"], {"analyzed": 0, "excluded": 0})[r["status"]] += 1
    for s in log["sources"]:
        s.update(status_counts=by_source.get(s["source_name"], {"analyzed": 0, "excluded": 0}))
    log.update(result="partial_failure" if failed else "ok", total=len(rows),
               status_counts=counts, exclusion_reasons=reasons)
    write_outputs(out_dir, rows, texts, log)
    print("수집 완료: 전체 %d건 / 분석 %d / 제외 %d %s -> %s" % (
        len(rows), counts["analyzed"], counts["excluded"], reasons or "", out_dir / "documents.csv"))
    for name, c in by_source.items():
        print("  %s: 분석 %d / 제외 %d" % (name, c["analyzed"], c["excluded"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
