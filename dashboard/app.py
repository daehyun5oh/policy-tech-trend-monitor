#!/usr/bin/env python3
"""대시보드 생성 (이슈 #16, #17).

keyword_stats.csv, documents.csv, document_keywords.csv를 읽어 서버 없이 열리는
HTML 파일 하나(dashboard.html)를 만든다. 외부 글꼴·CDN을 쓰지 않는다.
화면에는 문서 빈도·출현 횟수·비율·순위, 기간별 추이, 키워드별 근거 문서가 나온다.

사용 예:
  python dashboard/app.py --dir data/processed --open
  python dashboard/app.py --dir tests/fixtures --stats tests/fixtures/expected/keyword_stats.csv \\
      --keywords tests/fixtures/expected/document_keywords.csv --out dashboard.html

부분 기간 판정(제안): documents.csv의 collected_at 중 가장 늦은 날짜(수집 종료일)보다
period_end가 뒤인 기간을 '부분 기간'으로 표시한다. keyword_stats.csv에는 부분 기간 열이 없다.

종료 코드: 0 정상 / 1 입력 CSV가 규격에 맞지 않음 / 2 파일을 읽을 수 없음
"""
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import webbrowser
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import validate_csv as vc  # noqa: E402
from schema import SOURCE_TYPES, STATUS_ANALYZED, STATUS_EXCLUDED, STATUS_FAILED  # noqa: E402

TEMPLATE = HERE / "template.html"
KST = dt.timezone(dt.timedelta(hours=9))
TYPES = [("all", "통합"), ("news", "뉴스"), ("policy", "정책")]
DEFAULT_TITLE = "기술키워드 동향 모니터"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def week_of(d):
    start = d - dt.timedelta(days=d.weekday())
    return start, start + dt.timedelta(days=6)


def month_of(d):
    start = d.replace(day=1)
    return start, vc.month_end(start)


def cell(row, key):
    return (row.get(key) or "").strip()


def rank_key(row):
    rank = cell(row, "rank")
    return (int(rank) if rank.isdigit() else 10 ** 9, cell(row, "keyword"))


def build_model(documents, keyword_rows, stats_rows, title=DEFAULT_TITLE, generated_at=None):
    """CSV 행 목록으로 화면에 필요한 모든 값을 미리 계산한 dict를 만든다."""
    analyzed, excluded = [], []
    failed = {t: 0 for t in SOURCE_TYPES}
    undated = {t: 0 for t in SOURCE_TYPES}
    collected = []
    for r in documents:
        status, stype = cell(r, "status"), cell(r, "source_type")
        day = vc.parse_date(cell(r, "published_date"))
        if vc.parse_aware_datetime(cell(r, "collected_at")):
            collected.append(cell(r, "collected_at")[:10])
        if status == STATUS_FAILED and stype in failed:
            failed[stype] += 1
        elif status == STATUS_EXCLUDED and day is None and stype in undated:
            undated[stype] += 1
        if day is None:
            continue
        if status == STATUS_ANALYZED:
            analyzed.append((day, r))
        elif status == STATUS_EXCLUDED:
            excluded.append((day, r))
    analyzed.sort(key=lambda x: (-x[0].toordinal(), cell(x[1], "document_id")))

    spans = {"weekly": set(), "monthly": set()}
    for r in stats_rows:
        if cell(r, "period_type") in spans:
            spans[cell(r, "period_type")].add((cell(r, "period_start"), cell(r, "period_end")))
    for day, _ in analyzed:
        spans["weekly"].add(tuple(x.isoformat() for x in week_of(day)))
        spans["monthly"].add(tuple(x.isoformat() for x in month_of(day)))
    latest = max(collected) if collected else ""
    periods = {pt: [{"start": s, "end": e, "partial": bool(latest) and e > latest} for s, e in sorted(v)]
               for pt, v in spans.items()}

    by_group = defaultdict(list)
    for r in stats_rows:
        by_group[(cell(r, "period_type"), cell(r, "period_start"), cell(r, "source_type"))].append(r)
    kids_by_doc = defaultdict(set)
    for r in keyword_rows:
        kids_by_doc[cell(r, "document_id")].add(cell(r, "keyword_id"))

    doc_list, doc_index = [], {}

    def index_of(row):
        did = cell(row, "document_id")
        if did not in doc_index:
            doc_index[did] = len(doc_list)
            doc_list.append({"t": cell(row, "title"), "d": cell(row, "published_date"),
                             "s": cell(row, "source_name"), "u": cell(row, "original_url") if vc.is_http_url(cell(row, "original_url")) else "",
                             "y": cell(row, "source_type")})
        return doc_index[did]

    views = {}
    for pt, plist in periods.items():
        for p in plist:
            start, end = p["start"], p["end"]
            for tid, _ in TYPES:
                def inside(item):
                    return start <= item[0].isoformat() <= end and (tid == "all" or cell(item[1], "source_type") == tid)
                members = [m for m in analyzed if inside(m)]
                rows = sorted(by_group.get((pt, start, tid), []), key=rank_key)
                stats = [{"id": cell(r, "keyword_id"), "kw": cell(r, "keyword"),
                          "dc": vc.to_int(cell(r, "document_count")) or 0, "oc": vc.to_int(cell(r, "occurrence_count")) or 0,
                          "share": cell(r, "document_share"), "rank": cell(r, "rank")} for r in rows]
                wanted = {s["id"] for s in stats}
                evidence = defaultdict(list)
                for _, doc in members:
                    for kid in sorted(kids_by_doc.get(cell(doc, "document_id"), frozenset()) & wanted):
                        evidence[kid].append(index_of(doc))
                views["%s|%s|%s" % (pt, start, tid)] = {
                    "analyzed": len(members),
                    "excluded": sum(1 for m in excluded if inside(m)),
                    "csvTotal": (vc.to_int(cell(rows[0], "total_documents")) if rows else None),
                    "stats": stats,
                    "ev": dict(evidence),
                }
    return {
        "title": title,
        "generatedAt": generated_at or dt.datetime.now(KST).isoformat(timespec="seconds"),
        "latestCollected": latest,
        "types": [{"id": i, "label": l} for i, l in TYPES],
        "periods": periods,
        "views": views,
        "docs": doc_list,
        "overall": {"failed": failed, "excludedUndated": undated},
    }


def embed_json(model):
    """<script> 안에 넣어도 태그가 끝나거나 열리지 않도록 <, >, &를 유니코드 이스케이프한다."""
    text = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    for ch, esc in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(ch, esc)
    return text


def render_html(model, template_text=None):
    template_text = template_text if template_text is not None else TEMPLATE.read_text(encoding="utf-8")
    values = {"TITLE": html.escape(model["title"]), "DATA": embed_json(model)}
    # 한 번에 치환해, 데이터 안에 __DATA__ 같은 문자열이 있어도 다시 치환되지 않게 한다.
    return re.sub(r"__(TITLE|DATA)__", lambda m: values[m.group(1)], template_text)


def main(argv=None):
    p = argparse.ArgumentParser(description="CSV로 대시보드 HTML 파일을 만든다.")
    p.add_argument("--dir", default="data/processed", help="CSV 3종이 있는 폴더 (기본 data/processed)")
    p.add_argument("--documents")
    p.add_argument("--keywords")
    p.add_argument("--stats")
    p.add_argument("--out", help="출력 HTML (기본 <dir>/dashboard.html)")
    p.add_argument("--title", default=DEFAULT_TITLE)
    p.add_argument("--no-validate", action="store_true", help="입력 CSV 규격 검사를 건너뜀")
    p.add_argument("--open", action="store_true", help="만든 뒤 기본 브라우저로 연다")
    args = p.parse_args(argv)
    base = Path(args.dir)
    documents = args.documents or str(base / "documents.csv")
    keywords = args.keywords or str(base / "document_keywords.csv")
    stats = args.stats or str(base / "keyword_stats.csv")
    out = Path(args.out) if args.out else base / "dashboard.html"
    try:
        if not args.no_validate:
            report = vc.run(documents, keywords, stats)
            if report.errors:
                for name, row, message in report.errors[:20]:
                    print("오류 %s %d행: %s" % (name, row, message), file=sys.stderr)
                print("입력 CSV에 오류가 %d건 있어 대시보드를 만들지 않았습니다. (--no-validate로 건너뛸 수 있음)" % len(report.errors), file=sys.stderr)
                return 1
        model = build_model(read_csv(documents), read_csv(keywords), read_csv(stats), title=args.title)
    except OSError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(model), encoding="utf-8")
    print("대시보드 생성: %s (기간 %d개, 화면 %d개)" % (out, sum(len(v) for v in model["periods"].values()), len(model["views"])))
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
