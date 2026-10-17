import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import collect_news as cn  # noqa: E402
import validate_csv as vc  # noqa: E402

ATOM = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
<entry><title><![CDATA[인공지능 반도체]]></title>
<link rel='alternate' href='https://news.hada.io/topic?id=1'/>
<id>https://news.hada.io/topic?id=1</id>
<published>2026-10-02T23:30:00+09:00</published>
<content type='html'><![CDATA[<ul><li>AI &amp; 클라우드</li></ul>]]></content></entry>
<entry><title>같은 글</title>
<link rel='alternate' href='https://news.hada.io/topic?id=1'/>
<id>https://news.hada.io/topic?id=1</id>
<published>2026-10-03T00:00:00+09:00</published></entry>
<entry><title>기간 밖</title>
<link rel='alternate' href='https://news.hada.io/topic?id=2'/>
<id>https://news.hada.io/topic?id=2</id>
<published>2026-09-01T00:00:00+09:00</published></entry>
<entry><title>날짜 없음</title>
<link rel='alternate' href='https://news.hada.io/topic?id=3'/>
<id>https://news.hada.io/topic?id=3</id></entry>
</feed>"""
NOW = "2026-10-17T12:00:00+09:00"


class CollectNewsTest(unittest.TestCase):
    def test_parse_atom(self):
        items = cn.parse_atom(ATOM.encode("utf-8"))
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["source_document_id"], "1")
        self.assertEqual(items[0]["text"], "AI & 클라우드")

    def test_status_and_exclusion_reasons(self):
        items = cn.parse_atom(ATOM.encode("utf-8"))
        rows, texts = cn.build_documents(items, NOW, cn.parse_date("2026-10-01"), cn.parse_date("2026-10-31"))
        got = [(r["status"], r["exclusion_reason"]) for r in rows]
        self.assertEqual(got, [("analyzed", ""), ("excluded", "duplicate"),
                               ("excluded", "out_of_period"), ("excluded", "no_published_date")])
        self.assertEqual(rows[1]["duplicate_of"], rows[0]["document_id"])
        self.assertEqual(rows[0]["analysis_scope"], "summary")
        self.assertEqual(list(texts), [rows[0]["document_id"]])

    def test_published_date_uses_kst(self):
        self.assertEqual(cn.parse_date("2026-10-01T20:00:00+00:00").isoformat(), "2026-10-02")
        self.assertIsNone(cn.parse_date("어제"))

    def test_url_normalization_and_stable_ids(self):
        a = cn.normalize_url("HTTPS://Example.com:443/a/?utm_source=x&b=2&a=1#top")
        self.assertEqual(a, "https://example.com/a?a=1&b=2")
        self.assertEqual(cn.make_document_id("", "https://x.test/p"), cn.make_document_id("", "https://x.test/p/"))

    def test_output_passes_csv_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cn.main(["csv", str(ROOT / "data/sample/geeknews_2026-09_input.csv"),
                                      "--from", "2026-09-01", "--to", "2026-09-30", "--out-dir", tmp]), 0)
            report = vc.run(documents=Path(tmp) / "documents.csv")
            self.assertEqual(report.errors, [])

    def test_missing_input_is_logged_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cn.main(["csv", str(Path(tmp) / "none.csv"), "--out-dir", tmp]), 2)
            self.assertTrue((Path(tmp) / "collect_log.json").exists())


if __name__ == "__main__":
    unittest.main()
