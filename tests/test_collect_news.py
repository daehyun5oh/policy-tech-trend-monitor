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
        self.assertEqual(rows[0]["analysis_scope"], "title_summary")
        self.assertEqual(list(texts), [rows[0]["document_id"]])

    def test_published_date_uses_kst(self):
        self.assertEqual(cn.parse_date("2026-10-01T20:00:00+00:00").isoformat(), "2026-10-02")
        self.assertIsNone(cn.parse_date("어제"))

    def test_url_normalization_and_stable_ids(self):
        a = cn.normalize_url("HTTPS://Example.com:443/a/?utm_source=x&b=2&a=1#top")
        self.assertEqual(a, "https://example.com/a?a=1&b=2")
        self.assertEqual(cn.make_document_id("A사", "", "https://x.test/p"), cn.make_document_id("A사", "", "https://x.test/p/"))

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


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>t</title>
<item><title><![CDATA[AI 반도체 투자]]></title><link>https://a.test/1</link>
<pubDate>Sat, 17 Oct 2026 10:49:18 +0900</pubDate><description><![CDATA[<p>클라우드 &amp; 보안</p>]]></description></item>
<item><title>시간대 없는 날짜</title><link>https://a.test/2</link><pubDate>2026-10-15 23:59:00</pubDate><description>요약</description></item>
<item><title>다른 출처의 같은 주소</title><link>https://a.test/1/?utm_source=x</link><pubDate>2026-10-16 09:00:00</pubDate><description>다름</description></item>
</channel></rss>"""


class RssFeedTest(unittest.TestCase):
    def test_parse_rss_items(self):
        items = cn.parse_feed(RSS.encode("utf-8"))
        self.assertEqual([i["url"] for i in items], ["https://a.test/1", "https://a.test/2", "https://a.test/1/?utm_source=x"])
        self.assertEqual(items[0]["text"], "클라우드 & 보안")

    def test_date_formats(self):
        self.assertEqual(cn.parse_date("Sat, 17 Oct 2026 10:49:18 +0900").isoformat(), "2026-10-17")
        self.assertEqual(cn.parse_date("2026-10-15 23:59:00").isoformat(), "2026-10-15")
        # RFC 822 의 UTC 시각은 한국 표준시 날짜로 바뀐다.
        self.assertEqual(cn.parse_date("Sat, 17 Oct 2026 20:00:00 +0000").isoformat(), "2026-10-18")

    def test_duplicate_across_sources_and_same_id_in_different_sources(self):
        a = [dict(i, source_name="A사", source_document_id="1") for i in cn.parse_feed(RSS.encode("utf-8"))[:1]]
        b = [dict(i, source_name="B사", source_document_id="1") for i in cn.parse_feed(RSS.encode("utf-8"))[2:]]
        rows, _ = cn.build_documents(a + b, NOW)
        # 원문 ID가 같아도 출처가 다르면 별개지만, 정규화한 URL이 같으면 중복이다.
        self.assertNotEqual(rows[0]["document_id"], rows[1]["document_id"])
        self.assertEqual((rows[1]["status"], rows[1]["exclusion_reason"], rows[1]["duplicate_of"]),
                         ("excluded", "duplicate", rows[0]["document_id"]))

    def test_load_news_sources_filters_by_status_and_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.csv"
            lines = [
                "source_id,source_type,source_name,access_method,list_url,access_check_status",
                "n1,news,뉴스1,rss,https://a.test/rss,terms_ok",
                "n2,news,뉴스2,rss,https://b.test/rss,unverified",
                "n3,news,뉴스3,html_list,https://c.test/,terms_ok",
                "p1,policy,정책1,rss,https://d.test/rss,terms_ok",
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual([s["source_id"] for s in cn.load_news_sources(path)], ["n1"])
            with self.assertRaises(ValueError):
                cn.load_news_sources(path, ["n2"])

    def test_one_failing_source_does_not_stop_the_others(self):
        sources = [{"source_id": "n1", "source_name": "뉴스1", "list_url": "https://a.test/rss"},
                   {"source_id": "n2", "source_name": "뉴스2", "list_url": "https://b.test/rss"},
                   {"source_id": "n3", "source_name": "뉴스3", "list_url": "https://c.test/rss"}]

        def fake_fetch(url):
            if "b.test" in url:
                raise OSError("연결 실패")
            if "c.test" in url:
                return b"<rss><broken"
            return RSS.encode("utf-8")

        items, log = cn.collect_rss(sources, fetch=fake_fetch, gap=0)
        self.assertEqual([e["result"] for e in log], ["ok", "failed", "failed"])
        self.assertEqual([e.get("failure_reason") for e in log], [None, "fetch_error", "parse_error"])
        self.assertEqual(len(items), 3)
        self.assertTrue(all(i["source_name"] == "뉴스1" for i in items))


class AccumulationTest(unittest.TestCase):
    def items(self, *ids):
        return [{"title": "기사 %s" % i, "url": "https://a.test/%s" % i, "source_document_id": "", "published": "2026-10-1%s" % i,
                 "text": "요약", "full_text": False, "source_name": "A사"} for i in ids]

    def test_new_items_are_added_and_old_ones_keep_their_first_collection_time(self):
        first, added = cn.merge_raw_items([], self.items(1, 2), "2026-10-17T09:00:00+09:00")
        self.assertEqual(added, 2)
        second, added = cn.merge_raw_items(first, self.items(2, 3), "2026-10-18T09:00:00+09:00")
        self.assertEqual(added, 1)
        self.assertEqual([i["collected_at"][:10] for i in second], ["2026-10-17", "2026-10-17", "2026-10-18"])

    def test_documents_are_rebuilt_from_the_whole_store_with_first_seen_time(self):
        merged, _ = cn.merge_raw_items([], self.items(1), "2026-10-17T09:00:00+09:00")
        merged, _ = cn.merge_raw_items(merged, self.items(2), "2026-10-18T09:00:00+09:00")
        rows, _ = cn.build_documents(merged, "2026-10-18T09:00:00+09:00")
        self.assertEqual([r["collected_at"][:10] for r in rows], ["2026-10-17", "2026-10-18"])

    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw" / "news_items.csv"
            merged, _ = cn.merge_raw_items([], self.items(1, 2), "2026-10-17T09:00:00+09:00")
            cn.save_raw_items(path, merged)
            self.assertEqual(cn.load_raw_items(path), merged)

    def test_policy_rows_in_documents_csv_survive_a_news_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fields = cn.DOCUMENT_COLUMNS
            with open(out / "documents.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
                w.writeheader()
                w.writerow(dict.fromkeys(fields, "") | {"document_id": "policy-x", "source_type": "policy", "title": "정책 문서"})
                w.writerow(dict.fromkeys(fields, "") | {"document_id": "news-old", "source_type": "news", "title": "옛 뉴스"})
            rows, texts = cn.build_documents(self.items(1), NOW)
            cn.write_outputs(out, rows, texts, {})
            with open(out / "documents.csv", encoding="utf-8", newline="") as f:
                got = [r["document_id"] for r in csv.DictReader(f)]
            self.assertIn("policy-x", got)
            self.assertNotIn("news-old", got)  # 뉴스 행은 이번 결과로 다시 만든다


class TextCharsTest(unittest.TestCase):
    def test_text_chars_counts_body_not_title_and_blank_for_excluded(self):
        items = [
            {"title": "제목은 세지 않는다", "url": "https://a.test/1", "source_document_id": "", "published": "2026-10-10",
             "text": "요약 열 글자입니다", "full_text": False, "source_name": "A사"},
            {"title": "요약 없는 기사", "url": "https://a.test/2", "source_document_id": "", "published": "2026-10-10",
             "text": "", "full_text": False, "source_name": "A사"},
            {"title": "기간 밖", "url": "https://a.test/3", "source_document_id": "", "published": "2025-01-01",
             "text": "내용", "full_text": False, "source_name": "A사"},
            {"title": "같은 글", "url": "https://a.test/1/", "source_document_id": "", "published": "2026-10-10",
             "text": "요약 열 글자입니다", "full_text": False, "source_name": "A사"},
        ]
        rows, _ = cn.build_documents(items, NOW, cn.parse_date("2026-10-01"), cn.parse_date("2026-10-31"))
        self.assertEqual([(r["status"], r["analysis_scope"], r["text_chars"]) for r in rows], [
            ("analyzed", "title_summary", str(len("요약 열 글자입니다"))),
            ("analyzed", "title", "0"),
            ("excluded", "title_summary", ""),   # 기간 밖
            ("excluded", "title_summary", ""),   # 중복
        ])

    def test_optional_column_is_written_and_kept_for_other_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fields = cn.ALL_COLUMNS
            with open(out / "documents.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
                w.writeheader()
                w.writerow({k: "" for k in fields} | {"document_id": "policy-x", "source_type": "policy", "text_chars": "98"})
            rows, texts = cn.build_documents([], NOW)
            cn.write_outputs(out, rows, texts, {})
            with open(out / "documents.csv", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                got = list(reader)
            self.assertEqual(reader.fieldnames, cn.DOCUMENT_COLUMNS + ["text_chars"])
            self.assertEqual([(r["document_id"], r["text_chars"]) for r in got], [("policy-x", "98")])


if __name__ == "__main__":
    unittest.main()
