import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import run_daily as rd  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


class WindowAndStepsTest(unittest.TestCase):
    def write_sources(self, tmp, rows):
        path = Path(tmp) / "sources.csv"
        header = "source_id,source_type,access_method,access_check_status,collect_from,collect_to"
        path.write_text("\n".join([header] + rows) + "\n", encoding="utf-8")
        return path

    def test_window_spans_the_collectable_news_rss_sources_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_sources(tmp, [
                "n1,news,rss,terms_ok,2026-05-01,2026-10-31",
                "n2,news,rss,terms_ok,2026-06-01,2026-12-31",
                "n3,news,rss,unverified,2025-01-01,2025-02-01",   # 수집 불가
                "p1,policy,rss,terms_ok,2020-01-01,2020-02-01",     # 정책
            ])
            self.assertEqual(rd.load_window(path), ("2026-05-01", "2026-12-31"))

    def test_missing_sources_file_means_no_window(self):
        self.assertEqual(rd.load_window(Path("없는/파일.csv")), (None, None))

    def test_steps_are_in_pipeline_order_and_pass_the_window(self):
        steps = rd.build_steps(("2026-05-01", "2026-10-31"))
        self.assertEqual([n for n, _ in steps], ["collect", "extract", "aggregate", "validate", "dashboard"])
        self.assertEqual(steps[0][1], ["src/collect_news.py", "rss", "--from", "2026-05-01", "--to", "2026-10-31"])

    def test_no_collect_skips_only_the_collection(self):
        self.assertEqual([n for n, _ in rd.build_steps((None, None), collect=False)],
                         ["extract", "aggregate", "validate", "dashboard"])
        self.assertEqual(rd.build_steps((None, None))[0][1], ["src/collect_news.py", "rss"])


class RunAllTest(unittest.TestCase):
    def fake(self, codes):
        calls = []

        def runner(name, args):
            calls.append(name)
            return {"step": name, "returncode": codes.get(name, 0), "seconds": 0.0, "stdout_tail": "", "stderr_tail": ""}
        return runner, calls

    def test_all_steps_run_and_status_is_ok(self):
        runner, calls = self.fake({})
        results, status = rd.run_all(rd.build_steps((None, None)), runner)
        self.assertEqual((calls, status), (["collect", "extract", "aggregate", "validate", "dashboard"], "ok"))
        self.assertEqual(len(results), 5)

    def test_a_failed_step_does_not_stop_the_rest(self):
        runner, calls = self.fake({"extract": 2})
        _, status = rd.run_all(rd.build_steps((None, None)), runner)
        self.assertEqual(calls, ["collect", "extract", "aggregate", "validate", "dashboard"])
        self.assertEqual(status, "failed")

    def test_some_sources_failing_is_a_partial_status(self):
        runner, _ = self.fake({"collect": 1})
        self.assertEqual(rd.run_all(rd.build_steps((None, None)), runner)[1], "partial")

    def test_collect_input_error_is_a_failure_not_partial(self):
        runner, _ = self.fake({"collect": 2})
        self.assertEqual(rd.run_all(rd.build_steps((None, None)), runner)[1], "failed")

    def test_run_step_reports_a_missing_program_instead_of_raising(self):
        r = rd.run_step("x", ["존재하지_않는_스크립트.py"])
        self.assertNotEqual(r["returncode"], 0)
        self.assertEqual(r["step"], "x")


class PolicyStepsTest(unittest.TestCase):
    def test_policy_steps_follow_news_collection_and_precede_extraction(self):
        steps = rd.build_steps(("2026-05-01", "2026-10-31"), policy=True)
        self.assertEqual([n for n, _ in steps],
                         ["collect", "collect_policy", "collect_html", "extract", "aggregate", "validate", "dashboard"])
        # 정책 수집기는 기간을 sources.csv 에서 읽으므로 인자를 주지 않는다
        self.assertEqual(dict(steps)["collect_policy"], ["src/collect_policy.py"])
        self.assertEqual(dict(steps)["collect_html"], ["src/collect_html.py"])

    def test_policy_steps_are_off_by_default_and_with_no_collect(self):
        self.assertNotIn("collect_policy", [n for n, _ in rd.build_steps((None, None))])
        self.assertNotIn("collect_policy", [n for n, _ in rd.build_steps((None, None), collect=False, policy=True)])

    def test_find_policy_python_prefers_the_venv_then_the_current_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / ".venv" / "Scripts" / "python.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            venv = str(exe)
            self.assertEqual(rd.find_policy_python(tmp, check=lambda py: True), venv)
            self.assertEqual(rd.find_policy_python(tmp, check=lambda py: py == sys.executable), sys.executable)
            self.assertIsNone(rd.find_policy_python(tmp, check=lambda py: False))

    def test_find_policy_python_without_a_venv_uses_the_current_python_only_if_it_has_the_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(rd.venv_python(tmp))
            self.assertEqual(rd.find_policy_python(tmp, check=lambda py: True), sys.executable)
            self.assertIsNone(rd.find_policy_python(tmp, check=lambda py: False))

    def test_a_policy_source_failing_is_partial_but_a_policy_crash_is_failed(self):
        steps = rd.build_steps((None, None), policy=True)

        def fake(codes):
            return lambda name, args: {"step": name, "returncode": codes.get(name, 0), "seconds": 0.0,
                                       "stdout_tail": "", "stderr_tail": ""}
        self.assertEqual(rd.run_all(steps, fake({"collect_policy": 1}))[1], "partial")
        self.assertEqual(rd.run_all(steps, fake({"collect": 1, "collect_html": 1}))[1], "partial")
        self.assertEqual(rd.run_all(steps, fake({"collect_policy": 2}))[1], "failed")
        self.assertEqual(rd.run_all(steps, fake({"collect_html": 1, "extract": 1}))[1], "failed")


class PolicyOncePerDayTest(unittest.TestCase):
    def write_log(self, d, started, steps):
        stamp = started.replace("-", "").replace("T", "_").replace(":", "")[:15]
        (Path(d) / ("run_daily_%s.json" % stamp)).write_text(
            json.dumps({"started_at": started, "steps": [{"step": n, "returncode": rc} for n, rc in steps]}), encoding="utf-8")

    def test_a_policy_step_run_today_counts(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_log(d, "2026-10-21T09:00:05+09:00", [("collect", 0), ("collect_policy", 0), ("collect_html", 1)])
            self.assertTrue(rd.policy_collected_on("2026-10-21", d))

    def test_another_day_or_a_news_only_run_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_log(d, "2026-10-20T21:00:05+09:00", [("collect", 0), ("collect_policy", 0)])   # 어제
            self.write_log(d, "2026-10-21T09:00:05+09:00", [("collect", 0), ("extract", 0)])        # 오늘이지만 뉴스만
            self.assertFalse(rd.policy_collected_on("2026-10-21", d))

    def test_a_policy_step_that_could_not_even_start_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_log(d, "2026-10-21T09:00:05+09:00", [("collect_policy", 127), ("collect_html", 124)])
            self.assertFalse(rd.policy_collected_on("2026-10-21", d))

    def test_missing_folder_and_broken_logs_are_ignored(self):
        self.assertFalse(rd.policy_collected_on("2026-10-21", Path("없는/폴더")))
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "run_daily_20261021_090000.json").write_text("{깨짐", encoding="utf-8")
            self.assertFalse(rd.policy_collected_on("2026-10-21", d))


class LockTest(unittest.TestCase):
    def test_second_run_is_refused_while_the_lock_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "sub" / ".lock"
            self.assertTrue(rd.acquire_lock(lock))
            self.assertFalse(rd.acquire_lock(lock))
            rd.release_lock(lock)
            self.assertTrue(rd.acquire_lock(lock))

    def test_a_stale_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".lock"
            lock.write_text("옛 실행", encoding="utf-8")
            old = time.time() - rd.LOCK_STALE_SECONDS - 60
            os.utime(lock, (old, old))
            self.assertTrue(rd.acquire_lock(lock))


class LogTest(unittest.TestCase):
    def test_log_is_written_as_json_and_old_logs_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = dt.datetime(2026, 10, 19, 9, 0, 0, tzinfo=KST)
            for i in range(5):
                log = {"started_at": (base + dt.timedelta(hours=i)).isoformat(), "status": "ok", "steps": []}
                path = rd.write_log(log, log_dir=tmp, keep=3)
            names = sorted(p.name for p in Path(tmp).glob("run_daily_*.json"))
            self.assertEqual(names, ["run_daily_20261019_110000.json", "run_daily_20261019_120000.json", "run_daily_20261019_130000.json"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "ok")

    def test_dry_run_runs_nothing_and_returns_zero(self):
        self.assertEqual(rd.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
