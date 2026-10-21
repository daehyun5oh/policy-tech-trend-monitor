"""공개용 데모(src/build_demo.py)가 실제 자료를 담지 않고 같은 입력에서 같은 결과를 내는지 검사한다."""
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import build_demo as bd  # noqa: E402

DICT = ROOT / "config" / "keywords.csv"


class DemoDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = bd.make_documents(DICT)

    def test_same_seed_gives_the_same_documents(self):
        self.assertEqual(self.rows, bd.make_documents(DICT))

    def test_every_url_is_a_fake_sample_address_and_every_source_is_a_made_up_name(self):
        self.assertTrue(all(r["original_url"].startswith("https://sample.example/") for r in self.rows))
        names = {s[0] for s in bd.SOURCES}
        self.assertEqual({r["source_name"] for r in self.rows}, names)
        self.assertTrue(all(n.startswith("샘플") for n in names))

    def test_dates_stay_inside_the_demo_period_and_cover_every_country(self):
        days = {r["published_date"] for r in self.rows}
        self.assertEqual((min(days), max(days)), (bd.FIRST.isoformat(), bd.LAST.isoformat()))
        by = {r["source_name"] for r in self.rows if r["source_type"] == "policy"}
        self.assertEqual(by, {"샘플 한국 부처", "샘플 미국 기관", "샘플 중국 기관"})

    def test_ids_are_unique_and_rows_are_analyzed_title_only_documents(self):
        ids = [r["document_id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r["status"] == "analyzed" and r["analysis_scope"] == "title" for r in self.rows))

    def test_the_limited_source_is_marked_so_its_titles_are_hidden_in_the_demo_too(self):
        redis = {s[0]: s[4] for s in bd.SOURCES}
        self.assertEqual(redis["샘플 중국 기관"], "local_only_no_publish")


if __name__ == "__main__":
    unittest.main()
