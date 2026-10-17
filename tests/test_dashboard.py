"""dashboard/app.py 테스트 (이슈 #16, #17). 실행: python -m unittest discover -s tests -v"""
import csv
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("dashboard_app", ROOT / "dashboard" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

FIX = ROOT / "tests" / "fixtures"
DOCS = FIX / "documents.csv"
KEYWORDS = FIX / "expected" / "document_keywords.csv"
STATS = FIX / "expected" / "keyword_stats.csv"


def model(stats_filter=None, docs_edit=None):
    docs = app.read_csv(DOCS)
    stats = app.read_csv(STATS)
    if stats_filter:
        stats = [r for r in stats if stats_filter(r)]
    if docs_edit:
        docs_edit(docs)
    return app.build_model(docs, app.read_csv(KEYWORDS), stats, generated_at="2026-10-17T00:00:00+09:00")


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.m = model()

    def test_periods_come_from_stats_and_documents(self):
        self.assertEqual([p["start"] for p in self.m["periods"]["weekly"]], ["2026-09-21", "2026-09-28", "2026-10-05"])
        self.assertEqual([p["start"] for p in self.m["periods"]["monthly"]], ["2026-09-01", "2026-10-01"])

    def test_partial_period_is_judged_by_latest_collection_date(self):
        self.assertEqual(self.m["latestCollected"], "2026-10-12")
        partial = {p["start"]: p["partial"] for p in self.m["periods"]["weekly"] + self.m["periods"]["monthly"]}
        self.assertFalse(partial["2026-10-05"])  # 10-11 <= 10-12
        self.assertTrue(partial["2026-10-01"])   # 10-31 > 10-12

    def test_tie_view_keeps_common_rank_and_name_order(self):
        v = self.m["views"]["weekly|2026-09-28|all"]
        self.assertEqual(v["analyzed"], 4)
        self.assertEqual([r["kw"] for r in v["stats"]], ["생성형 AI", "양자컴퓨팅", "인공지능", "클라우드"])
        self.assertEqual({r["rank"] for r in v["stats"]}, {"1"})
        self.assertEqual(v["csvTotal"], 4)

    def test_empty_period_has_no_documents_and_no_rows(self):
        for key in ("weekly|2026-10-05|news", "weekly|2026-09-21|policy"):
            v = self.m["views"][key]
            self.assertEqual((v["analyzed"], v["stats"], v["csvTotal"]), (0, [], None))

    def test_documents_without_keywords_still_count(self):
        v = self.m["views"]["monthly|2026-10-01|news"]  # F005(키워드 있음) + F006(키워드 없음)
        self.assertEqual((v["analyzed"], v["csvTotal"]), (2, 2))

    def test_excluded_and_failed_are_reported_separately(self):
        self.assertEqual(self.m["views"]["weekly|2026-09-28|news"]["excluded"], 1)  # F007 (중복)
        self.assertEqual(self.m["overall"]["failed"], {"news": 0, "policy": 1})       # F011
        self.assertEqual(self.m["overall"]["excludedUndated"], {"news": 0, "policy": 1})  # F009

    def test_evidence_lists_documents_newest_first(self):
        v = self.m["views"]["monthly|2026-09-01|all"]
        ids = [self.m["docs"][i]["t"] for i in v["ev"]["K001"]]
        self.assertEqual(ids, ["생성형 AI 활용 가이드라인과 AI 윤리 원칙 발표", "AI와 AI 규제, 인공지능 기본법 논의",
                               "인공지능 반도체 투자 확대", "산업 동향 브리핑"])

    def test_evidence_respects_source_type_filter(self):
        v = self.m["views"]["monthly|2026-09-01|policy"]
        self.assertEqual(len(v["ev"]["K001"]), 1)

    def test_period_with_documents_but_no_stats_rows_is_kept(self):
        m = model(stats_filter=lambda r: not (r["period_type"] == "weekly" and r["period_start"] == "2026-09-28" and r["source_type"] == "news"))
        v = m["views"]["weekly|2026-09-28|news"]
        self.assertEqual((v["analyzed"], v["stats"], v["csvTotal"]), (2, [], None))

    def test_csv_total_mismatch_is_exposed(self):
        def edit(rows):
            next(r for r in rows if r["document_id"] == "F006")["status"] = "excluded"
            next(r for r in rows if r["document_id"] == "F006")["exclusion_reason"] = "test"
        m = model(docs_edit=edit)
        v = m["views"]["weekly|2026-09-28|news"]
        self.assertNotEqual(v["csvTotal"], v["analyzed"])

    def test_non_http_url_is_dropped(self):
        def edit(rows):
            next(r for r in rows if r["document_id"] == "F001")["original_url"] = "javascript:alert(1)"
        m = model(docs_edit=edit)
        titles = {d["t"]: d["u"] for d in m["docs"]}
        self.assertEqual(titles["인공지능 반도체 투자 확대"], "")


class RenderTest(unittest.TestCase):
    def embedded(self, page):
        m = re.search(r'<script type="application/json" id="model">(.*?)</script>', page, re.S)
        return json.loads(m.group(1))

    def test_hostile_strings_cannot_break_out_of_the_data_script(self):
        def edit(rows):
            r = next(x for x in rows if x["document_id"] == "F001")
            r["title"] = '</script><img src=x onerror=alert(1)> __DATA__ __TITLE__'
        m = model(docs_edit=edit)
        m["title"] = '<b>제목</b> & "따옴표"'
        page = app.render_html(m)
        self.assertNotIn("</script><img", page)
        self.assertNotIn("<b>제목</b>", page)
        self.assertIn("&lt;b&gt;제목&lt;/b&gt;", page)
        self.assertEqual(page.count("</script>"), 2)  # 데이터용 하나, 코드용 하나
        self.assertEqual(self.embedded(page), json.loads(json.dumps(m)))

    def test_embedded_data_round_trips(self):
        m = model()
        self.assertEqual(self.embedded(app.render_html(m)), json.loads(json.dumps(m)))

    def test_page_has_no_external_resources(self):
        page = app.render_html(model())
        self.assertNotRegex(page, r'(?:src|href)="https?://')
        self.assertNotIn("@import", page)


class CommandLineTest(unittest.TestCase):
    ARGS = ["--documents", str(DOCS), "--keywords", str(KEYWORDS), "--stats", str(STATS)]

    def test_builds_file_from_valid_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d" / "dashboard.html"
            self.assertEqual(app.main(self.ARGS + ["--out", str(out)]), 0)
            page = out.read_text(encoding="utf-8")
            self.assertIn("기술키워드 동향 모니터", page)
            self.assertNotIn("__DATA__", page)

    def test_refuses_invalid_csv_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "keyword_stats.csv"
            rows = app.read_csv(STATS)
            rows[0]["rank"] = "9"
            with open(bad, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
            out = Path(tmp) / "dashboard.html"
            code = app.main(["--documents", str(DOCS), "--keywords", str(KEYWORDS), "--stats", str(bad), "--out", str(out)])
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())

    def test_missing_file_returns_2(self):
        self.assertEqual(app.main(["--dir", str(Path(tempfile.gettempdir()) / "no-such-dir-xyz")]), 2)


class PartialColumnTest(unittest.TestCase):
    def test_is_partial_period_column_overrides_collection_date(self):
        docs, stats = app.read_csv(DOCS), app.read_csv(STATS)
        for r in stats:
            # 같은 기간(2026-10 월간)은 수집 종료일 기준으로는 부분 기간이지만 열이 no라고 말한다.
            r["is_partial_period"] = "no" if (r["period_type"], r["period_start"]) == ("monthly", "2026-10-01") else ""
        m = app.build_model(docs, app.read_csv(KEYWORDS), stats, generated_at="2026-10-17T00:00:00+09:00")
        partial = {p["start"]: p["partial"] for p in m["periods"]["monthly"]}
        self.assertFalse(partial["2026-10-01"])


if __name__ == "__main__":
    unittest.main()
