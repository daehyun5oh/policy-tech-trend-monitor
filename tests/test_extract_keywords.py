"""extract_keywords.py 테스트 (이슈 #11, #14). 실행: python -m unittest discover -s tests -v"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import extract_keywords as ek  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def kinds(result):
    """analyze_text 결과를 {keyword_id: 출현 횟수}로 단순화."""
    return {kid: occ for kid, occ, _ in result}


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.dic = ek.load_dictionary(FIX / "keywords_fixture.csv")

    def test_output_matches_hand_written_expectation(self):
        docs = ek.read_documents(FIX / "documents.csv")
        rows, errors, analyzed = ek.extract_documents(docs, self.dic, FIX / "texts")
        self.assertEqual(errors, [])
        self.assertEqual(analyzed, 8)  # analyzed 상태 문서만 (중복 제외·날짜 없음·수집 실패 문서는 제외)
        self.assertEqual(rows, read_csv(FIX / "expected" / "document_keywords.csv"))

    def test_command_line_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            outs = []
            for name in ("a.csv", "b.csv"):
                out = Path(tmp) / name
                code = ek.main(["--documents", str(FIX / "documents.csv"), "--dictionary",
                                str(FIX / "keywords_fixture.csv"), "--text-dir", str(FIX / "texts"),
                                "--out", str(out)])
                self.assertEqual(code, 0)
                outs.append(out.read_bytes())
            self.assertEqual(outs[0], outs[1])


class MatchingRuleTest(unittest.TestCase):
    def setUp(self):
        self.dic = ek.load_dictionary(FIX / "keywords_fixture.csv")

    def analyze(self, text):
        return kinds(ek.analyze_text(text, self.dic))

    def test_alias_is_normalized_to_one_keyword(self):
        self.assertEqual(self.analyze("AI와 인공지능"), {"K001": 2})

    def test_longest_match_does_not_double_count_inner_term(self):
        self.assertEqual(self.analyze("생성형 AI"), {"K002": 1})
        self.assertEqual(self.analyze("생성형 AI와 AI"), {"K001": 1, "K002": 1})

    def test_ascii_boundary(self):
        self.assertEqual(self.analyze("MAIL AI2 AIR"), {})
        self.assertEqual(self.analyze("AI-powered (AI)"), {"K001": 2})

    def test_plural_s_is_allowed_after_latin_terms(self):
        self.assertEqual(self.analyze("AIs and clouds"), {"K001": 1, "K003": 1})
        self.assertEqual(self.analyze("AIS"), {})

    def test_case_sensitivity_per_term(self):
        self.assertEqual(self.analyze("it is IT"), {"K006": 1})
        self.assertEqual(self.analyze("CLOUD Cloud cloud"), {"K003": 3})

    def test_nfkc_and_whitespace(self):
        self.assertEqual(self.analyze("ＡＩ"), {"K001": 1})
        self.assertEqual(self.analyze("양자\n  컴퓨팅"), {"K005": 1})

    def test_matched_terms_keep_first_appearance_order(self):
        result = ek.analyze_text("cloud 와 클라우드, 다시 cloud", self.dic)
        self.assertEqual(result, [("K003", 3, ["cloud", "클라우드"])])

    def test_segments_are_matched_separately_and_merged(self):
        # 제목 끝과 본문 첫머리가 이어져 '양자 컴퓨팅'이 되는 일치는 만들지 않는다.
        self.assertEqual(ek.analyze_segments(["양자", "컴퓨팅"], self.dic), [])
        self.assertEqual(ek.analyze_segments(["인공지능 AI", "AI와 클라우드"], self.dic),
                         [("K001", 3, ["인공지능", "AI"]), ("K003", 1, ["클라우드"])])


class ErrorHandlingTest(unittest.TestCase):
    def setUp(self):
        self.dic = ek.load_dictionary(FIX / "keywords_fixture.csv")

    def doc(self, **kw):
        base = {"document_id": "X1", "title": "인공지능", "status": "analyzed", "analysis_scope": "title"}
        base.update(kw)
        return base

    def test_missing_text_file_is_reported_and_skipped(self):
        rows, errors, _ = ek.extract_documents([self.doc(analysis_scope="title_body")], self.dic, FIX / "texts")
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)

    def test_unsafe_document_id_never_touches_filesystem(self):
        rows, errors, _ = ek.extract_documents(
            [self.doc(document_id="../secret", analysis_scope="title_summary")], self.dic, FIX / "texts")
        self.assertEqual(rows, [])
        self.assertIn("파일 이름", errors[0])

    def test_invalid_scope_is_reported(self):
        _, errors, _ = ek.extract_documents([self.doc(analysis_scope="full")], self.dic)
        self.assertEqual(len(errors), 1)

    def test_non_analyzed_documents_are_ignored(self):
        rows, errors, analyzed = ek.extract_documents([self.doc(status="excluded")], self.dic)
        self.assertEqual((rows, errors, analyzed), ([], [], 0))


class DictionaryValidationTest(unittest.TestCase):
    HEADER = "keyword_id,keyword,term,case_sensitive,dictionary_version\n"

    def load(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.csv"
            path.write_text(self.HEADER + body, encoding="utf-8")
            return ek.load_dictionary(path)

    def test_valid_dictionary_loads(self):
        d = self.load("K1,반도체,반도체,0,v1\n")
        self.assertEqual((d.version, d.keywords), ("v1", {"K1": "반도체"}))

    def test_shipped_dictionary_follows_the_rules(self):
        d = ek.load_dictionary(ROOT / "config" / "keywords.csv")
        self.assertTrue(d.version)
        self.assertGreater(len(d.keywords), 0)
        self.assertEqual(set(d.keywords), {t.keyword_id for t in d.terms})

    def test_rejects_missing_canonical_term(self):
        with self.assertRaises(ek.DictionaryError):
            self.load("K1,반도체,semiconductor,0,v1\n")

    def test_rejects_term_shared_by_two_keywords(self):
        with self.assertRaises(ek.DictionaryError):
            self.load("K1,AI,AI,1,v1\nK2,인공지능,인공지능,0,v1\nK2,인공지능,ai,0,v1\n")

    def test_rejects_mixed_versions_and_bad_flag(self):
        with self.assertRaises(ek.DictionaryError):
            self.load("K1,AI,AI,1,v1\nK2,반도체,반도체,0,v2\n")
        with self.assertRaises(ek.DictionaryError):
            self.load("K1,AI,AI,yes,v1\n")


if __name__ == "__main__":
    unittest.main()
