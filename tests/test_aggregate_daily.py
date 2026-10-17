"""일간 집계 검증 (이슈 #13 후속, A의 이슈 #26 요청)

기대값을 새로 만들지 않는다. `main` 에 있는 **A가 손으로 계산한 정답**
(`tests/fixtures/expected/keyword_stats.csv` 의 `period_type=daily` 18행)을
그대로 기준으로 쓴다 (CLAUDE.md 규칙 1).

A의 정답 파일에는 `is_partial_period` 열이 없다. 그 열을 넣을지는 합의 전이므로
(PR #42) 두 쪽에 공통으로 있는 열만 대조한다.

표준 라이브러리 `unittest` 로 쓴다. 네트워크에 접속하지 않는다.
"""

from __future__ import annotations

import csv
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "src"))

import aggregate as ag  # noqa: E402

# A의 정답은 2026-09-24 ~ 2026-10-05 자료로 만들어졌다. 부분 기간 판정이
# 실행일에 따라 흔들리지 않도록 기준일을 고정한다.
AS_OF = date(2026, 10, 31)

# 두 쪽에 공통으로 있는 열만 대조한다
COMPARED = ("period_type", "period_start", "period_end", "source_type",
            "keyword_id", "keyword", "document_count", "occurrence_count",
            "total_documents", "document_share", "rank")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def key_of(row: dict) -> tuple:
    return (row["period_type"], str(row["period_start"]), row["source_type"],
            row["keyword_id"])


def comparable(row: dict) -> dict:
    return {field: str(row[field]) for field in COMPARED}


class DayBoundsTest(unittest.TestCase):

    def test_day_bounds_is_one_day(self):
        self.assertEqual(ag.day_bounds(date(2026, 10, 9)),
                         (date(2026, 10, 9), date(2026, 10, 9)))

    def test_period_of_daily(self):
        self.assertEqual(ag.period_of(date(2026, 10, 9), "daily"),
                         (date(2026, 10, 9), date(2026, 10, 9)))

    def test_daily_is_in_default_period_types(self):
        """대시보드가 기본 실행 결과에서 일간 보기를 찾을 수 있어야 한다."""
        self.assertIn("daily", ag.PERIOD_TYPES)

    def test_unknown_period_type_still_raises(self):
        with self.assertRaises(ValueError):
            ag.period_of(date(2026, 10, 9), "yearly")

    def test_validate_accepts_daily_rows(self):
        stats, _ = ag.aggregate(read_csv(FIXTURES / "documents.csv"),
                                read_csv(FIXTURES / "expected" / "document_keywords.csv"),
                                period_types=("daily",), as_of=AS_OF)
        self.assertEqual(ag.validate_stats(stats), [])


class AgainstHandComputedAnswerTest(unittest.TestCase):
    """A가 손으로 계산한 일간 18행과 대조한다."""

    @classmethod
    def setUpClass(cls):
        cls.expected = [r for r in read_csv(FIXTURES / "expected" / "keyword_stats.csv")
                        if r["period_type"] == "daily"]
        cls.actual, cls.totals = ag.aggregate(
            read_csv(FIXTURES / "documents.csv"),
            read_csv(FIXTURES / "expected" / "document_keywords.csv"),
            period_types=("daily",), as_of=AS_OF)

    def test_answer_file_has_eighteen_daily_rows(self):
        """기준이 바뀌면 이 테스트부터 깨져야 한다."""
        self.assertEqual(len(self.expected), 18)

    def test_row_count_matches(self):
        self.assertEqual(len(self.actual), len(self.expected))

    def test_no_missing_or_extra_rows(self):
        mine = {key_of(r) for r in self.actual}
        theirs = {key_of(r) for r in self.expected}
        self.assertEqual(theirs - mine, set(), "정답에 있는 행이 빠졌다")
        self.assertEqual(mine - theirs, set(), "정답에 없는 행을 만들었다")

    def test_every_value_matches(self):
        mine = {key_of(r): comparable(r) for r in self.actual}
        for row in self.expected:
            with self.subTest(row=key_of(row)):
                self.assertEqual(mine[key_of(row)], comparable(row))

    def test_period_start_equals_period_end(self):
        for row in self.actual:
            self.assertEqual(row["period_start"], row["period_end"])

    def test_all_counts_union_not_sum(self):
        """통합(all)은 뉴스와 정책을 더하지 않고 문서 ID 합집합으로 센다."""
        by_key = {(r["period_start"], r["source_type"]): int(r["total_documents"])
                  for r in self.totals}
        for (start, stype), total in by_key.items():
            if stype != "all":
                continue
            parts = sum(by_key.get((start, t), 0) for t in ("news", "policy"))
            self.assertLessEqual(total, parts,
                                 f"{start} 통합 문서 수가 유형별 합보다 크다")


class DailyBoundaryTest(unittest.TestCase):
    """하루 경계. 자정을 넘는 문서가 섞이지 않아야 한다."""

    def setUp(self):
        self.documents = [
            {"document_id": "D1", "source_type": "news", "status": "analyzed",
             "published_date": "2026-10-09"},
            {"document_id": "D2", "source_type": "news", "status": "analyzed",
             "published_date": "2026-10-10"},
            {"document_id": "D3", "source_type": "policy", "status": "excluded",
             "published_date": "2026-10-09"},
            {"document_id": "D4", "source_type": "policy", "status": "analyzed",
             "published_date": ""},
        ]
        self.keywords = [
            {"document_id": "D1", "keyword_id": "K001", "keyword": "인공지능",
             "occurrences": "2"},
            {"document_id": "D2", "keyword_id": "K001", "keyword": "인공지능",
             "occurrences": "1"},
            {"document_id": "D3", "keyword_id": "K001", "keyword": "인공지능",
             "occurrences": "9"},
        ]

    def run_daily(self, as_of=AS_OF):
        return ag.aggregate(self.documents, self.keywords,
                            period_types=("daily",), as_of=as_of)

    def test_each_day_is_counted_separately(self):
        stats, _ = self.run_daily()
        news = [r for r in stats if r["source_type"] == "news"]
        self.assertEqual(sorted(r["period_start"] for r in news),
                         ["2026-10-09", "2026-10-10"])
        for row in news:
            self.assertEqual(row["document_count"], 1)
            self.assertEqual(row["total_documents"], 1)

    def test_excluded_document_is_not_counted(self):
        stats, _ = self.run_daily()
        self.assertEqual([r for r in stats if r["source_type"] == "policy"], [])

    def test_document_without_date_is_not_counted(self):
        """발행일 없는 문서를 수집일로 대체하지 않는다 (CLAUDE.md 규칙 9)."""
        stats, _ = self.run_daily()
        self.assertTrue(all(r["period_start"] in ("2026-10-09", "2026-10-10")
                            for r in stats))

    def test_occurrence_count_is_per_day(self):
        stats, _ = self.run_daily()
        by_day = {r["period_start"]: r["occurrence_count"] for r in stats
                  if r["source_type"] == "news"}
        self.assertEqual(by_day, {"2026-10-09": 2, "2026-10-10": 1})

    def test_today_is_partial_period(self):
        """오늘자 하루는 자료가 덜 모였을 수 있다."""
        stats, _ = self.run_daily(as_of=date(2026, 10, 10))
        flags = {r["period_start"]: r["is_partial_period"] for r in stats}
        self.assertEqual(flags["2026-10-10"], "yes")
        self.assertEqual(flags["2026-10-09"], "no")

    def test_same_input_twice_gives_same_result(self):
        first, _ = self.run_daily()
        second, _ = self.run_daily()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
