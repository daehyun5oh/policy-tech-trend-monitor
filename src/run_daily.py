#!/usr/bin/env python3
"""정기 수집 실행기 (docs/automation.md).

뉴스 피드와 정책 자료를 받아 → 키워드 추출 → 집계 → 검증 → 대시보드 HTML 갱신까지 순서대로 돌린다.
Windows 작업 스케줄러가 하루 두 번 부르는 것을 전제로 하지만, 손으로 실행해도 같다.

- 각 단계는 기존 명령줄을 그대로 부른다(`python src/collect_news.py rss` 등). 표준 라이브러리만 쓴다.
- 수집은 출처마다 요청 1회, 재시도 없음(`collect_news.py`의 규칙). 이 실행기도 재시도하지 않는다.
- 한 단계가 실패해도 이유를 기록하고 나머지를 계속한다(이미 쌓인 자료로 대시보드는 갱신된다).
- 실행 기록은 data/logs/ 에 JSON으로 남기고 최근 60개만 둔다. 동시에 두 번 돌지 않도록 잠금 파일을 쓴다.
- 정책 수집(`collect_policy.py`, `collect_html.py`)은 외부 패키지(requirements.txt)가 필요하다. 이 실행기 자신은
  표준 라이브러리만 쓰고, 패키지가 있는 파이썬(저장소의 .venv 또는 지금 파이썬)이 있으면 정책 수집을 함께 돌리고,
  없으면 정책 수집만 건너뛰고 설치 방법(`python setup_env.py`)을 알려 준다. 뉴스·분석·대시보드는 그대로 돈다.

사용 예:
  python src/run_daily.py              # 전체 실행
  python src/run_daily.py --dry-run    # 실행할 단계만 보여 줌
  python src/run_daily.py --no-collect # 수집은 건너뛰고 이미 쌓인 자료로 분석·대시보드만
  python src/run_daily.py --no-policy  # 정책 수집은 건너뛴다(뉴스만)

종료 코드: 0 모두 성공 / 1 일부 단계 실패 / 3 이미 실행 중이라 건너뜀
"""
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = dt.timezone(dt.timedelta(hours=9))
LOG_DIR = ROOT / "data" / "logs"
LOCK_FILE = ROOT / "data" / "processed" / ".run_daily.lock"
KEEP_LOGS = 60
LOCK_STALE_SECONDS = 2 * 60 * 60
STEP_TIMEOUT_SECONDS = 600
TAIL = 800
POLICY_MODULES = ("feedparser", "requests", "bs4", "lxml")
POLICY_STEPS = ("collect_policy", "collect_html")
COLLECT_STEPS = ("collect",) + POLICY_STEPS
POLICY_PYTHON = None  # main()이 정한다. 정책 수집 단계만 이 파이썬으로 실행한다


def load_window(sources_path):
    """수집 가능한 뉴스 RSS 출처의 collect_from 중 가장 이른 날과 collect_to 중 가장 늦은 날. 없으면 (None, None)."""
    try:
        with open(sources_path, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f)
                    if r.get("source_type") == "news" and r.get("access_method") == "rss"
                    and r.get("access_check_status") == "terms_ok" and r.get("collect_from") and r.get("collect_to")]
    except OSError:
        return None, None
    if not rows:
        return None, None
    return min(r["collect_from"] for r in rows), max(r["collect_to"] for r in rows)


def venv_python(root=ROOT):
    """저장소 안 가상환경(.venv)의 파이썬 경로. 없으면 None."""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        p = Path(root) / rel
        if p.is_file():
            return str(p)
    return None


def has_policy_modules(python):
    """그 파이썬에서 정책 수집기가 쓰는 외부 패키지를 모두 불러올 수 있는가."""
    try:
        return subprocess.run([python, "-c", "import " + ",".join(POLICY_MODULES)], capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_policy_python(root=ROOT, check=has_policy_modules):
    """정책 수집을 돌릴 수 있는 파이썬. 저장소 .venv → 지금 파이썬 순으로 찾고, 없으면 None."""
    candidates = [venv_python(root), sys.executable]
    for py in candidates:
        if py and check(py):
            return py
    return None


def build_steps(window, collect=True, policy=False):
    """실행할 단계 [(이름, 인자 목록)]. 인자는 저장소 루트 기준이다. policy=True면 정책 수집 단계를 뉴스 수집 뒤에 넣는다."""
    steps = []
    if collect:
        args = ["src/collect_news.py", "rss"]
        if window[0] and window[1]:
            args += ["--from", window[0], "--to", window[1]]
        steps.append(("collect", args))
        if policy:
            steps.append(("collect_policy", ["src/collect_policy.py"]))
            steps.append(("collect_html", ["src/collect_html.py"]))
    steps += [
        ("extract", ["src/extract_keywords.py"]),
        ("aggregate", ["src/aggregate.py"]),
        ("validate", ["src/validate_csv.py", "--dir", "data/processed", "--sources", "config/sources.csv"]),
        ("dashboard", ["dashboard/app.py", "--dir", "data/processed"]),
    ]
    return steps


def run_step(name, args):
    """한 단계를 하위 프로세스로 실행한다. 시간 제한을 넘기거나 실행하지 못해도 예외 대신 기록을 돌려준다."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    started = dt.datetime.now(KST)
    try:
        py = POLICY_PYTHON if (name in POLICY_STEPS and POLICY_PYTHON) else sys.executable
        proc = subprocess.run([py] + args, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_SECONDS, env=env)
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", "시간 제한(%d초)을 넘겨 중단함" % STEP_TIMEOUT_SECONDS
    except OSError as e:
        rc, out, err = 127, "", "실행하지 못함: %s" % e
    return {"step": name, "returncode": rc, "seconds": round((dt.datetime.now(KST) - started).total_seconds(), 1),
            "stdout_tail": (out or "").strip()[-TAIL:], "stderr_tail": (err or "").strip()[-TAIL:]}


def run_all(steps, runner=run_step):
    """모든 단계를 순서대로 실행한다. 실패한 단계가 있어도 계속한다. 반환: (결과 목록, 전체 상태)."""
    results = [runner(name, args) for name, args in steps]
    failed = [r["step"] for r in results if r["returncode"] != 0]
    if not failed:
        status = "ok"
    elif all(r["step"] in COLLECT_STEPS and r["returncode"] == 1 for r in results if r["returncode"] != 0):
        status = "partial"  # 일부 출처만 받지 못함. 받은 자료로 나머지는 진행됨
    else:
        status = "failed"
    return results, status


def acquire_lock(path=LOCK_FILE, now=None):
    """잠금을 얻는다. 이미 다른 실행이 잠금을 쥐고 있으면(2시간 이내) False."""
    now = now or dt.datetime.now(KST)
    path = Path(path)
    if path.exists():
        try:
            age = now.timestamp() - path.stat().st_mtime
        except OSError:
            age = LOCK_STALE_SECONDS + 1
        if age < LOCK_STALE_SECONDS:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("%d %s\n" % (os.getpid(), now.isoformat(timespec="seconds")), encoding="utf-8")
    return True


def release_lock(path=LOCK_FILE):
    try:
        Path(path).unlink()
    except OSError:
        pass


def git_info():
    """실행한 코드가 어느 브랜치·커밋인지(추적용). 알 수 없으면 빈 값."""
    def git(*a):
        try:
            return subprocess.run(["git"] + list(a), cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "commit": git("rev-parse", "--short", "HEAD"),
            "dirty": bool(git("status", "--porcelain"))}


def write_log(log, log_dir=LOG_DIR, keep=KEEP_LOGS):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.fromisoformat(log["started_at"]).strftime("%Y%m%d_%H%M%S")
    path = log_dir / ("run_daily_%s.json" % stamp)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    old = sorted(log_dir.glob("run_daily_*.json"))
    for p in old[:-keep] if len(old) > keep else []:
        try:
            p.unlink()
        except OSError:
            pass
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="뉴스 수집 → 추출 → 집계 → 검증 → 대시보드를 한 번에 실행한다.")
    ap.add_argument("--dry-run", action="store_true", help="실행할 단계만 보여 주고 아무것도 실행하지 않는다")
    ap.add_argument("--no-collect", action="store_true", help="수집은 건너뛰고 쌓인 자료로 분석·대시보드만 갱신한다")
    ap.add_argument("--no-policy", action="store_true", help="정책 수집은 건너뛴다(뉴스만)")
    args = ap.parse_args(argv)

    global POLICY_PYTHON
    window = load_window(ROOT / "config" / "sources.csv")
    policy_note = ""
    use_policy = False
    if args.no_collect or args.no_policy:
        policy_note = "정책 수집 안 함 (요청)"
    else:
        POLICY_PYTHON = find_policy_python()
        use_policy = POLICY_PYTHON is not None
        policy_note = ("정책 수집 포함 (%s)" % POLICY_PYTHON) if use_policy else (
            "정책 수집 건너뜀 — 외부 패키지가 없습니다. 정책까지 보려면 `python setup_env.py`를 한 번 실행하세요")
    steps = build_steps(window, collect=not args.no_collect, policy=use_policy)
    print(policy_note)
    if args.dry_run:
        print("수집 기간: %s ~ %s" % (window[0] or "제한 없음", window[1] or "제한 없음"))
        for name, a in steps:
            print("  %-10s python %s" % (name, " ".join(a)))
        return 0

    now = dt.datetime.now(KST)
    if not acquire_lock(now=now):
        print("이미 실행 중이라 건너뜁니다 (%s)." % LOCK_FILE)
        return 3
    try:
        results, status = run_all(steps)
    finally:
        release_lock()
    log = {"started_at": now.replace(microsecond=0).isoformat(), "status": status, "window": list(window), "policy": policy_note,
           "git": git_info(), "steps": results}
    path = write_log(log)
    for r in results:
        print("%-10s %s (%.1f초)" % (r["step"], "ok" if r["returncode"] == 0 else "실패 rc=%d" % r["returncode"], r["seconds"]))
    print("전체 %s -> %s" % (status, path))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
