"""집계 모듈 검증 (이슈 #13)

완료기준 C4(문서 빈도·출현 횟수·비율·동률 순위·기간 경계)를 고정 fixture의
**수작업 정답**과 대조한다. 네트워크에 접속하지 않는다.

표준 라이브러리 `unittest` 로 쓴다 (`docs/environment.md` 테스트 규칙).

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
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import aggregate as ag  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = date(2026, 10, 17)

WEEK_A = ("2026-10-05", "2026-10-11")
WEEK_B = ("2026-10-12", "2026-10-18")
MONTH = ("2026-10-01", "2026-10-31")

_CACHE = {}


def stats_and_totals():
    """fixture 를 한 번만 집계해 돌려준다 (pytest 의 module fixture 자리)."""
    if "result" not in _CACHE:
        documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
        keywords = ag.read_csv(FIXTURES / "aggregate_case1_document_keywords.csv")
        _CACHE["result"] = ag.aggregate(documents, keywords, as_of=AS_OF)
    return _CACHE["result"]


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


class AggregateCase(unittest.TestCase):
    """fixture 집계 결과를 공유하는 바탕 클래스."""

    @classmethod
    def setUpClass(cls):
        cls.stats, cls.totals = stats_and_totals()


# ──────────────────────────── 기간 경계 ────────────────────────────

class PeriodBoundsTest(unittest.TestCase):

    def test_week_bounds_monday_to_sunday(self):
        self.assertEqual(ag.week_bounds(date(2026, 10, 5)),
                         (date(2026, 10, 5), date(2026, 10, 11)))
        self.assertEqual(ag.week_bounds(date(2026, 10, 11)),
                         (date(2026, 10, 5), date(2026, 10, 11)))
        self.assertEqual(ag.week_bounds(date(2026, 10, 12)),
                         (date(2026, 10, 12), date(2026, 10, 18)))

    def test_month_bounds_calendar_month(self):
        self.assertEqual(ag.month_bounds(date(2026, 10, 17)),
                         (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(ag.month_bounds(date(2026, 2, 3)),
                         (date(2026, 2, 1), date(2026, 2, 28)))

    def test_period_of_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            ag.period_of(date(2026, 10, 17), "daily")


# ──────────────────────────── 집계 대상 ────────────────────────────

class ScopeTest(AggregateCase):

    def test_excluded_documents_are_not_counted(self):
        """제외 문서 2건은 집계에 들어가지 않는다."""
        for row in self.stats:
            self.assertLessEqual(int(row["total_documents"]), 5)

        # 기간 밖 문서(2026-01-02)의 1월 기간 행이 생기지 않아야 한다
        jan = [r for r in self.stats if r["period_start"].startswith("2026-01")]
        self.assertEqual(jan, [])

    def test_total_documents_includes_docs_without_keywords(self):
        """분모에는 키워드가 없는 문서도 들어간다. 여기서는 모든 문서에 키워드가 있다."""
        week_a_all = [t for t in self.totals
                      if t["period_type"] == "weekly"
                      and (t["period_start"], t["period_end"]) == WEEK_A
                      and t["source_type"] == "all"]
        self.assertEqual(week_a_all[0]["total_documents"], 4)


# ──────────────────────────── 문서 빈도·출현 횟수 ────────────────────────────

class CountTest(AggregateCase):

    def test_week_a_policy_counts(self):
        ai = pick(self.stats, WEEK_A, "policy", "kw-ai")
        self.assertEqual(ai["document_count"], 2)
        self.assertEqual(ai["occurrence_count"], 8)          # 5 + 3
        self.assertEqual(ai["total_documents"], 2)
        self.assertEqual(ai["document_share"], "100.00")
        self.assertEqual(ai["rank"], 1)

        quantum = pick(self.stats, WEEK_A, "policy", "kw-quantum")
        self.assertEqual(quantum["document_count"], 1)
        self.assertEqual(quantum["occurrence_count"], 2)
        self.assertEqual(quantum["document_share"], "50.00")
        self.assertEqual(quantum["rank"], 2)

    def test_document_frequency_counts_document_once(self):
        """한 문서에서 여러 번 나와도 문서 빈도는 1건이다."""
        ai = pick(self.stats, WEEK_A, "policy", "kw-ai")
        self.assertEqual(ai["document_count"], 2)
        self.assertLess(ai["document_count"], ai["occurrence_count"])


# ──────────────────────────── 통합(all)은 합집합 ────────────────────────────

class UnionTest(AggregateCase):

    def test_all_is_union_not_sum(self):
        """all 은 뉴스·정책 건수를 더하지 않고 문서 식별자의 합집합으로 센다."""
        policy = pick(self.stats, WEEK_A, "policy", "kw-ai")
        news = pick(self.stats, WEEK_A, "news", "kw-ai")
        merged = pick(self.stats, WEEK_A, "all", "kw-ai")

        self.assertEqual(policy["document_count"], 2)
        self.assertEqual(news["document_count"], 1)
        self.assertEqual(merged["document_count"], 3)        # 합집합 {0001, 0002, news-0001}
        self.assertEqual(merged["total_documents"], 4)
        self.assertEqual(merged["document_share"], "75.00")
        self.assertEqual(merged["occurrence_count"], 10)     # 5 + 3 + 2

    def test_all_total_documents_is_union(self):
        policy = rows_of(self.stats, WEEK_A, "policy")[0]
        news = rows_of(self.stats, WEEK_A, "news")[0]
        merged = rows_of(self.stats, WEEK_A, "all")[0]
        self.assertEqual(policy["total_documents"], 2)
        self.assertEqual(news["total_documents"], 2)
        self.assertEqual(merged["total_documents"], 4)


# ──────────────────────────── 동률 순위 ────────────────────────────

class RankTest(AggregateCase):

    def test_tied_ranks_are_shared(self):
        """A주 뉴스는 세 키워드가 모두 1건 → 공동 1위."""
        rows = rows_of(self.stats, WEEK_A, "news")
        self.assertEqual({r["keyword_id"] for r in rows},
                         {"kw-ai", "kw-quantum", "kw-semi"})
        self.assertTrue(all(r["document_count"] == 1 for r in rows))
        self.assertTrue(all(r["rank"] == 1 for r in rows))

    def test_rank_sequence_with_ties(self):
        """월간 all: 인공지능 4건(1위), 반도체·양자 각 2건(공동 2위)."""
        rows = sorted(rows_of(self.stats, MONTH, "all", "monthly"),
                      key=lambda r: (r["rank"], r["keyword"]))
        self.assertEqual([(r["keyword"], r["document_count"], r["rank"]) for r in rows],
                         [("인공지능", 4, 1), ("반도체", 2, 2), ("양자", 2, 2)])

    def test_ties_sorted_by_keyword_name(self):
        """동률 안에서는 keyword 이름순으로 정렬한다."""
        rows = rows_of(self.stats, WEEK_B, "policy")
        self.assertEqual([r["keyword"] for r in rows], ["반도체", "인공지능"])
        self.assertTrue(all(r["rank"] == 1 for r in rows))


# ──────────────────────────── 비율 반올림 ────────────────────────────

class ShareTest(AggregateCase):

    def test_share_rounds_to_two_decimals(self):
        """월간 정책: 양자 1건 / 분모 3건 → 33.33"""
        quantum = pick(self.stats, MONTH, "policy", "kw-quantum", "monthly")
        self.assertEqual(quantum["document_count"], 1)
        self.assertEqual(quantum["total_documents"], 3)
        self.assertEqual(quantum["document_share"], "33.33")

    def test_share_full_coverage(self):
        ai = pick(self.stats, MONTH, "policy", "kw-ai", "monthly")
        self.assertEqual(ai["document_count"], 3)
        self.assertEqual(ai["document_share"], "100.00")


# ──────────────────────────── 부분 기간 ────────────────────────────

class PartialPeriodTest(AggregateCase):

    def test_partial_period_flag(self):
        """기준일 2026-10-17 이 포함된 기간만 부분 기간이다."""
        self.assertEqual(pick(self.stats, WEEK_A, "all", "kw-ai")["is_partial_period"], "no")
        self.assertEqual(pick(self.stats, WEEK_B, "all", "kw-ai")["is_partial_period"], "yes")
        self.assertEqual(
            pick(self.stats, MONTH, "all", "kw-ai", "monthly")["is_partial_period"], "yes")

    def test_partial_period_changes_with_as_of(self):
        documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
        keywords = ag.read_csv(FIXTURES / "aggregate_case1_document_keywords.csv")
        stats, _ = ag.aggregate(documents, keywords, as_of=date(2026, 12, 1))
        self.assertTrue(all(r["is_partial_period"] == "no" for r in stats))


# ──────────────────────────── 빈 결과 ────────────────────────────

class EmptyResultTest(AggregateCase):

    def test_no_rows_when_type_has_no_documents(self):
        """B주에는 뉴스 문서가 없다. 행을 만들지 않고 실행 기록에만 남긴다."""
        self.assertEqual(rows_of(self.stats, WEEK_B, "news"), [])

        entry = [t for t in self.totals
                 if t["period_type"] == "weekly"
                 and (t["period_start"], t["period_end"]) == WEEK_B
                 and t["source_type"] == "news"]
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0]["total_documents"], 0)

    def test_empty_keywords_yields_no_stats(self):
        """키워드 추출 결과가 없으면 집계 행이 없다. 기간별 문서 수는 남는다."""
        documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
        stats, totals = ag.aggregate(documents, [], as_of=AS_OF)
        self.assertEqual(stats, [])
        self.assertTrue(any(t["total_documents"] == 5 for t in totals))


# ──────────────────────────── 검증 함수 ────────────────────────────

class ValidateStatsTest(AggregateCase):

    def test_validate_passes(self):
        self.assertEqual(ag.validate_stats(self.stats), [])

    def test_validate_catches_share_mismatch(self):
        bad = [dict(self.stats[0])]
        bad[0]["document_share"] = "99.99"
        problems = ag.validate_stats(bad)
        self.assertTrue(any("document_share" in p for p in problems))

    def test_validate_catches_count_over_total(self):
        bad = [dict(self.stats[0])]
        bad[0]["document_count"] = "999"
        problems = ag.validate_stats(bad)
        self.assertTrue(any("total_documents" in p for p in problems))

    def test_validate_catches_bad_source_type(self):
        bad = [dict(self.stats[0])]
        bad[0]["source_type"] = "blog"
        problems = ag.validate_stats(bad)
        self.assertTrue(any("source_type" in p for p in problems))


# ──────────────────────────── 재현성 (C8) ────────────────────────────

class ReproducibilityTest(AggregateCase):

    def test_aggregate_is_deterministic(self):
        documents = ag.read_csv(FIXTURES / "aggregate_case1_documents.csv")
        keywords = ag.read_csv(FIXTURES / "aggregate_case1_document_keywords.csv")
        first, _ = ag.aggregate(documents, keywords, as_of=AS_OF)
        second, _ = ag.aggregate(documents, keywords, as_of=AS_OF)
        key = lambda rows: [tuple(r[f] for f in ag.STATS_FIELDS) for r in rows]  # noqa: E731
        self.assertEqual(key(first), key(second))

    def test_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keyword_stats.csv"
            ag.write_stats(path, self.stats)
            loaded = ag.read_csv(path)
            self.assertEqual(len(loaded), len(self.stats))
            self.assertEqual(list(loaded[0].keys()), ag.STATS_FIELDS)
            self.assertEqual(ag.validate_stats(loaded), [])


if __name__ == "__main__":
    unittest.main()
