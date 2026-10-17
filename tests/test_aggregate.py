"""집계 모듈 검증 (이슈 #13)

완료기준 C4(문서 빈도·출현 횟수·비율·동률 순위·기간 경계)를 고정 fixture의
**수작업 정답**과 대조한다. 네트워크에 접속하지 않는다.

fixture
    tests/fixtures/aggregate_case1_documents.csv
    tests/fixtures/aggregate_case1_document_keywords.csv

수작업 정답 (기준일 2026-10-17 토요일)

analyzed 문서 5건. 제외 문서 2건(기간 밖, 발행일 없음)은 집계에서 빠진다.

| 문서 | 유형 | 발행일 | 요일 | 키워드(출현) |
|---|---|---|---|---|
| policy-0001 | policy | 2026-10-05 | 월 | 인공지능(5), 양자(2) |
| policy-0002 | policy | 2026-10-06 | 화 | 인공지능(3) |
| policy-0003 | policy | 2026-10-12 | 월 | 인공지능(1), 반도체(4) |
| news-0001 | news | 2026-10-05 | 월 | 인공지능(2), 양자(1) |
| news-0002 | news | 2026-10-07 | 수 | 반도체(6) |

주간 경계(월~일)
- A주 2026-10-05 ~ 10-11 : policy 2건, news 2건, all 4건  (기준일이 밖 → 부분 기간 아님)
- B주 2026-10-12 ~ 10-18 : policy 1건, news 0건, all 1건  (기준일 포함 → 부분 기간)

월간 2026-10-01 ~ 10-31 : policy 3건, news 2건, all 5건  (기준일 포함 → 부분 기간)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import aggregate as ag  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = date(2026, 10, 17)

WEEK_A = ("2026-10-05", "2026-10-11")
WEEK_B = ("2026-10-12", "2026-10-18")
MONTH = ("2026-10-01", "2026-10-31")


@pytest.fixture(scope="module")
def stats_and_totals():
    documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
    keywords = ag.read_csv(FIXTURES / "aggregate_case1_document_keywords.csv")
    return ag.aggregate(documents, keywords, as_of=AS_OF)


def pick(stats, period, source_type, keyword_id, period_type="weekly"):
    """한 행을 찾는다. 없으면 None."""
    for row in stats:
        if (row["period_type"] == period_type
                and (row["period_start"], row["period_end"]) == period
                and row["source_type"] == source_type
                and row["keyword_id"] == keyword_id):
            return row
    return None


def rows_of(stats, period, source_type, period_type="weekly"):
    return [r for r in stats
            if r["period_type"] == period_type
            and (r["period_start"], r["period_end"]) == period
            and r["source_type"] == source_type]


# ──────────────────────────── 기간 경계 ────────────────────────────

def test_week_bounds_monday_to_sunday():
    assert ag.week_bounds(date(2026, 10, 5)) == (date(2026, 10, 5), date(2026, 10, 11))
    assert ag.week_bounds(date(2026, 10, 11)) == (date(2026, 10, 5), date(2026, 10, 11))
    assert ag.week_bounds(date(2026, 10, 12)) == (date(2026, 10, 12), date(2026, 10, 18))


def test_month_bounds_calendar_month():
    assert ag.month_bounds(date(2026, 10, 17)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert ag.month_bounds(date(2026, 2, 3)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_period_of_rejects_unknown_type():
    """일간은 이슈 #26 요청으로 지원 대상이 됐다. 모르는 단위만 거부한다."""
    with pytest.raises(ValueError):
        ag.period_of(date(2026, 10, 17), "yearly")


# ──────────────────────────── 집계 대상 ────────────────────────────

def test_excluded_documents_are_not_counted(stats_and_totals):
    """제외 문서 2건은 집계에 들어가지 않는다."""
    stats, _ = stats_and_totals
    for row in stats:
        assert int(row["total_documents"]) <= 5

    # 기간 밖 문서(2026-01-02)의 1월 기간 행이 생기지 않아야 한다
    jan = [r for r in stats if r["period_start"].startswith("2026-01")]
    assert jan == []


def test_total_documents_includes_docs_without_keywords(stats_and_totals):
    """분모에는 키워드가 없는 문서도 들어간다. 여기서는 모든 문서에 키워드가 있다."""
    stats, totals = stats_and_totals
    week_a_all = [t for t in totals
                  if t["period_type"] == "weekly"
                  and (t["period_start"], t["period_end"]) == WEEK_A
                  and t["source_type"] == "all"]
    assert week_a_all[0]["total_documents"] == 4


# ──────────────────────────── 문서 빈도·출현 횟수 ────────────────────────────

def test_week_a_policy_counts(stats_and_totals):
    stats, _ = stats_and_totals
    ai = pick(stats, WEEK_A, "policy", "kw-ai")
    assert ai["document_count"] == 2
    assert ai["occurrence_count"] == 8          # 5 + 3
    assert ai["total_documents"] == 2
    assert ai["document_share"] == "100.00"
    assert ai["rank"] == 1

    quantum = pick(stats, WEEK_A, "policy", "kw-quantum")
    assert quantum["document_count"] == 1
    assert quantum["occurrence_count"] == 2
    assert quantum["document_share"] == "50.00"
    assert quantum["rank"] == 2


def test_document_frequency_counts_document_once(stats_and_totals):
    """한 문서에서 여러 번 나와도 문서 빈도는 1건이다."""
    stats, _ = stats_and_totals
    ai = pick(stats, WEEK_A, "policy", "kw-ai")
    assert ai["document_count"] == 2 < ai["occurrence_count"]


# ──────────────────────────── 통합(all)은 합집합 ────────────────────────────

def test_all_is_union_not_sum(stats_and_totals):
    """all 은 뉴스·정책 건수를 더하지 않고 문서 식별자의 합집합으로 센다."""
    stats, _ = stats_and_totals
    policy = pick(stats, WEEK_A, "policy", "kw-ai")
    news = pick(stats, WEEK_A, "news", "kw-ai")
    merged = pick(stats, WEEK_A, "all", "kw-ai")

    assert policy["document_count"] == 2
    assert news["document_count"] == 1
    assert merged["document_count"] == 3        # 합집합 {0001, 0002, news-0001}
    assert merged["total_documents"] == 4
    assert merged["document_share"] == "75.00"
    assert merged["occurrence_count"] == 10     # 5 + 3 + 2


def test_all_total_documents_is_union(stats_and_totals):
    stats, _ = stats_and_totals
    policy = rows_of(stats, WEEK_A, "policy")[0]
    news = rows_of(stats, WEEK_A, "news")[0]
    merged = rows_of(stats, WEEK_A, "all")[0]
    assert policy["total_documents"] == 2
    assert news["total_documents"] == 2
    assert merged["total_documents"] == 4


# ──────────────────────────── 동률 순위 ────────────────────────────

def test_tied_ranks_are_shared(stats_and_totals):
    """A주 뉴스는 세 키워드가 모두 1건 → 공동 1위."""
    stats, _ = stats_and_totals
    rows = rows_of(stats, WEEK_A, "news")
    assert {r["keyword_id"] for r in rows} == {"kw-ai", "kw-quantum", "kw-semi"}
    assert all(r["document_count"] == 1 for r in rows)
    assert all(r["rank"] == 1 for r in rows)


def test_rank_sequence_with_ties(stats_and_totals):
    """월간 all: 인공지능 4건(1위), 반도체·양자 각 2건(공동 2위)."""
    stats, _ = stats_and_totals
    rows = sorted(rows_of(stats, MONTH, "all", "monthly"),
                  key=lambda r: (r["rank"], r["keyword"]))
    assert [(r["keyword"], r["document_count"], r["rank"]) for r in rows] == [
        ("인공지능", 4, 1),
        ("반도체", 2, 2),
        ("양자", 2, 2),
    ]


def test_ties_sorted_by_keyword_name(stats_and_totals):
    """동률 안에서는 keyword 이름순으로 정렬한다."""
    stats, _ = stats_and_totals
    rows = rows_of(stats, WEEK_B, "policy")
    assert [r["keyword"] for r in rows] == ["반도체", "인공지능"]
    assert all(r["rank"] == 1 for r in rows)


# ──────────────────────────── 비율 반올림 ────────────────────────────

def test_share_rounds_to_two_decimals(stats_and_totals):
    """월간 정책: 양자 1건 / 분모 3건 → 33.33"""
    stats, _ = stats_and_totals
    quantum = pick(stats, MONTH, "policy", "kw-quantum", "monthly")
    assert quantum["document_count"] == 1
    assert quantum["total_documents"] == 3
    assert quantum["document_share"] == "33.33"


def test_share_full_coverage(stats_and_totals):
    stats, _ = stats_and_totals
    ai = pick(stats, MONTH, "policy", "kw-ai", "monthly")
    assert ai["document_count"] == 3
    assert ai["document_share"] == "100.00"


# ──────────────────────────── 부분 기간 ────────────────────────────

def test_partial_period_flag(stats_and_totals):
    """기준일 2026-10-17 이 포함된 기간만 부분 기간이다."""
    stats, _ = stats_and_totals
    assert pick(stats, WEEK_A, "all", "kw-ai")["is_partial_period"] == "no"
    assert pick(stats, WEEK_B, "all", "kw-ai")["is_partial_period"] == "yes"
    assert pick(stats, MONTH, "all", "kw-ai", "monthly")["is_partial_period"] == "yes"


def test_partial_period_changes_with_as_of():
    documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
    keywords = ag.read_csv(FIXTURES / "aggregate_case1_document_keywords.csv")
    stats, _ = ag.aggregate(documents, keywords, as_of=date(2026, 12, 1))
    assert all(r["is_partial_period"] == "no" for r in stats)


# ──────────────────────────── 빈 결과 ────────────────────────────

def test_no_rows_when_type_has_no_documents(stats_and_totals):
    """B주에는 뉴스 문서가 없다. 행을 만들지 않고 실행 기록에만 남긴다."""
    stats, totals = stats_and_totals
    assert rows_of(stats, WEEK_B, "news") == []

    entry = [t for t in totals
             if t["period_type"] == "weekly"
             and (t["period_start"], t["period_end"]) == WEEK_B
             and t["source_type"] == "news"]
    assert len(entry) == 1
    assert entry[0]["total_documents"] == 0


def test_empty_keywords_yields_no_stats():
    """키워드 추출 결과가 없으면 집계 행이 없다. 기간별 문서 수는 남는다."""
    documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
    stats, totals = ag.aggregate(documents, [], as_of=AS_OF)
    assert stats == []
    assert any(t["total_documents"] == 5 for t in totals)


# ──────────────────────────── 검증 함수 ────────────────────────────

def test_validate_passes(stats_and_totals):
    stats, _ = stats_and_totals
    assert ag.validate_stats(stats) == []


def test_validate_catches_share_mismatch(stats_and_totals):
    stats, _ = stats_and_totals
    bad = [dict(stats[0])]
    bad[0]["document_share"] = "99.99"
    problems = ag.validate_stats(bad)
    assert any("document_share" in p for p in problems)


def test_validate_catches_count_over_total(stats_and_totals):
    stats, _ = stats_and_totals
    bad = [dict(stats[0])]
    bad[0]["document_count"] = "999"
    problems = ag.validate_stats(bad)
    assert any("total_documents" in p for p in problems)


def test_validate_catches_bad_source_type(stats_and_totals):
    stats, _ = stats_and_totals
    bad = [dict(stats[0])]
    bad[0]["source_type"] = "blog"
    problems = ag.validate_stats(bad)
    assert any("source_type" in p for p in problems)


# ──────────────────────────── 재현성 (C8) ────────────────────────────

def test_aggregate_is_deterministic():
    documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
    keywords = ag.read_csv(FIXTURES / "aggregate_case1_document_keywords.csv")
    first, _ = ag.aggregate(documents, keywords, as_of=AS_OF)
    second, _ = ag.aggregate(documents, keywords, as_of=AS_OF)
    key = lambda rows: [tuple(r[f] for f in ag.STATS_FIELDS) for r in rows]  # noqa: E731
    assert key(first) == key(second)


def test_csv_round_trip(tmp_path, stats_and_totals):
    stats, _ = stats_and_totals
    path = tmp_path / "keyword_stats.csv"
    ag.write_stats(path, stats)
    loaded = ag.read_csv(path)
    assert len(loaded) == len(stats)
    assert list(loaded[0].keys()) == ag.STATS_FIELDS
    assert ag.validate_stats(loaded) == []
