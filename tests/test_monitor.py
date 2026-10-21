"""monitor.py (기간을 입력해 수집·대시보드) 검사. 기대값은 손으로 계산했다."""
import csv
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
import monitor as mo  # noqa: E402

D = dt.date
TODAY = D(2026, 10, 21)


class ResolvePeriodTest(unittest.TestCase):
    def test_blank_input_means_the_last_seven_days_ending_today(self):
        # 10/21 포함 7일 = 10/15 ~ 10/21
        self.assertEqual(mo.resolve_period("", "", TODAY), (D(2026, 10, 15), D(2026, 10, 21)))

    def test_blank_start_counts_seven_days_back_from_the_given_end(self):
        self.assertEqual(mo.resolve_period("", "2026-10-10", TODAY), (D(2026, 10, 4), D(2026, 10, 10)))

    def test_explicit_values_are_used_and_whitespace_ignored(self):
        self.assertEqual(mo.resolve_period(" 2026-10-01 ", "2026-10-21", TODAY), (D(2026, 10, 1), D(2026, 10, 21)))

    def test_bad_inputs_raise_value_error(self):
        for s, e in (("2026-10-22", "2026-10-21"),   # 시작이 종료보다 늦음
                     ("2026-10-01", "2026-10-22"),   # 종료가 오늘 뒤
                     ("10/01", ""), ("", "어제")):    # 형식 오류
            with self.subTest(s=s, e=e):
                with self.assertRaises(ValueError):
                    mo.resolve_period(s, e, TODAY)

    def test_interactive_prompt_asks_again_after_a_bad_answer(self):
        answers = iter(["2026-10-30", "", "2026-10-18", "2026-10-21"])   # (시작 10/30, 종료 빈칸)은 거부 → (10/18, 10/21)
        self.assertEqual(mo.ask_period(TODAY, lambda prompt: next(answers)), (D(2026, 10, 18), D(2026, 10, 21)))


class StepsTest(unittest.TestCase):
    def test_steps_pass_the_period_and_the_workdir_and_policy_is_optional(self):
        steps, view = mo.build_steps((D(2026, 10, 14), D(2026, 10, 21)), "W", policy=True)
        self.assertEqual([n for n, _ in steps], ["collect", "collect_policy", "collect_html", "extract"])
        d = dict(steps)
        self.assertEqual(d["collect"][:6], ["src/collect_news.py", "rss", "--from", "2026-10-14", "--to", "2026-10-21"])
        self.assertIn(str(Path("W") / "processed"), d["collect"])
        self.assertEqual(d["collect_html"][1:5], ["--from", "2026-10-14", "--to", "2026-10-21"])
        self.assertEqual(d["collect_policy"][-2:], ["--data-dir", "W"])
        self.assertEqual([n for n, _ in view], ["aggregate", "validate", "dashboard"])
        no_policy = mo.build_steps((D(2026, 10, 14), D(2026, 10, 21)), "W", policy=False)[0]
        self.assertEqual([n for n, _ in no_policy], ["collect", "extract"])


def write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


DOC = ["document_id", "source_type", "published_date", "status"]


def doc(i, t, d, s="analyzed"):
    return {"document_id": i, "source_type": t, "published_date": d, "status": s}


class ViewAndPruneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = Path(self.tmp.name)
        write(self.w / "processed" / "documents.csv", DOC, [
            doc("a", "news", "2026-10-10"),                  # 기간 안
            doc("b", "news", "2026-10-21"),                  # 기간 끝 날(포함)
            doc("c", "policy", "2026-10-09"),                # 기간 시작 전
            doc("d", "policy", "2026-10-12"),                # 기간 안
            doc("e", "policy", "", "excluded"),              # 날짜 없음(제외 행): 건수를 남기려고 보여 줌
            doc("old", "policy", "2026-06-01"),              # 90일(7/23) 이전
        ])
        write(self.w / "processed" / "document_keywords.csv", ["document_id", "keyword_id"],
              [{"document_id": i, "keyword_id": "K1"} for i in ("a", "b", "c", "d", "old")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_view_keeps_only_documents_inside_the_period_plus_undated_rows(self):
        n_docs, n_kw = mo.build_view(self.w, (D(2026, 10, 10), D(2026, 10, 21)))
        self.assertEqual((n_docs, n_kw), (4, 3))   # a, b, d, e / a, b, d 의 키워드
        with open(self.w / "view" / "documents.csv", encoding="utf-8") as f:
            self.assertEqual([r["document_id"] for r in csv.DictReader(f)], ["a", "b", "d", "e"])

    def test_prune_removes_documents_older_than_the_keep_period_and_their_files(self):
        (self.w / "raw").mkdir(parents=True, exist_ok=True)
        (self.w / "raw" / "old.txt").write_text("x", encoding="utf-8")
        (self.w / "raw" / "a.txt").write_text("x", encoding="utf-8")
        # 기준일 10/21 - 90일 = 7/23. 6/1 은 오래됨, 날짜 없는 행(e)과 나머지는 남는다
        self.assertEqual(mo.prune(self.w, TODAY, 90), 1)
        self.assertFalse((self.w / "raw" / "old.txt").exists())
        self.assertTrue((self.w / "raw" / "a.txt").exists())
        with open(self.w / "processed" / "documents.csv", encoding="utf-8") as f:
            self.assertEqual([r["document_id"] for r in csv.DictReader(f)], ["a", "b", "c", "d", "e"])
        with open(self.w / "processed" / "document_keywords.csv", encoding="utf-8") as f:
            self.assertEqual([r["document_id"] for r in csv.DictReader(f)], ["a", "b", "c", "d"])

    def test_prune_on_an_empty_workdir_does_nothing(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(mo.prune(empty, TODAY, 90), 0)

    def test_prune_also_trims_the_accumulated_news_items(self):
        write(self.w / "raw" / "news_items.csv", ["title", "published"], [
            {"title": "옛날", "published": "2026-06-01T09:00:00+09:00"},      # 7/23 이전 → 삭제
            {"title": "최근 RFC", "published": "Mon, 19 Oct 2026 10:00:00 +0900"},
            {"title": "날짜 없음", "published": ""},                          # 읽지 못하면 남김
        ])
        mo.prune(self.w, TODAY, 90)
        with open(self.w / "raw" / "news_items.csv", encoding="utf-8") as f:
            self.assertEqual([r["title"] for r in csv.DictReader(f)], ["최근 RFC", "날짜 없음"])


class CoverageTest(unittest.TestCase):
    def test_coverage_reports_the_actual_range_and_names_the_missing_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            write(w / "view" / "documents.csv", DOC, [
                doc("a", "news", "2026-10-18"), doc("b", "news", "2026-10-21"),
                doc("c", "policy", "2026-10-02"), doc("x", "policy", "2026-10-03", "excluded"),
            ])
            period = (D(2026, 10, 1), D(2026, 10, 21))
            cov = mo.coverage(w, period)
            self.assertEqual(cov, {"news": (2, "2026-10-18", "2026-10-21"), "policy": (1, "2026-10-02", "2026-10-02")})
            lines = mo.coverage_lines(cov, period)
            # 뉴스는 10/1 ~ 10/17 을 받지 못함, 정책은 10/1 하루만 못 받음
            self.assertIn("2026-10-01 ~ 2026-10-17", lines[0])
            self.assertIn("2026-10-01 ~ 2026-10-01", lines[1])

    def test_no_documents_in_the_period_is_stated_plainly(self):
        lines = mo.coverage_lines({"news": (0, "", ""), "policy": (0, "", "")}, (D(2026, 10, 1), D(2026, 10, 7)))
        self.assertTrue(all("받은 자료가 없습니다" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
