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


class DailyAndRedistributionTest(unittest.TestCase):
    def test_daily_periods_are_contiguous_up_to_the_latest_collection_day(self):
        m = model()
        days = [p["start"] for p in m["periods"]["daily"]]
        self.assertEqual(days[0], "2026-09-24")
        self.assertEqual(days[-1], "2026-10-12")  # 마지막 수집일. 문서가 없는 날도 포함
        self.assertEqual(len(days), 19)
        self.assertEqual(len(set(days)), len(days))
        # 문서가 없는 날은 빈 결과로 보인다 (10-03: 분석 문서 없음)
        v = m["views"]["daily|2026-10-03|all"]
        self.assertEqual((v["analyzed"], v["stats"], v["csvTotal"]), (0, [], None))
        # 10-02는 중복 제외 문서만 있어 분석 문서는 0건, 제외는 1건
        v = m["views"]["daily|2026-10-02|news"]
        self.assertEqual((v["analyzed"], v["excluded"]), (0, 1))

    def test_daily_values_match_the_hand_computed_fixture(self):
        v = model()["views"]["daily|2026-09-24|all"]
        self.assertEqual(v["analyzed"], 2)
        self.assertEqual([(r["kw"], r["dc"], r["oc"], r["rank"]) for r in v["stats"]],
                         [("반도체", 2, 3, "1"), ("인공지능", 2, 2, "1")])

    def test_daily_is_hidden_when_stats_have_no_daily_rows(self):
        m = model(stats_filter=lambda r: r["period_type"] != "daily")
        self.assertEqual(m["periods"]["daily"], [])
        self.assertEqual(len(m["periods"]["weekly"]), 3)

    def test_the_day_containing_the_collection_end_is_partial(self):
        partial = {p["start"]: p["partial"] for p in model()["periods"]["daily"]}
        self.assertTrue(partial["2026-10-12"])
        self.assertFalse(partial["2026-10-05"])

    def test_coverage_and_common_range(self):
        m = model()
        self.assertEqual(m["coverage"], {"news": ["2026-09-24", "2026-10-01"], "policy": ["2026-09-28", "2026-10-05"]})
        self.assertEqual(m["commonRange"], ["2026-09-28", "2026-10-01"])

    def test_title_of_no_publish_source_never_reaches_the_page(self):
        docs, stats = app.read_csv(DOCS), app.read_csv(STATS)
        m = app.build_model(docs, app.read_csv(KEYWORDS), stats, generated_at="2026-10-17T00:00:00+09:00",
                            hidden_sources=frozenset({"fixture-policy"}))
        policy = [d for d in m["docs"] if d["y"] == "policy"]
        self.assertTrue(policy)
        self.assertTrue(all(d["t"] == "" and d["h"] == 1 and d["u"].startswith("https://") and d["d"] for d in policy))
        news = [d for d in m["docs"] if d["y"] == "news"]
        self.assertTrue(all(d["t"] and "h" not in d for d in news))
        page = app.render_html(m)
        self.assertNotIn("IT 인프라 반도체 지원", page)
        self.assertIn("인공지능 반도체 투자 확대", page)

    def test_load_hidden_sources_reads_redistribution(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.csv"
            path.write_text("\n".join([
                "source_id,source_name,redistribution",
                "a,공개 출처,metadata_only",
                "b,비공개 출처,local_only_no_publish",
            ]) + "\n", encoding="utf-8")
            self.assertEqual(app.load_hidden_sources(path), frozenset({"비공개 출처"}))
            self.assertEqual(app.load_hidden_sources(Path(tmp) / "none.csv"), frozenset())


CTX = ROOT / "tests" / "fixtures" / "context"


class ContextPanelModelTest(unittest.TestCase):
    """대시보드가 계산한 맥락 값이 손으로 계산한 정답(tests/fixtures/context/expected)과 같은지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.m = app.build_model(app.read_csv(CTX / "documents.csv"), app.read_csv(CTX / "document_keywords.csv"), [],
                                generated_at="2026-10-26T00:00:00+09:00")
        cls.weekly = {p["start"]: p["end"] for p in cls.m["periods"]["weekly"]}

    def each_view(self):
        for start, end in self.weekly.items():
            for t in ("news", "policy", "all"):
                yield start, end, t, self.m["views"]["weekly|%s|%s" % (start, t)]["ctx"]

    def expected(self, name, keys, values):
        out = {}
        for r in app.read_csv(CTX / "expected" / name):
            out[tuple(r[k] for k in keys)] = tuple(r[v] for v in values)
        return out

    def test_weekly_periods_skip_the_week_without_documents(self):
        self.assertEqual(list(self.weekly), ["2026-09-21", "2026-09-28", "2026-10-05", "2026-10-19"])

    def test_co_keywords(self):
        got = {}
        for start, end, t, ctx in self.each_view():
            for kid, c in ctx.items():
                for rid, rname, n, share, rank in c["co"]:
                    got[("weekly", start, end, t, kid, rid)] = (rname, str(n), share, str(rank))
        want = self.expected("keyword_context.csv", ("period_type", "period_start", "period_end", "source_type", "keyword_id", "related_keyword_id"),
                             ("related_keyword", "co_document_count", "co_share", "rank"))
        self.assertEqual(got, want)

    def test_sources(self):
        got = {}
        for start, end, t, ctx in self.each_view():
            for kid, c in ctx.items():
                for src, n, total, share in c["src"]:
                    got[("weekly", start, end, t, kid, src)] = (str(n), str(total), share)
        want = self.expected("keyword_sources.csv", ("period_type", "period_start", "period_end", "source_type", "keyword_id", "source_name"),
                             ("document_count", "source_total_documents", "source_share"))
        self.assertEqual(got, want)

    def test_terms(self):
        got = {}
        for start, end, t, ctx in self.each_view():
            for kid, c in ctx.items():
                for term, n in c["terms"]:
                    got[("weekly", start, end, t, kid, term)] = (str(n),)
        want = self.expected("keyword_terms.csv", ("period_type", "period_start", "period_end", "source_type", "keyword_id", "term"),
                             ("document_count",))
        self.assertEqual(got, want)

    def test_spread_and_streak(self):
        got = {}
        for start, end, t, ctx in self.each_view():
            for kid, c in ctx.items():
                got[("weekly", start, end, t, kid)] = (str(c["sc"]), str(c["tc"]), str(c["st"]))
        want = self.expected("keyword_stats_extra.csv", ("period_type", "period_start", "period_end", "source_type", "keyword_id"),
                             ("source_count", "type_count", "streak"))
        self.assertEqual(got, want)

    def test_meta_is_loaded_and_embedded(self):
        meta = app.load_meta(ROOT / "config" / "keyword_meta.csv")
        self.assertEqual(len(meta), 47)
        self.assertEqual(meta["K001"]["c"], "AI·모델")
        m = app.build_model(app.read_csv(CTX / "documents.csv"), app.read_csv(CTX / "document_keywords.csv"), [], meta=meta)
        self.assertEqual(m["meta"]["K001"]["d"], meta["K001"]["d"])
        self.assertEqual(app.load_meta(ROOT / "none.csv"), {})


class ScopeNoticeTest(unittest.TestCase):
    def test_scope_by_source_counts_each_scope(self):
        rows = [{"source_name": "B사", "analysis_scope": "title"}, {"source_name": "B사", "analysis_scope": "title"},
                {"source_name": "A사", "analysis_scope": "title_body"}, {"source_name": "A사", "analysis_scope": "title_summary"},
                {"source_name": "A사", "analysis_scope": "title_body"}, {"source_name": "C사", "analysis_scope": "unknown"}]
        self.assertEqual(app.scope_by_source(rows), [["A사", 0, 1, 2], ["B사", 2, 0, 0]])

    def test_every_view_carries_the_scope_counts(self):
        m = model()
        v = m["views"]["weekly|2026-09-21|news"]  # F001, F002(title) + F010(title_summary)
        self.assertEqual(v["scope"], [["fixture-news", 2, 1, 0]])
        self.assertEqual(m["views"]["weekly|2026-10-05|news"]["scope"], [])  # 분석 문서 없는 기간
        self.assertEqual(m["views"]["weekly|2026-09-21|all"]["scope"], v["scope"])


if __name__ == "__main__":
    unittest.main()
