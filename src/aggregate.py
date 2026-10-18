"""집계 모듈 (이슈 #13, 담당 B)

`documents.csv` 와 `document_keywords.csv` 를 읽어 기간·자료 유형·표준 키워드별
지표를 계산하고 `keyword_stats.csv` 를 만든다. 대시보드(A)와 보고서(B)가 모두
이 파일을 쓴다.

계산 규칙은 `docs/data_dictionary.md` §6 에 있고, 이 모듈은 그 구현이다.

표준 라이브러리만 쓴다. 네트워크에 접속하지 않으므로 고정 입력으로 검증할 수
있다 (완료기준 C4, C8).

사용법
    python src/aggregate.py
    python src/aggregate.py --period-type weekly
    python src/aggregate.py --as-of 2026-10-17 --out data/processed/keyword_stats.csv
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"
KEYWORDS_CSV = PROCESSED_DIR / "document_keywords.csv"
STATS_CSV = PROCESSED_DIR / "keyword_stats.csv"

KST = timezone(timedelta(hours=9))

# data_dictionary.md §6 의 필드 순서
STATS_FIELDS = [
    "period_type", "period_start", "period_end", "source_type",
    "keyword_id", "keyword", "document_count", "occurrence_count",
    "total_documents", "document_share", "rank", "is_partial_period",
]

NOT_APPLICABLE = "해당 없음"
PERIOD_TYPES = ("weekly", "monthly")
SOURCE_TYPES = ("news", "policy")


# ──────────────────────────── 기간 ────────────────────────────

def week_bounds(d: date) -> tuple[date, date]:
    """주간 경계. 월요일 시작, 일요일 종료. 양쪽 모두 포함."""
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def month_bounds(d: date) -> tuple[date, date]:
    """월간 경계. 달력 월 기준. 양쪽 모두 포함."""
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, 1), date(d.year, d.month, last)


def period_of(d: date, period_type: str) -> tuple[date, date]:
    if period_type == "weekly":
        return week_bounds(d)
    if period_type == "monthly":
        return month_bounds(d)
    raise ValueError(f"알 수 없는 기간 단위: {period_type}")


# ──────────────────────────── 읽기 ────────────────────────────

def read_csv(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def analyzed_documents(rows: list[dict]) -> list[dict]:
    """집계 대상은 status=analyzed 이고 발행일이 있는 문서뿐이다."""
    return [r for r in rows
            if r.get("status") == "analyzed" and r.get("published_date")]


# ──────────────────────────── 순위 ────────────────────────────

def assign_ranks(rows: list[dict]) -> None:
    """문서 빈도 내림차순 순위. 동률은 공동 순위(1, 2, 2, 4 방식).

    같은 빈도 안에서는 keyword 이름순으로 정렬한다 (data_dictionary.md §6).
    제자리에서 `rank` 를 채운다.
    """
    rows.sort(key=lambda r: (-r["document_count"], r["keyword"]))
    previous_count = None
    previous_rank = 0
    for i, row in enumerate(rows, start=1):
        if row["document_count"] == previous_count:
            row["rank"] = previous_rank
        else:
            row["rank"] = i
            previous_rank = i
            previous_count = row["document_count"]


# ──────────────────────────── 집계 ────────────────────────────

def aggregate(documents: list[dict], keywords: list[dict], *,
              period_types: tuple[str, ...] = PERIOD_TYPES,
              as_of: date | None = None) -> tuple[list[dict], list[dict]]:
    """집계한다. (keyword_stats 행 목록, 기간별 분석 문서 수 기록) 을 돌려준다.

    `as_of` 는 부분 기간 판정 기준일이다. 기본값은 실행일(KST).
    고정 입력 검증에서는 명시해 결과를 고정한다.
    """
    as_of = as_of or datetime.now(KST).date()

    docs = {r["document_id"]: r for r in analyzed_documents(documents)}

    # 문서별 키워드. document_keywords.csv 는 분석 문서만 담지만, 안전하게 걸러낸다
    by_document: dict[str, list[dict]] = {}
    for row in keywords:
        did = row.get("document_id", "")
        if did in docs:
            by_document.setdefault(did, []).append(row)

    stats: list[dict] = []
    totals_log: list[dict] = []

    for period_type in period_types:
        # 기간별로 문서를 모은다
        buckets: dict[tuple[date, date], list[str]] = {}
        for did, doc in docs.items():
            try:
                published = date.fromisoformat(doc["published_date"])
            except ValueError:
                continue
            buckets.setdefault(period_of(published, period_type), []).append(did)

        for (start, end), doc_ids in sorted(buckets.items()):
            is_partial = "yes" if start <= as_of <= end else "no"

            # 자료 유형별 집합. all 은 문서 식별자의 합집합으로 다시 센다
            by_type: dict[str, set[str]] = {t: set() for t in SOURCE_TYPES}
            for did in doc_ids:
                stype = docs[did].get("source_type", "")
                if stype in by_type:
                    by_type[stype].add(did)
            by_type["all"] = set(doc_ids)

            for source_type, id_set in by_type.items():
                total_documents = len(id_set)
                totals_log.append({
                    "period_type": period_type,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "source_type": source_type,
                    "total_documents": total_documents,
                    "is_partial_period": is_partial,
                })
                if total_documents == 0:
                    continue

                # 키워드별 집계
                per_keyword: dict[str, dict] = {}
                for did in id_set:
                    for kw in by_document.get(did, []):
                        kid = kw["keyword_id"]
                        entry = per_keyword.setdefault(kid, {
                            "keyword_id": kid,
                            "keyword": kw.get("keyword", ""),
                            "documents": set(),
                            "occurrence_count": 0,
                        })
                        entry["documents"].add(did)
                        try:
                            entry["occurrence_count"] += int(kw.get("occurrences") or 0)
                        except ValueError:
                            pass

                rows = []
                for entry in per_keyword.values():
                    document_count = len(entry["documents"])
                    share = round(document_count / total_documents * 100, 2)
                    rows.append({
                        "period_type": period_type,
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                        "source_type": source_type,
                        "keyword_id": entry["keyword_id"],
                        "keyword": entry["keyword"],
                        "document_count": document_count,
                        "occurrence_count": entry["occurrence_count"],
                        "total_documents": total_documents,
                        "document_share": f"{share:.2f}",
                        "rank": 0,
                        "is_partial_period": is_partial,
                    })

                assign_ranks(rows)
                stats.extend(rows)

    return stats, totals_log


# ──────────────────────────── 쓰기 ────────────────────────────

def write_stats(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STATS_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in STATS_FIELDS})


def validate_stats(rows: list[dict]) -> list[str]:
    """완료기준 C4 점검. 문제를 문장 목록으로 돌려준다. 빈 목록이면 통과."""
    problems: list[str] = []
    for i, row in enumerate(rows, start=2):
        if row.get("period_type") not in PERIOD_TYPES:
            problems.append(f"{i}행: period_type 허용값이 아니다 ({row.get('period_type')!r})")
        if row.get("source_type") not in (*SOURCE_TYPES, "all"):
            problems.append(f"{i}행: source_type 허용값이 아니다 ({row.get('source_type')!r})")
        if row.get("is_partial_period") not in ("yes", "no"):
            problems.append(f"{i}행: is_partial_period 허용값이 아니다")
        if row.get("period_start", "") > row.get("period_end", ""):
            problems.append(f"{i}행: period_start 가 period_end 보다 늦다")

        try:
            dc = int(row["document_count"])
            td = int(row["total_documents"])
            oc = int(row["occurrence_count"])
        except (KeyError, ValueError):
            problems.append(f"{i}행: 건수 필드가 정수가 아니다")
            continue

        if dc < 0 or td < 0 or oc < 0:
            problems.append(f"{i}행: 건수가 음수다")
        if dc > td:
            problems.append(f"{i}행: document_count({dc}) 가 total_documents({td}) 보다 크다")
        if oc < dc:
            problems.append(f"{i}행: occurrence_count({oc}) 가 document_count({dc}) 보다 작다")

        share = row.get("document_share", "")
        if share != NOT_APPLICABLE and td > 0:
            expected = round(dc / td * 100, 2)
            try:
                if abs(float(share) - expected) > 0.01:
                    problems.append(f"{i}행: document_share 가 맞지 않다 "
                                    f"({share} != {expected:.2f})")
            except ValueError:
                problems.append(f"{i}행: document_share 가 숫자가 아니다 ({share!r})")
    return problems


# ──────────────────────────── 실행 ────────────────────────────

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="기간·유형·키워드별 집계 (B 담당)")
    ap.add_argument("--documents", type=Path, default=DOCUMENTS_CSV)
    ap.add_argument("--keywords", type=Path, default=KEYWORDS_CSV)
    ap.add_argument("--out", type=Path, default=STATS_CSV)
    ap.add_argument("--period-type", choices=PERIOD_TYPES,
                    help="하나만 집계. 기본값은 주간과 월간 모두")
    ap.add_argument("--as-of", help="부분 기간 판정 기준일 YYYY-MM-DD. 기본값은 실행일")
    args = ap.parse_args(argv)

    documents = read_csv(args.documents)
    if not documents:
        print(f"문서가 없습니다: {args.documents}", file=sys.stderr)
        print("먼저 src/collect_policy.py 로 수집하세요.", file=sys.stderr)
        return 1

    keywords = read_csv(args.keywords)
    docs_analyzed = analyzed_documents(documents)
    print(f"문서 {len(documents)}건 (분석 대상 {len(docs_analyzed)}건)")
    print(f"문서-키워드 {len(keywords)}건")

    if not keywords:
        print()
        print("document_keywords.csv 가 없어 키워드 집계를 만들 수 없습니다.")
        print("키워드 추출은 이슈 #11(담당 A)입니다. 추출 결과가 들어오면 "
              "이 명령을 그대로 다시 실행하면 됩니다.")
        print("계산 규칙 자체는 tests/test_aggregate.py 의 고정 입력으로 검증됩니다.")

    period_types = (args.period_type,) if args.period_type else PERIOD_TYPES
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    stats, totals = aggregate(documents, keywords,
                              period_types=period_types, as_of=as_of)

    write_stats(args.out, stats)
    problems = validate_stats(stats)

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    log_path = PROCESSED_DIR / f"aggregate_log_{stamp}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "run_at": datetime.now(KST).isoformat(timespec="seconds"),
        "as_of": (as_of or datetime.now(KST).date()).isoformat(),
        "documents_total": len(documents),
        "documents_analyzed": len(docs_analyzed),
        "document_keywords": len(keywords),
        "stats_rows": len(stats),
        "period_totals": totals,
        "validation_problems": problems,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"집계 행 {len(stats)}건 -> {args.out}")
    print(f"기간·유형 조합 {len(totals)}개의 분석 문서 수를 실행 기록에 남겼습니다")
    print(f"실행 기록 -> data/processed/{log_path.name}")

    if problems:
        print(f"\n규격 검증에서 문제 {len(problems)}건 (완료기준 C4):")
        for p in problems[:10]:
            print(f"  - {p}")
        return 2

    print("규격 검증 통과 (완료기준 C4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
