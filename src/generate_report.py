"""보고서 생성기 (이슈 #19, 담당 B)

집계 결과(`keyword_stats.csv`)와 문서 목록을 읽어 주간·월간 Markdown 보고서를
만든다. 대시보드와 같은 CSV를 쓰므로 수치가 어긋나지 않는다 (기획안 §4-6).

포함 항목은 기획안 §5 를 따른다.
    1. 분석 기간, 생성 시각, 사용한 출처·사전 버전
    2. 자료 유형별 수집·분석·제외 문서 수
    3. 상위 기술키워드의 문서 빈도·비율·순위
    4. 주요 키워드의 근거 문서 링크와 발행일
    5. 수집 누락, 부분 기간 및 해석 한계

재배포 제한이 있는 출처는 제목을 보고서에 넣지 않는다. `config/sources.csv` 의
`redistribution` 이 `local_only_no_publish` 인 출처가 해당한다
(`docs/source_policy.md` §3, §6).

표준 라이브러리만 쓴다.

사용법
    python src/generate_report.py --period-type weekly
    python src/generate_report.py --period-type monthly --period-start 2026-10-01
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
SOURCES_CSV = ROOT / "config" / "sources.csv"
DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"
KEYWORDS_CSV = PROCESSED_DIR / "document_keywords.csv"
STATS_CSV = PROCESSED_DIR / "keyword_stats.csv"
REPORTS_DIR = ROOT / "reports"

KST = timezone(timedelta(hours=9))
NOT_APPLICABLE = "해당 없음"

# 제목을 공개 산출물에 넣지 않는 재배포 조건
TITLE_RESTRICTED = {"local_only_no_publish"}

PERIOD_LABEL = {"weekly": "주간", "monthly": "월간"}

# 기획안 §2, §4 의 해석 한계. 보고서마다 그대로 싣는다
INTERPRETATION_LIMITS = [
    "빈도는 수집한 자료 안의 언급량입니다. 기술의 혁신성·성숙도·시장성이나 미래 유망성을 뜻하지 않습니다.",
    "기간별 차이를 신규 또는 급증으로 판정하지 않습니다. 수치 변화만 적습니다.",
    "출처와 수집 기간이 다르면 수치를 바로 비교할 수 없습니다. 뉴스와 정책의 발행량 차이가 크기 때문에 자료 유형별 결과를 기본으로 봅니다.",
    "이 보고서는 정책 효과를 평가하지 않습니다.",
]


def read_csv(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ──────────────────────────── 재배포 조건 ────────────────────────────

NUMERIC_STATS_FIELDS = ("document_count", "occurrence_count", "total_documents", "rank")


def normalize_stats_row(row: dict) -> dict:
    """집계 행의 숫자 필드를 정수로 맞춘다.

    `aggregate()` 를 바로 호출하면 정수가 오지만 `keyword_stats.csv` 를 읽으면
    문자열이 온다. 두 경로를 같게 다루려고 여기서 변환한다.
    `document_share` 는 "해당 없음" 이 들어올 수 있어 문자열로 둔다.
    """
    out = dict(row)
    for field in NUMERIC_STATS_FIELDS:
        try:
            out[field] = int(out.get(field, 0) or 0)
        except (TypeError, ValueError):
            out[field] = 0
    return out


def redistribution_map(sources: list[dict]) -> dict[str, str]:
    """출처 이름 → 재배포 조건."""
    return {r["source_name"]: r.get("redistribution", "")
            for r in sources if r.get("source_name")}


def title_allowed(source_name: str, redistribution: dict[str, str]) -> bool:
    """이 출처의 제목을 공개 산출물에 실어도 되는지."""
    return redistribution.get(source_name, "") not in TITLE_RESTRICTED


# ──────────────────────────── 기간 고르기 ────────────────────────────

def available_periods(stats: list[dict], period_type: str) -> list[tuple[str, str]]:
    seen = {(r["period_start"], r["period_end"]) for r in stats
            if r["period_type"] == period_type}
    return sorted(seen)


def pick_period(stats: list[dict], period_type: str,
                period_start: str | None) -> tuple[str, str] | None:
    periods = available_periods(stats, period_type)
    if not periods:
        return None
    if period_start:
        for p in periods:
            if p[0] == period_start:
                return p
        return None
    return periods[-1]


def period_bounds_for_documents(period_type: str, start: str, end: str) -> tuple[str, str]:
    """집계 행이 없을 때도 문서 수를 세려면 기간 경계가 필요하다."""
    return start, end


# ──────────────────────────── 문서 수 ────────────────────────────

def document_counts(documents: list[dict], start: str, end: str) -> dict:
    """자료 유형별 수집·분석·제외 문서 수.

    발행일이 없는 문서는 기간을 배정할 수 없어 따로 센다 (기획안 §3).
    """
    result = {
        "analyzed": {"news": 0, "policy": 0, "all": 0},
        "excluded": {},
        "failed": {},
        "undated": 0,
        "collected_in_period": 0,
    }
    for row in documents:
        published = row.get("published_date", "")
        status = row.get("status", "")
        reason = row.get("exclusion_reason", "")

        if not published:
            result["undated"] += 1
            if status == "excluded":
                result["excluded"][reason] = result["excluded"].get(reason, 0) + 1
            elif status == "failed":
                result["failed"][reason] = result["failed"].get(reason, 0) + 1
            continue

        if not (start <= published <= end):
            continue

        result["collected_in_period"] += 1
        if status == "analyzed":
            stype = row.get("source_type", "")
            if stype in result["analyzed"]:
                result["analyzed"][stype] += 1
            result["analyzed"]["all"] += 1
        elif status == "excluded":
            result["excluded"][reason] = result["excluded"].get(reason, 0) + 1
        elif status == "failed":
            result["failed"][reason] = result["failed"].get(reason, 0) + 1
    return result


# ──────────────────────────── 근거 문서 ────────────────────────────

def evidence_documents(documents: list[dict], keywords: list[dict],
                       keyword_id: str, start: str, end: str,
                       source_type: str, limit: int) -> list[dict]:
    """키워드 하나의 근거 문서를 발행일 내림차순으로 고른다."""
    docs = {r["document_id"]: r for r in documents
            if r.get("status") == "analyzed" and r.get("published_date")}
    matched = []
    for kw in keywords:
        if kw.get("keyword_id") != keyword_id:
            continue
        doc = docs.get(kw.get("document_id", ""))
        if not doc:
            continue
        if not (start <= doc["published_date"] <= end):
            continue
        if source_type != "all" and doc.get("source_type") != source_type:
            continue
        matched.append(doc)

    matched.sort(key=lambda d: (d["published_date"], d["document_id"]), reverse=True)
    return matched[:limit]


# ──────────────────────────── 보고서 자료 ────────────────────────────

def build_report(*, documents: list[dict], keywords: list[dict],
                 stats: list[dict], sources: list[dict],
                 period_type: str, period: tuple[str, str],
                 source_type: str = "all", top_n: int = 10,
                 evidence_limit: int = 5,
                 generated_at: str | None = None) -> dict:
    """보고서에 들어갈 자료를 모은다. 렌더링과 분리해 검증하기 쉽게 둔다."""
    start, end = period
    redistribution = redistribution_map(sources)

    # 집계 결과는 메모리(aggregate 호출)로도 오고 CSV 파일로도 온다.
    # CSV 를 거치면 숫자가 문자열이 되므로 여기서 형을 맞춘다.
    rows = [normalize_stats_row(r) for r in stats
            if r["period_type"] == period_type
            and r["period_start"] == start and r["period_end"] == end
            and r["source_type"] == source_type]
    rows.sort(key=lambda r: (r["rank"], r["keyword"]))
    top = rows[:top_n]

    is_partial = "no"
    if rows:
        is_partial = rows[0].get("is_partial_period", "no")
    else:
        today = date.today().isoformat()
        is_partial = "yes" if start <= today <= end else "no"

    dictionary_versions = sorted({k.get("dictionary_version", "")
                                  for k in keywords if k.get("dictionary_version")})

    used_sources = sorted({r["source_name"] for r in documents
                           if r.get("status") == "analyzed"
                           and r.get("published_date")
                           and start <= r["published_date"] <= end})

    evidence = {}
    restricted_used = False
    for row in top:
        docs = evidence_documents(documents, keywords, row["keyword_id"],
                                  start, end, source_type, evidence_limit)
        items = []
        for d in docs:
            allowed = title_allowed(d["source_name"], redistribution)
            if not allowed:
                restricted_used = True
            items.append({
                "published_date": d["published_date"],
                "title": d["title"] if allowed else "",
                "title_withheld": not allowed,
                "original_url": d["original_url"],
                "source_name": d["source_name"],
                "source_type": d.get("source_type", ""),
            })
        evidence[row["keyword_id"]] = items

    return {
        "period_type": period_type,
        "period_start": start,
        "period_end": end,
        "source_type": source_type,
        "is_partial_period": is_partial,
        "generated_at": generated_at or datetime.now(KST).isoformat(timespec="seconds"),
        "counts": document_counts(documents, start, end),
        "top_keywords": top,
        "evidence": evidence,
        "used_sources": used_sources,
        "dictionary_versions": dictionary_versions,
        "restricted_titles_used": restricted_used,
        "stats_rows_in_period": len(rows),
    }


# ──────────────────────────── 렌더링 ────────────────────────────

def render(report: dict) -> str:
    label = PERIOD_LABEL.get(report["period_type"], report["period_type"])
    start, end = report["period_start"], report["period_end"]
    counts = report["counts"]
    out: list[str] = []

    out.append(f"# {label} 기술키워드 보고서 — {start} ~ {end}")
    out.append("")
    if report["is_partial_period"] == "yes":
        out.append("> **부분 기간입니다.** 이 기간이 끝나지 않은 상태에서 집계했습니다. "
                   "뒤에 자료가 더 쌓이면 수치가 달라집니다.")
        out.append("")

    # 1. 분석 기간·생성 시각·출처·사전 버전
    out.append("## 1. 집계 조건")
    out.append("")
    out.append(f"- **분석 기간** {start} ~ {end} (양쪽 날짜 포함)")
    out.append(f"- **자료 유형** {report['source_type']}")
    out.append(f"- **생성 시각** {report['generated_at']}")
    versions = ", ".join(report["dictionary_versions"]) or NOT_APPLICABLE
    out.append(f"- **사전 버전** {versions}")
    if report["used_sources"]:
        out.append(f"- **출처** {', '.join(report['used_sources'])}")
    else:
        out.append(f"- **출처** {NOT_APPLICABLE}")
    out.append("")

    # 2. 자료 유형별 문서 수
    out.append("## 2. 문서 수")
    out.append("")
    out.append("| 자료 유형 | 분석 문서 |")
    out.append("|---|---|")
    for stype in ("news", "policy", "all"):
        out.append(f"| {stype} | {counts['analyzed'][stype]}건 |")
    out.append("")
    out.append(f"기간 안에서 수집된 문서는 {counts['collected_in_period']}건입니다.")
    out.append("")

    if counts["excluded"] or counts["failed"]:
        out.append("### 제외·실패 문서")
        out.append("")
        out.append("| 사유 | 건수 |")
        out.append("|---|---|")
        for reason, n in sorted(counts["excluded"].items()):
            out.append(f"| 제외 · {reason} | {n}건 |")
        for reason, n in sorted(counts["failed"].items()):
            out.append(f"| 실패 · {reason} | {n}건 |")
        out.append("")
    if counts["undated"]:
        out.append(f"발행일을 확인할 수 없어 기간을 배정하지 못한 문서가 "
                   f"{counts['undated']}건 있습니다. 수집일로 대체하지 않았습니다.")
        out.append("")

    # 3. 상위 키워드
    out.append("## 3. 상위 기술키워드")
    out.append("")
    if not report["top_keywords"]:
        out.append("이 기간에 집계된 키워드가 없습니다.")
        out.append("")
        if not report["dictionary_versions"]:
            out.append("키워드 추출 결과(`document_keywords.csv`)가 아직 없기 때문입니다. "
                       "추출이 끝나면 같은 조건으로 다시 생성하면 채워집니다.")
            out.append("")
    else:
        out.append("| 순위 | 키워드 | 문서 빈도 | 문서 비율 | 출현 횟수 |")
        out.append("|---|---|---|---|---|")
        for row in report["top_keywords"]:
            share = row["document_share"]
            share_text = share if share == NOT_APPLICABLE else f"{share}%"
            out.append(f"| {row['rank']} | {row['keyword']} | "
                       f"{row['document_count']}건 | {share_text} | "
                       f"{row['occurrence_count']}회 |")
        out.append("")
        total = report["top_keywords"][0]["total_documents"]
        out.append(f"문서 비율의 분모는 이 기간·유형의 분석 문서 {total}건입니다. "
                   f"키워드가 없는 문서도 분모에 들어갑니다.")
        out.append("")
        out.append("문서 빈도는 해당 키워드를 포함한 문서 수입니다. "
                   "한 문서에서 여러 번 나와도 1건으로 셉니다.")
        out.append("")

    # 4. 근거 문서
    out.append("## 4. 근거 문서")
    out.append("")
    if not report["top_keywords"]:
        out.append("상위 키워드가 없어 근거 문서를 싣지 않습니다.")
        out.append("")
    else:
        for row in report["top_keywords"]:
            items = report["evidence"].get(row["keyword_id"], [])
            out.append(f"### {row['keyword']} ({row['document_count']}건)")
            out.append("")
            if not items:
                out.append("근거 문서를 찾지 못했습니다.")
                out.append("")
                continue
            for item in items:
                if item["title_withheld"]:
                    out.append(f"- {item['published_date']} · {item['source_name']} · "
                               f"제목 비공개 · <{item['original_url']}>")
                else:
                    out.append(f"- {item['published_date']} · {item['source_name']} · "
                               f"{item['title']} · <{item['original_url']}>")
            if len(items) < row["document_count"]:
                out.append("")
                out.append(f"위는 최근 {len(items)}건입니다. 전체 목록은 "
                           f"`data/processed/documents.csv` 에서 확인할 수 있습니다.")
            out.append("")

    # 5. 누락·부분 기간·해석 한계
    out.append("## 5. 읽을 때 주의할 점")
    out.append("")
    if report["restricted_titles_used"]:
        out.append("- 재배포가 제한된 출처의 문서는 **제목을 싣지 않고** 발행일과 "
                   "원문 주소만 적었습니다. 근거는 원문 주소로 확인할 수 있습니다.")
    if report["is_partial_period"] == "yes":
        out.append("- 이 기간은 아직 끝나지 않았습니다. 자료가 더 쌓이면 수치가 달라집니다.")
    if counts["undated"]:
        out.append(f"- 발행일을 확인할 수 없는 문서 {counts['undated']}건은 "
                   f"기간 집계에서 빠졌습니다.")
    for line in INTERPRETATION_LIMITS:
        out.append(f"- {line}")
    out.append("")
    out.append("---")
    out.append("")
    out.append(f"이 보고서는 `{Path('data/processed/keyword_stats.csv').as_posix()}` 의 "
               f"같은 조건 행 {report['stats_rows_in_period']}건을 그대로 옮긴 것입니다. "
               f"대시보드와 같은 파일을 씁니다.")
    out.append("")
    return "\n".join(out)


def output_path(report: dict, out_dir: Path | None = None) -> Path:
    sub = {"weekly": "weekly", "monthly": "monthly"}[report["period_type"]]
    base = out_dir or (REPORTS_DIR / sub)
    name = f"report_{report['period_type']}_{report['period_start']}_{report['period_end']}.md"
    return Path(base) / name


# ──────────────────────────── 실행 ────────────────────────────

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="주간·월간 보고서 생성 (B 담당)")
    ap.add_argument("--period-type", choices=("weekly", "monthly"), default="weekly")
    ap.add_argument("--period-start", help="기간 시작일 YYYY-MM-DD. 기본값은 가장 최근 기간")
    ap.add_argument("--source-type", choices=("news", "policy", "all"), default="all")
    ap.add_argument("--top", type=int, default=10, help="상위 키워드 개수. 기본 10")
    ap.add_argument("--evidence", type=int, default=5, help="키워드당 근거 문서 수. 기본 5")
    ap.add_argument("--documents", type=Path, default=DOCUMENTS_CSV)
    ap.add_argument("--keywords", type=Path, default=KEYWORDS_CSV)
    ap.add_argument("--stats", type=Path, default=STATS_CSV)
    ap.add_argument("--sources", type=Path, default=SOURCES_CSV)
    ap.add_argument("--out-dir", type=Path, help="저장 폴더. 기본값은 reports/<기간단위>/")
    args = ap.parse_args(argv)

    documents = read_csv(args.documents)
    if not documents:
        print(f"문서가 없습니다: {args.documents}", file=sys.stderr)
        return 1
    keywords = read_csv(args.keywords)
    stats = read_csv(args.stats)
    sources = read_csv(args.sources)

    period = pick_period(stats, args.period_type, args.period_start)
    if period is None:
        if args.period_start:
            print(f"집계에 그 기간이 없습니다: {args.period_start}", file=sys.stderr)
            periods = available_periods(stats, args.period_type)
            if periods:
                print("가능한 기간: " + ", ".join(p[0] for p in periods), file=sys.stderr)
            return 1
        # 집계 행이 없으면 문서 발행일에서 기간을 잡는다
        dates = sorted(r["published_date"] for r in documents
                       if r.get("status") == "analyzed" and r.get("published_date"))
        if not dates:
            print("기간을 정할 수 없습니다. 분석 문서에 발행일이 없습니다.", file=sys.stderr)
            return 1
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import aggregate as ag  # noqa: PLC0415
        start, end = ag.period_of(date.fromisoformat(dates[-1]), args.period_type)
        period = (start.isoformat(), end.isoformat())
        print(f"집계 행이 없어 최근 문서 기준으로 기간을 잡았습니다: "
              f"{period[0]} ~ {period[1]}")

    report = build_report(
        documents=documents, keywords=keywords, stats=stats, sources=sources,
        period_type=args.period_type, period=period,
        source_type=args.source_type, top_n=args.top,
        evidence_limit=args.evidence)

    text = render(report)
    path = output_path(report, args.out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    print(f"{PERIOD_LABEL[args.period_type]} 보고서 생성")
    print(f"  기간 {report['period_start']} ~ {report['period_end']} "
          f"(부분 기간: {report['is_partial_period']})")
    print(f"  분석 문서 {report['counts']['analyzed']['all']}건 "
          f"/ 상위 키워드 {len(report['top_keywords'])}개")
    if report["restricted_titles_used"]:
        print("  재배포 제한 출처의 제목은 싣지 않았습니다")
    print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
