#!/usr/bin/env python3
"""기간을 입력하면 그 기간의 뉴스·정책을 모아 대시보드로 보여 주는 실행기.

    python monitor.py                                  # 시작일·종료일을 물어본다 (Enter = 기본값)
    python monitor.py --from 2026-10-14 --to 2026-10-21  # 묻지 않고 바로
    python monitor.py --clean                          # 작업 폴더를 지운다

동작
- 입력한 기간으로 뉴스(RSS)와 정책(RSS·목록 페이지)을 수집하고, 키워드를 추출·집계해 대시보드를 만들어 연다.
- **작업 폴더는 시스템 임시 폴더**(`<임시>/policy-tech-trend-monitor`)다. 저장소의 `data/` 는 건드리지 않는다.
  받은 자료는 이 폴더에 쌓이고, 실행할 때마다 **발행일이 보관 기간(기본 90일)보다 오래된 자료와 그 원문 파일을 지운다.**
  `--fresh` 로 이번 실행 전에 작업 폴더를 비울 수 있다.
- 뉴스 피드는 최근 기사만 준다(ZDNet 약 15시간, 긱뉴스 약 하루, 전자신문 약 이틀, 보안뉴스 약 4주).
  그래서 **요청한 기간을 다 받지 못할 수 있고**, 실제로 확보한 기간을 요청 기간과 나란히 알려 준다.
  제공자가 열어 두지 않은 경로로 과거 기사를 받지 않는다. 같은 도구를 며칠 간격으로 돌리면 확보 기간이 늘어난다.
- 정책 수집은 외부 패키지가 필요하다. 없으면 정책만 건너뛰고 `python setup_env.py` 를 안내한다.
- 이 파일은 표준 라이브러리만 쓴다. 수집은 출처마다 요청 1회, 재시도 없음(각 수집기의 규칙).
"""
import argparse
import csv
import datetime as dt
import json
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import run_daily as rd  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_DAYS = 7
DEFAULT_KEEP_DAYS = 90
WORKDIR_NAME = "policy-tech-trend-monitor"


def today_kst():
    return dt.datetime.now(KST).date()


def default_workdir():
    return Path(tempfile.gettempdir()) / WORKDIR_NAME


def parse_date(text):
    """YYYY-MM-DD 문자열을 날짜로. 형식이 아니면 ValueError."""
    return dt.datetime.strptime(text.strip(), "%Y-%m-%d").date()


def resolve_period(start, end, today):
    """입력(문자열 또는 빈 값)을 (시작일, 종료일)로 정한다. 빈 시작일은 종료일 6일 전(7일치), 빈 종료일은 오늘.

    시작일이 종료일보다 늦거나 종료일이 오늘 뒤이면 ValueError.
    """
    e = parse_date(end) if (end or "").strip() else today
    s = parse_date(start) if (start or "").strip() else e - dt.timedelta(days=DEFAULT_DAYS - 1)
    if s > e:
        raise ValueError("시작일(%s)이 종료일(%s)보다 늦습니다." % (s, e))
    if e > today:
        raise ValueError("종료일(%s)이 오늘(%s)보다 뒤입니다." % (e, today))
    return s, e


def ask_period(today, input_fn=input):
    """대화형으로 기간을 묻는다. 잘못 입력하면 이유를 알리고 다시 묻는다."""
    default_end = today
    default_start = today - dt.timedelta(days=DEFAULT_DAYS - 1)
    print("조회할 기간을 입력하세요 (YYYY-MM-DD). 그냥 Enter 를 누르면 기본값(최근 %d일)입니다." % DEFAULT_DAYS)
    print("뉴스 피드는 최근 며칠치만 주기 때문에 먼 과거는 비어 있을 수 있습니다.\n")
    while True:
        start = input_fn("  시작일 [%s]: " % default_start)
        end = input_fn("  종료일 [%s]: " % default_end)
        try:
            return resolve_period(start, end, today)
        except ValueError as e:
            print("  입력을 확인하세요: %s\n" % e)


def work_paths(workdir):
    w = Path(workdir)
    return {"work": w, "raw": w / "raw", "processed": w / "processed", "view": w / "view"}


def build_steps(period, workdir, policy):
    """실행할 단계 [(이름, 인자 목록)]. 경로는 모두 문자열이다."""
    f, t = period[0].isoformat(), period[1].isoformat()
    p = work_paths(workdir)
    raw, proc, view = str(p["raw"]), str(p["processed"]), str(p["view"])
    steps = [("collect", ["src/collect_news.py", "rss", "--from", f, "--to", t, "--out-dir", proc, "--raw-dir", raw])]
    if policy:
        steps.append(("collect_policy", ["src/collect_policy.py", "--from", f, "--to", t, "--data-dir", str(p["work"])]))
        steps.append(("collect_html", ["src/collect_html.py", "--from", f, "--to", t, "--data-dir", str(p["work"])]))
    steps.append(("extract", ["src/extract_keywords.py", "--documents", proc + "/documents.csv",
                              "--text-dir", raw, "--text-dir", proc + "/texts", "--out", proc + "/document_keywords.csv"]))
    return steps, [
        ("aggregate", ["src/aggregate.py", "--documents", view + "/documents.csv", "--keywords", view + "/document_keywords.csv",
                       "--out", view + "/keyword_stats.csv"]),
        ("validate", ["src/validate_csv.py", "--dir", view, "--sources", "config/sources.csv"]),
        ("dashboard", ["dashboard/app.py", "--dir", view, "--out", view + "/dashboard.html", "--range-from", f, "--range-to", t]),
    ]


def read_rows(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            return list(r.fieldnames or []), list(r)
    except OSError:
        return [], []


def write_rows(path, fields, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def in_period(row, period):
    """발행일이 기간 안이면 True. 발행일이 비어 있으면(날짜 없음으로 제외된 행) 건수를 남기려고 True."""
    d = (row.get("published_date") or "").strip()
    return (not d) or (period[0].isoformat() <= d <= period[1].isoformat())


def build_view(workdir, period):
    """작업 폴더의 전체 자료에서 조회 기간만 잘라 view/ 에 쓴다. (documents 행 수, 키워드 행 수)를 돌려준다."""
    p = work_paths(workdir)
    dfields, docs = read_rows(p["processed"] / "documents.csv")
    kfields, kws = read_rows(p["processed"] / "document_keywords.csv")
    keep = [r for r in docs if in_period(r, period)]
    ids = {r["document_id"] for r in keep}
    keep_kw = [r for r in kws if r["document_id"] in ids]
    write_rows(p["view"] / "documents.csv", dfields, keep)
    write_rows(p["view"] / "document_keywords.csv", kfields or ["document_id"], keep_kw)
    return len(keep), len(keep_kw)


def prune(workdir, today, keep_days=DEFAULT_KEEP_DAYS):
    """발행일이 보관 기간보다 오래된 문서와 그 원문 파일을 지운다. 날짜가 없는 행은 지우지 않는다. 지운 문서 수를 돌려준다."""
    p = work_paths(workdir)
    cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
    removed = 0
    dfields, docs = read_rows(p["processed"] / "documents.csv")
    if docs:
        old = [r for r in docs if (r.get("published_date") or "") and r["published_date"] < cutoff]
        if old:
            gone = {r["document_id"] for r in old}
            write_rows(p["processed"] / "documents.csv", dfields, [r for r in docs if r["document_id"] not in gone])
            kfields, kws = read_rows(p["processed"] / "document_keywords.csv")
            if kws:
                write_rows(p["processed"] / "document_keywords.csv", kfields, [r for r in kws if r["document_id"] not in gone])
            for d in (p["raw"], p["processed"] / "texts"):
                for doc_id in gone:
                    try:
                        (d / (doc_id + ".txt")).unlink()
                    except OSError:
                        pass
            removed = len(gone)
    nfields, news = read_rows(p["raw"] / "news_items.csv")
    if news:
        kept = [r for r in news if not ((r.get("published") or "")[:10] and _iso(r) and _iso(r) < cutoff)]
        if len(kept) != len(news):
            write_rows(p["raw"] / "news_items.csv", nfields, kept)
    return removed


def _iso(row):
    """누적 뉴스 한 줄의 발행일을 YYYY-MM-DD 로. 읽지 못하면 빈 문자열(= 지우지 않음)."""
    text = (row.get("published") or "").strip()
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(text).astimezone(KST).date().isoformat()
    except (TypeError, ValueError):
        return ""


def coverage(workdir, period):
    """요청 기간 안에서 분석된 문서의 실제 발행일 범위. {유형: (건수, 처음, 끝)}."""
    _, docs = read_rows(work_paths(workdir)["view"] / "documents.csv")
    out = {}
    for t in ("news", "policy"):
        dates = sorted(r["published_date"] for r in docs if r.get("source_type") == t and r.get("status") == "analyzed"
                       and r.get("published_date"))
        out[t] = (len(dates), dates[0], dates[-1]) if dates else (0, "", "")
    return out


def coverage_lines(cov, period):
    names = {"news": "뉴스", "policy": "정책"}
    lines = []
    for t in ("news", "policy"):
        n, a, b = cov[t]
        if n == 0:
            lines.append("  %s: 요청한 기간에 받은 자료가 없습니다." % names[t])
            continue
        gap = ""
        if a > period[0].isoformat():
            gap = " — %s ~ %s 은(는) 받지 못했습니다(피드에 남아 있지 않음)" % (period[0], (dt.date.fromisoformat(a) - dt.timedelta(days=1)))
        lines.append("  %s: %d건, 실제 %s ~ %s%s" % (names[t], n, a, b, gap))
    return lines


def run_steps(steps, label):
    results = []
    for i, (name, args) in enumerate(steps, start=1):
        print("  [%s %d/%d] %s ..." % (label, i, len(steps), name), end="", flush=True)
        r = rd.run_step(name, args)
        results.append(r)
        print(" %s (%.0f초)" % ("ok" if r["returncode"] == 0 else "실패 rc=%d" % r["returncode"], r["seconds"]))
        if r["returncode"] not in (0, 1):  # 1 은 일부 출처만 실패(수집기 규칙). 그 밖은 중단 사유
            tail = (r["stderr_tail"] or r["stdout_tail"]).splitlines()[-3:]
            for line in tail:
                print("      " + line)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="기간을 입력해 뉴스·정책을 모으고 대시보드로 보여 준다.")
    ap.add_argument("--from", dest="date_from", help="시작일 YYYY-MM-DD (없으면 물어봄)")
    ap.add_argument("--to", dest="date_to", help="종료일 YYYY-MM-DD (없으면 물어봄)")
    ap.add_argument("--workdir", help="작업 폴더 (기본: 시스템 임시 폴더 안)")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="발행일이 이보다 오래된 자료는 지움 (기본 %d)" % DEFAULT_KEEP_DAYS)
    ap.add_argument("--fresh", action="store_true", help="실행 전에 작업 폴더를 비운다")
    ap.add_argument("--clean", action="store_true", help="작업 폴더를 지우고 끝낸다")
    ap.add_argument("--no-policy", action="store_true", help="정책 수집은 건너뛴다(뉴스만)")
    ap.add_argument("--no-open", action="store_true", help="끝난 뒤 브라우저를 열지 않는다")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else default_workdir()
    if args.clean:
        shutil.rmtree(workdir, ignore_errors=True)
        print("작업 폴더를 지웠습니다: %s" % workdir)
        return 0

    today = today_kst()
    try:
        if args.date_from is not None or args.date_to is not None:
            period = resolve_period(args.date_from, args.date_to, today)
        elif sys.stdin.isatty():
            period = ask_period(today)
        else:
            period = resolve_period("", "", today)
    except ValueError as e:
        print("기간을 확인하세요: %s" % e, file=sys.stderr)
        return 2
    print("\n조회 기간: %s ~ %s (%d일)" % (period[0], period[1], (period[1] - period[0]).days + 1))

    if args.fresh:
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    removed = prune(workdir, today, args.keep_days)
    if removed:
        print("보관 기간(%d일)이 지난 문서 %d건을 지웠습니다." % (args.keep_days, removed))

    policy = False
    if args.no_policy:
        print("정책 수집은 건너뜁니다(요청).")
    else:
        rd.POLICY_PYTHON = rd.find_policy_python()
        policy = rd.POLICY_PYTHON is not None
        if not policy:
            print("정책 수집은 건너뜁니다 — 외부 패키지가 없습니다. 정책까지 보려면 `python setup_env.py` 를 한 번 실행하세요.")

    collect_steps, view_steps = build_steps(period, workdir, policy)
    print("\n수집·분석 (작업 폴더: %s)" % workdir)
    results = run_steps(collect_steps, "수집")
    n_docs, n_kw = build_view(workdir, period)
    results += run_steps(view_steps, "분석")
    failed = [r["step"] for r in results if r["returncode"] not in (0, 1)]

    dash = work_paths(workdir)["view"] / "dashboard.html"
    cov = coverage(workdir, period)
    print("\n확보한 자료 (요청 %s ~ %s)" % (period[0], period[1]))
    for line in coverage_lines(cov, period):
        print(line)
    if failed or not dash.exists():
        print("\n일부 단계가 실패해 대시보드를 만들지 못했거나 불완전합니다: %s" % ", ".join(failed or ["dashboard"]))
        return 1
    print("\n대시보드: %s" % dash)
    print("이 파일에는 수집한 기사·문서의 제목과 링크가 들어 있습니다. 다른 사람에게 보내지 마세요.")
    if not args.no_open:
        webbrowser.open(dash.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
