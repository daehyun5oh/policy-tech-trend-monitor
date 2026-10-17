"""validate_csv.py 테스트 (이슈 #15). 실행: python -m unittest discover -s tests -v"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import validate_csv as vc  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
SOURCES = {
    "documents": FIX / "documents.csv",
    "keywords": FIX / "expected" / "document_keywords.csv",
    "stats": FIX / "expected" / "keyword_stats.csv",
}


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def row_of(rows, **match):
    return next(r for r in rows if all(r[k] == v for k, v in match.items()))


class ValidatorTestCase(unittest.TestCase):
    def check(self, documents=None, keywords=None, stats=None):
        """fixture를 복사해 mutate 함수를 적용한 뒤 검증하고 오류 메시지 목록을 반환한다."""
        mutations = {"documents": documents, "keywords": keywords, "stats": stats}
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for key, src in SOURCES.items():
                fields, rows = read(src)
                if mutations[key]:
                    mutations[key](rows)
                paths[key] = Path(tmp) / (key + ".csv")
                write(paths[key], fields, rows)
            report = vc.run(str(paths["documents"]), str(paths["keywords"]), str(paths["stats"]))
        return [m for _, _, m in report.errors]

    def assertReported(self, messages, fragment):
        self.assertTrue(any(fragment in m for m in messages), "'%s' 오류가 없음: %s" % (fragment, messages))


class FixtureIsValidTest(ValidatorTestCase):
    def test_fixture_has_no_errors(self):
        self.assertEqual(self.check(), [])

    def test_not_applicable_row_for_empty_denominator_is_valid(self):
        def add(rows):
            rows.append({"period_type": "weekly", "period_start": "2026-09-21", "period_end": "2026-09-27",
                         "source_type": "policy", "keyword_id": "K001", "keyword": "인공지능",
                         "document_count": "0", "occurrence_count": "0", "total_documents": "0",
                         "document_share": "해당 없음", "rank": "해당 없음"})
        self.assertEqual(self.check(stats=add), [])


class DocumentsTest(ValidatorTestCase):
    def test_duplicate_document_id(self):
        self.assertReported(self.check(documents=lambda r: r.append(dict(r[0]))), "document_id 중복")

    def test_missing_required_evidence_of_analyzed_document(self):
        def f(rows):
            row_of(rows, document_id="F001")["title"] = ""
        self.assertReported(self.check(documents=f), "필수값 누락: title")

    def test_invalid_calendar_date(self):
        def f(rows):
            row_of(rows, document_id="F001")["published_date"] = "2026-02-30"
        self.assertReported(self.check(documents=f), "YYYY-MM-DD")

    def test_non_http_url_is_rejected(self):
        def f(rows):
            row_of(rows, document_id="F001")["original_url"] = "javascript:alert(1)"
        self.assertReported(self.check(documents=f), "http(s)")

    def test_duplicate_must_point_to_existing_analyzed_representative(self):
        def missing(rows):
            row_of(rows, document_id="F007")["duplicate_of"] = "NOPE"
        def not_analyzed(rows):
            row_of(rows, document_id="F007")["duplicate_of"] = "F009"
        def itself(rows):
            row_of(rows, document_id="F007")["duplicate_of"] = "F007"
        self.assertReported(self.check(documents=missing), "대표 문서가 없음")
        self.assertReported(self.check(documents=not_analyzed), "분석 완료 상태가 아님")
        self.assertReported(self.check(documents=itself), "자기 자신")

    def test_duplicate_of_on_analyzed_document_and_missing_reason(self):
        def a(rows):
            row_of(rows, document_id="F001")["duplicate_of"] = "F002"
        def b(rows):
            row_of(rows, document_id="F009")["exclusion_reason"] = ""
        self.assertReported(self.check(documents=a), "제외(excluded) 문서에만")
        self.assertReported(self.check(documents=b), "exclusion_reason이 없음")


class KeywordsTest(ValidatorTestCase):
    def test_unknown_document(self):
        def f(rows):
            rows[0]["document_id"] = "NOPE"
        self.assertReported(self.check(keywords=f), "documents.csv에 없는")

    def test_keyword_linked_to_excluded_document(self):
        def f(rows):
            rows.append(dict(rows[0], document_id="F007"))
        self.assertReported(self.check(keywords=f), "분석 완료가 아닌 문서")

    def test_duplicate_pair_bad_json_bad_count_and_mixed_versions(self):
        self.assertReported(self.check(keywords=lambda r: r.append(dict(r[0]))), "중복")
        def bad_json(rows):
            rows[0]["matched_terms"] = "인공지능"
        def bad_count(rows):
            rows[0]["occurrences"] = "0"
        def mixed(rows):
            rows[0]["dictionary_version"] = "other"
        self.assertReported(self.check(keywords=bad_json), "JSON 배열")
        self.assertReported(self.check(keywords=bad_count), "1 이상의 정수")
        self.assertReported(self.check(keywords=mixed), "여러 개")


class StatsTest(ValidatorTestCase):
    def test_week_must_start_on_monday(self):
        def f(rows):
            row_of(rows, period_type="weekly", period_start="2026-09-21")["period_start"] = "2026-09-22"
        self.assertReported(self.check(stats=f), "월요일~일요일")

    def test_month_must_cover_whole_month(self):
        def f(rows):
            row_of(rows, period_type="monthly", period_start="2026-10-01")["period_end"] = "2026-10-30"
        self.assertReported(self.check(stats=f), "1일~말일")

    def test_rank_and_share_must_follow_definitions(self):
        def rank(rows):
            row_of(rows, period_type="weekly", source_type="news", keyword_id="K001", period_start="2026-09-21")["rank"] = "2"
        def share(rows):
            row_of(rows, period_type="weekly", source_type="news", keyword_id="K001", period_start="2026-09-21")["document_share"] = "99.00"
        self.assertReported(self.check(stats=rank), "공동 순위")
        self.assertReported(self.check(stats=share), "document_share")

    def test_zero_denominator_requires_not_applicable(self):
        def f(rows):
            rows.append({"period_type": "weekly", "period_start": "2026-09-21", "period_end": "2026-09-27",
                         "source_type": "policy", "keyword_id": "K001", "keyword": "인공지능",
                         "document_count": "0", "occurrence_count": "0", "total_documents": "0",
                         "document_share": "0.00", "rank": "1"})
        self.assertReported(self.check(stats=f), "해당 없음")

    def test_total_must_agree_within_group_and_union(self):
        def group(rows):
            row_of(rows, period_type="monthly", source_type="news", keyword_id="K001", period_start="2026-09-01")["total_documents"] = "9"
        def union(rows):
            row_of(rows, period_type="monthly", source_type="all", keyword_id="K001", period_start="2026-09-01")["document_count"] = "2"
        self.assertReported(self.check(stats=group), "total_documents가 다름")
        self.assertReported(self.check(stats=union), "통합 문서 빈도")

    def test_duplicate_stats_row(self):
        self.assertReported(self.check(stats=lambda r: r.append(dict(r[0]))), "중복")


class PartialPeriodColumnTest(unittest.TestCase):
    def run_with(self, value):
        fields, rows = read(SOURCES["stats"])
        for r in rows:
            r["is_partial_period"] = value
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stats.csv"
            write(path, fields + ["is_partial_period"], rows)
            return [m for _, _, m in vc.run(stats=str(path)).errors]

    def test_yes_and_no_are_accepted(self):
        self.assertEqual(self.run_with("no"), [])
        self.assertEqual(self.run_with("yes"), [])

    def test_other_values_are_reported(self):
        self.assertTrue(any("is_partial_period" in m for m in self.run_with("true")))


if __name__ == "__main__":
    unittest.main()
