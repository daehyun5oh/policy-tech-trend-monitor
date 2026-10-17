"""config/keyword_meta.csv 검사 (docs/context_indicators.md §3-5)."""
import csv
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = {"AI·모델", "로봇·모빌리티", "반도체·컴퓨팅", "클라우드·인프라", "통신·네트워크",
              "보안·개인정보", "데이터·플랫폼", "블록체인·가상자산", "확장현실", "산업·서비스"}
# 기획안 §2: 혁신성·성숙도·시장성·유망성·영향력을 판정하지 않는다. 설명에 평가·전망 표현을 쓰지 않는다.
EVALUATIVE = ["혁신", "유망", "전망", "중요", "핵심", "주목", "급성장", "미래", "필수", "획기", "차세대 핵심", "각광"]


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class KeywordMetaTest(unittest.TestCase):
    def setUp(self):
        self.meta = read(ROOT / "config" / "keyword_meta.csv")
        self.keywords = {}
        for r in read(ROOT / "config" / "keywords.csv"):
            self.keywords.setdefault(r["keyword_id"], r["keyword"])

    def test_covers_every_dictionary_keyword_exactly_once(self):
        ids = [r["keyword_id"] for r in self.meta]
        self.assertEqual(len(ids), len(set(ids)), "keyword_id 중복")
        self.assertEqual(set(ids), set(self.keywords))

    def test_categories_are_from_the_allowed_list(self):
        for r in self.meta:
            self.assertIn(r["category"], CATEGORIES, r["keyword_id"])

    def test_descriptions_are_present_short_and_neutral(self):
        for r in self.meta:
            d = r["description"].strip()
            self.assertTrue(d, r["keyword_id"])
            self.assertLessEqual(len(d), 120, r["keyword_id"])
            for word in EVALUATIVE:
                self.assertNotIn(word, d, "%s: 평가·전망 표현 '%s'" % (r["keyword_id"], word))

    def test_single_meta_version_and_source_recorded(self):
        self.assertEqual({r["meta_version"] for r in self.meta}, {"m1"})
        self.assertTrue(all(r["description_source"].strip() for r in self.meta))


if __name__ == "__main__":
    unittest.main()
