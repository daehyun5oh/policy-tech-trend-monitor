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
