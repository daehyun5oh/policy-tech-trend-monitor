#!/usr/bin/env python3
"""처음 한 번 실행하는 설치 도우미.

저장소 안에 가상환경(.venv)을 만들고, 정책 수집기가 쓰는 외부 패키지(requirements.txt)를 거기에 설치한다.
이 파일은 표준 라이브러리만 쓴다. 설치는 이 PC의 .venv 안에서만 일어나고 다른 곳은 바꾸지 않는다.

사용법:
  python setup_env.py           # .venv 만들기 + 패키지 설치
  python setup_env.py --check   # 설치하지 않고 상태만 확인

설치 뒤에는 `python src/run_daily.py` 하나로 뉴스·정책 수집부터 대시보드까지 돈다.
"""
import argparse
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import run_daily  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="가상환경을 만들고 정책 수집기용 패키지를 설치한다.")
    ap.add_argument("--check", action="store_true", help="설치하지 않고 상태만 확인한다")
    args = ap.parse_args(argv)

    if sys.version_info < (3, 10):
        print("Python 3.10 이상이 필요합니다 (지금 %d.%d)." % sys.version_info[:2])
        return 1

    py = run_daily.venv_python(ROOT)
    if args.check:
        found = run_daily.find_policy_python(ROOT)
        print("정책 수집 가능: %s" % (found or "아니요 — `python setup_env.py` 로 설치하세요"))
        return 0 if found else 1

    if py is None:
        print("가상환경(.venv)을 만듭니다...")
        venv.EnvBuilder(with_pip=True).create(str(ROOT / ".venv"))
        py = run_daily.venv_python(ROOT)
        if py is None:
            print("가상환경을 만들지 못했습니다.")
            return 1
    print("패키지를 설치합니다 (requirements.txt)...")
    rc = subprocess.run([py, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]).returncode
    if rc != 0:
        print("설치에 실패했습니다. 위 오류를 확인하세요. 뉴스만 쓸 때는 설치 없이 `python src/run_daily.py` 가 돕니다.")
        return rc
    if not run_daily.has_policy_modules(py):
        print("설치는 끝났지만 패키지를 불러오지 못했습니다.")
        return 1
    print("\n완료. 이제 `python src/run_daily.py` 를 실행하면 뉴스와 정책 수집, 분석, 대시보드 갱신까지 한 번에 돕니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
