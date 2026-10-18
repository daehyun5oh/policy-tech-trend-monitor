"""대시보드 인사이트 화면(docs/insight_views.md)의 계산 검사.

기대값은 손으로 계산했다. 구현 출력을 복사하지 않았다 (CLAUDE.md 규칙 1).
"""
import csv
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "src"))
import app  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
TYPES = ("all", "news", "policy")


def mkview(analyzed, stats=(), scope=(), cat=None):
    return {"analyzed": analyzed, "stats": [{"id": i, "kw": n, "dc": dc} for i, n, dc in stats],
            "scope": [list(s) for s in scope], "cat": cat or {}}


def weekly(starts, per_type):
    """per_type: {유형: {시작일: view}}. 지정하지 않은 유형·기간은 문서 0건."""
    periods = {"daily": [], "monthly": [], "weekly": [{"start": s, "end": s, "partial": False} for s in starts]}
    views = {}
    for s in starts:
        for t in TYPES:
            views["weekly|%s|%s" % (s, t)] = per_type.get(t, {}).get(s, mkview(0))
    return periods, views


STARTS = ["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26", "2026-02-02", "2026-02-09", "2026-02-16"]


class CompareCountsTest(unittest.TestCase):
    def test_hand_calculated_examples(self):
        # n1=8/20, n0=8/80: p1=40, p0=10, 합친 비율 .16 -> z = .30 / sqrt(.16*.84*.0625) = 3.27
        self.assertEqual(app.compare_counts(8, 20, 8, 80), {"p1": 40.0, "p0": 10.0, "d": 30.0, "z": 3.27})
        # n1=0/20, n0=5/80: p1=0, p0=6.25, 합친 비율 .05 -> z = -.0625 / sqrt(.05*.95*.0625) = -1.15
        self.assertEqual(app.compare_counts(0, 20, 5, 80), {"p1": 0.0, "p0": 6.25, "d": -6.25, "z": -1.15})
        # 뉴스 6/20 대 정책 1/40: 30.00 대 2.50, 차이 27.50, z = .275 / sqrt(.116667*.883333*.075) = 3.13
        self.assertEqual(app.compare_counts(6, 20, 1, 40), {"p1": 30.0, "p0": 2.5, "d": 27.5, "z": 3.13})
        # 분야 안: 3/10 대 2/20: 30 대 10, 차이 20, 합친 비율 1/6 -> z = .20 / sqrt(.166667*.833333*.15) = 1.39
        self.assertEqual(app.compare_counts(3, 10, 2, 20), {"p1": 30.0, "p0": 10.0, "d": 20.0, "z": 1.39})

    def test_z_is_none_when_nobody_or_everybody_has_the_keyword(self):
        self.assertIsNone(app.compare_counts(0, 20, 0, 80)["z"])
        self.assertIsNone(app.compare_counts(20, 20, 80, 80)["z"])


class BuildChangesTest(unittest.TestCase):
    def setUp(self):
        # 주간 7개. 이번 기간=7번째(20건). 문서가 없는 3번째는 건너뛰므로 기준 기간은 6,5,4,2번째(각 20건 = 80건)
        sizes = [20, 20, 0, 20, 20, 20, 20]
        a_counts = [None, 2, None, 2, 2, 2, 8]           # 키워드 A: 기준 2씩(합 8), 이번 8
        c_counts = [None, 4, None, 4, 4, 4, None]        # 키워드 C: 기준 4씩(합 16), 이번 0
        b_counts = [None, 1, None, 1, 1, 2, None]        # 키워드 B: 기준 합 5, 이번 0
        mix_base = [("S1", 10, 0, 0), ("S2", 10, 0, 0)]
        views = {}
        for i, s in enumerate(STARTS):
            stats = []
            for kid, name, arr in (("A", "에이", a_counts), ("B", "비", b_counts), ("C", "씨", c_counts)):
                if arr[i]:
                    stats.append((kid, name, arr[i]))
            scope = [("S1", sizes[i], 0, 0)] if i == 6 else (mix_base if sizes[i] else [])
            views[s] = mkview(sizes[i], stats, scope)
        self.periods, self.views = weekly(STARTS, {"all": views})
        app.build_changes(self.views, self.periods, {"A": "에이", "B": "비", "C": "씨"})
        self.chg = self.views["weekly|2026-02-16|all"]["chg"]

    def test_baseline_skips_empty_periods_and_uses_four_periods(self):
        self.assertTrue(self.chg["ok"])
        self.assertEqual((self.chg["N1"], self.chg["N0"], self.chg["k"]), (20, 80, 4))
        self.assertEqual(self.chg["base"], ["2026-01-12", "2026-02-09"])

    def test_increase_list_has_the_hand_calculated_row(self):
        up = self.chg["up"]
        self.assertEqual([r["id"] for r in up], ["A"])
        self.assertEqual({k: up[0][k] for k in ("n1", "n0", "p1", "p0", "d", "z")},
                         {"n1": 8, "n0": 8, "p1": 40.0, "p0": 10.0, "d": 30.0, "z": 3.27})

    def test_decrease_list_requires_an_expected_count_of_two(self):
        down = self.chg["down"]
        # C: 기준 16/80 -> 이번 기대 건수 4건 이상, p1=0, p0=20, 합친 비율 .16 -> z = -.20 / sqrt(.16*.84*.0625) = -2.18
        self.assertEqual([(r["id"], r["n1"], r["n0"], r["d"], r["z"]) for r in down], [("C", 0, 16, -20.0, -2.18)])
        # B: 기준 5/80 -> 기대 1.25건이라 목록에 올리지 않는다
        self.assertNotIn("B", [r["id"] for r in down])

    def test_source_mix_warning_when_a_source_changed_a_lot(self):
        # 이번 기간은 S1이 100%, 기준 기간은 S1 50% · S2 50%
        self.assertEqual(self.chg["mix"], [{"src": "S1", "cur": 100.0, "base": 50.0}, {"src": "S2", "cur": 0.0, "base": 50.0}])

    def test_not_compared_when_current_period_is_small(self):
        periods, views = weekly(STARTS[:5], {"all": {STARTS[i]: mkview(n) for i, n in enumerate([20, 20, 20, 20, 4])}})
        app.build_changes(views, periods, {})
        self.assertEqual(views["weekly|%s|all" % STARTS[4]]["chg"]["why"], "cur")

    def test_not_compared_when_baseline_is_small(self):
        periods, views = weekly(STARTS[:3], {"all": {STARTS[i]: mkview(n) for i, n in enumerate([6, 6, 10])}})
        app.build_changes(views, periods, {})
        chg = views["weekly|%s|all" % STARTS[2]]["chg"]
        self.assertEqual((chg["ok"], chg["why"], chg["N0"]), (False, "base", 12))
        self.assertNotIn("chg", views["weekly|%s|news" % STARTS[2]])  # 문서 없는 유형은 건너뜀


class BuildGapsTest(unittest.TestCase):
    def test_news_policy_gap_hand_calculated(self):
        s = STARTS[0]
        news = {s: mkview(20, [("A", "에이", 6), ("B", "비", 3)])}
        policy = {s: mkview(40, [("A", "에이", 1)])}
        periods, views = weekly([s], {"news": news, "policy": policy})
        app.build_gaps(views, periods)
        gap = views["weekly|%s|all" % s]["gap"]
        self.assertTrue(gap["ok"])
        a, b = gap["news"]
        self.assertEqual((a["id"], a["pn"], a["pp"], a["d"], a["z"], a["tag"]), ("A", 30.0, 2.5, 27.5, 3.13, ""))
        # B: 뉴스 3/20 = 15.00, 정책 0/40 -> 합친 비율 .05, z = .15 / sqrt(.05*.95*.075) = 2.51
        self.assertEqual((b["id"], b["d"], b["z"], b["tag"]), ("B", 15.0, 2.51, "only_news"))
        self.assertEqual(gap["policy"], [])

    def test_not_compared_when_one_type_has_too_few_documents(self):
        s = STARTS[0]
        periods, views = weekly([s], {"news": {s: mkview(20)}, "policy": {s: mkview(3)}})
        app.build_gaps(views, periods)
        self.assertEqual(views["weekly|%s|all" % s]["gap"], {"Nn": 20, "Np": 3, "ok": False})


class CategoryViewTest(unittest.TestCase):
    def test_share_within_category_and_change_hand_calculated(self):
        cat = lambda n, k: {"AI": {"N": n, "k": {"a": k}}}  # noqa: E731
        views = {STARTS[0]: mkview(10, scope=[("S", 10, 0, 0)], cat=cat(10, 1)),
                 STARTS[1]: mkview(10, scope=[("S", 10, 0, 0)], cat=cat(10, 1)),
                 STARTS[2]: mkview(10, [("a", "에이", 3)], [("S", 10, 0, 0)], cat(10, 3))}
        periods, built = weekly(STARTS[:3], {"all": views})
        app.build_changes(built, periods, {"a": "에이"})
        row = built["weekly|%s|all" % STARTS[2]]["catv"]["AI"]
        self.assertEqual((row["N"], row["N0"], row["cmp"]), (10, 20, True))
        r = row["rows"][0]
        self.assertEqual((r["id"], r["n1"], r["n0"], r["p1"], r["p0"], r["d"], r["z"]), ("a", 3, 2, 30.0, 10.0, 20.0, 1.39))

    def test_small_category_shows_share_without_comparison(self):
        views = {STARTS[0]: mkview(10, cat={"AI": {"N": 10, "k": {"a": 1}}}),
                 STARTS[1]: mkview(3, cat={"AI": {"N": 3, "k": {"a": 1}}})}
        periods, built = weekly(STARTS[:2], {"all": views})
        app.build_changes(built, periods, {"a": "에이"})
        row = built["weekly|%s|all" % STARTS[1]]["catv"]["AI"]
        self.assertFalse(row["cmp"])
        self.assertEqual(row["rows"], [{"id": "a", "kw": "에이", "n1": 1, "p1": 33.33}])

    def test_category_counts_counts_each_document_once_per_category(self):
        members = [{"document_id": "d1"}, {"document_id": "d2"}, {"document_id": "d3"}]
        kids = {"d1": {"a", "b"}, "d2": {"a"}, "d3": {"z"}}
        out = app.category_counts(members, kids, {"a": "AI", "b": "AI", "z": "보안"})
        self.assertEqual(out["AI"], {"N": 2, "k": {"a": 2, "b": 1}})
        self.assertEqual(out["보안"], {"N": 1, "k": {"z": 1}})


class BackgroundKeywordTest(unittest.TestCase):
    def docs(self, n, with_a, with_b):
        analyzed = [(None, {"document_id": "d%d" % i, "source_type": "news"}) for i in range(n)]
        kids = {}
        for i in range(n):
            ks = set()
            if i < with_a:
                ks.add("A")
            if i < with_b:
                ks.add("B")
            kids["d%d" % i] = ks
        return analyzed, kids

    def test_keyword_in_forty_percent_or_more_is_background(self):
        analyzed, kids = self.docs(25, 10, 9)  # A 40%, B 36%
        self.assertEqual(app.background_keywords(analyzed, kids)["news"], ["A"])

    def test_not_judged_with_fewer_than_twenty_documents(self):
        analyzed, kids = self.docs(19, 19, 19)
        self.assertEqual(app.background_keywords(analyzed, kids)["news"], [])


class ModelIntegrationTest(unittest.TestCase):
    def test_every_non_empty_view_has_change_and_category_data_and_model_is_json(self):
        def read(p):
            with open(p, encoding="utf-8", newline="") as f:
                return list(csv.DictReader(f))
        meta = app.load_meta(ROOT / "config" / "keyword_meta.csv")
        m = app.build_model(read(FIX / "documents.csv"), read(FIX / "expected" / "document_keywords.csv"),
                            read(FIX / "expected" / "keyword_stats.csv"), meta=meta)
        for key, v in m["views"].items():
            if v["analyzed"] > 0:
                self.assertIn("chg", v, key)
                self.assertIn("catv", v, key)
            self.assertIsInstance(v["cat"], dict)
        self.assertIn("gap", m["views"]["weekly|2026-09-28|all"])
        self.assertEqual(set(m["background"]), set(TYPES))
        self.assertIn("AI·모델", m["categories"])
        json.dumps(m, ensure_ascii=False)  # 직렬화 가능해야 한다


class NoVerdictWordsTest(unittest.TestCase):
    FORBIDDEN = ["급증", "급등", "폭증", "유망", "신흥기술", "뜨는", "주목할"]

    def test_insight_screens_use_descriptive_wording_only(self):
        text = (ROOT / "dashboard" / "template.html").read_text(encoding="utf-8")
        a, b = text.index("/* INSIGHT-BEGIN */"), text.index("/* INSIGHT-END */")
        block = text[a:b]
        for word in self.FORBIDDEN:
            self.assertNotIn(word, block, "인사이트 화면 문구에 판정어 '%s'" % word)


if __name__ == "__main__":
    unittest.main()
