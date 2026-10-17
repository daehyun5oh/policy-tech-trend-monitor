"""종단 실행 (이슈 #21, 담당 B)

수집 → 정제·중복 제거 → 키워드 추출 → 집계 → (선택) 보고서를 한 번에 돌린다.
단계마다 결과와 실패 사유를 남기고, 한 단계가 비어도 다음 단계로 넘어간다.

두 가지 방식으로 돌아간다.

**고정 샘플** (`--sample`)
    `data/sample/` 의 합성 자료만 쓴다. 네트워크에 접속하지 않고 API 키도 필요
    없다 (완료기준 C9). 키워드 추출기(이슈 #11, 담당 A)가 없어도 샘플 키워드
    파일이 그 자리를 대신해 CSV 3종이 모두 만들어진다.

**실제 자료** (기본)
    `config/sources.csv` 의 허용된 출처에서 수집한다. 키워드 추출기가 아직
    없으면 그 단계를 건너뛰고 사유를 기록한다.

사용법
    python src/pipeline.py --sample
    python src/pipeline.py --sample --with-report
    python src/pipeline.py
    python src/pipeline.py --source-id policy-kr-001 --limit 10
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import aggregate as ag  # noqa: E402
import normalize as nz  # noqa: E402

PROCESSED_DIR = ROOT / "data" / "processed"
SAMPLE_DIR = ROOT / "data" / "sample"
CONFIG_SOURCES = ROOT / "config" / "sources.csv"

RAW_DIR = ROOT / "data" / "raw"
DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"
KEYWORDS_CSV = PROCESSED_DIR / "document_keywords.csv"
STATS_CSV = PROCESSED_DIR / "keyword_stats.csv"

SAMPLE_INPUT = SAMPLE_DIR / "policy_sample_input.csv"
SAMPLE_KEYWORDS = SAMPLE_DIR / "document_keywords_sample.csv"
SAMPLE_SOURCES = SAMPLE_DIR / "sources_sample.csv"

EXTRACTOR = SRC / "extract_keywords.py"

KST = timezone(timedelta(hours=9))

KEYWORD_FIELDS = ["document_id", "keyword_id", "keyword", "matched_terms",
                  "occurrences", "dictionary_version"]


def now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def shown_path(path: Path) -> str:
    """기록에 남길 경로. 저장소 안이면 상대 경로, 밖이면 절대 경로."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Stage:
    """단계 하나의 결과. 실패해도 예외를 던지지 않고 기록으로 남긴다."""

    def __init__(self, name: str):
        self.name = name
        self.status = "skipped"
        self.reason = ""
        self.detail: dict = {}

    def ok(self, **detail) -> "Stage":
        self.status = "ok"
        self.detail = detail
        return self

    def empty(self, reason: str, **detail) -> "Stage":
        self.status = "empty"
        self.reason = reason
        self.detail = detail
        return self

    def skip(self, reason: str, **detail) -> "Stage":
        self.status = "skipped"
        self.reason = reason
        self.detail = detail
        return self

    def fail(self, reason: str, **detail) -> "Stage":
        self.status = "failed"
        self.reason = reason
        self.detail = detail
        return self

    def as_dict(self) -> dict:
        return {"stage": self.name, "status": self.status,
                "reason": self.reason, **self.detail}


# ──────────────────────────── 1단계 수집 ────────────────────────────

def collect_from_sample(window: tuple[str, str]) -> tuple[list[dict], Stage]:
    """고정 샘플 파일을 문서로 바꾼다. 네트워크에 접속하지 않는다."""
    stage = Stage("collect(sample)")
    rows = read_csv(SAMPLE_INPUT)
    if not rows:
        return [], stage.fail("샘플 입력을 읽을 수 없다", path=str(SAMPLE_INPUT))

    collected_at = now()
    documents = []
    for row in rows:
        documents.append(nz.build_document(
            source_type=row.get("source_type") or "policy",
            source_name=row.get("source_name") or "샘플출처",
            title=row.get("title") or "",
            original_url=row.get("url") or "",
            raw_body=row.get("body") or "",
            published_raw=row.get("published_date") or "",
            collected_at=collected_at,
            window=window,
        ))
    return documents, stage.ok(input=shown_path(SAMPLE_INPUT),
                               rows_read=len(rows), documents=len(documents))


def collect_live(source_id: str | None, limit: int | None,
                 delay: float) -> tuple[list[dict], Stage]:
    """허용된 출처에서 수집한다. collect_policy 모듈을 쓴다."""
    stage = Stage("collect(live)")
    import collect_policy as cp  # noqa: PLC0415

    sources = cp.load_sources(source_id)
    if not sources:
        return [], stage.empty("수집 가능한 출처가 없다. "
                               "access_check_status 가 terms_ok 인 행이 필요하다")

    runnable = [s for s in sources if s.get("access_method") == "rss"]
    skipped = [s["source_id"] for s in sources if s.get("access_method") != "rss"]
    if not runnable:
        return [], stage.empty("rss 출처가 없다. html_list 수집기는 이슈 #37",
                               skipped=skipped)

    documents, logs = [], []
    for s in runnable:
        window = (s.get("collect_from") or "0001-01-01",
                  s.get("collect_to") or "9999-12-31")
        docs, log = cp.collect_rss(s, window, delay, limit)
        documents.extend(docs)
        logs.append(log)

    failed = [log["source_id"] for log in logs if log.get("error")]
    detail = {"sources": [log["source_id"] for log in logs],
              "skipped": skipped, "failed": failed,
              "documents": len(documents), "source_logs": logs}
    if not documents:
        return [], stage.empty("수집된 문서가 없다", **detail)
    if failed:
        stage.detail = detail
        stage.status = "ok"
        stage.reason = f"일부 출처 실패: {', '.join(failed)}"
        return documents, stage
    return documents, stage.ok(**detail)


# ──────────────────────────── 2단계 정제·중복 ────────────────────────────

def normalize_stage(documents: list[dict]) -> tuple[list[dict], Stage]:
    stage = Stage("normalize")
    if not documents:
        return [], stage.skip("수집 결과가 없다")

    existing = read_csv(DOCUMENTS_CSV)
    counts = nz.dedupe(documents, existing)
    merged, added = nz.merge_documents(existing, documents)
    nz.write_documents(DOCUMENTS_CSV, merged)
    problems = nz.validate_documents(merged)

    # 정제한 본문을 파일로 남긴다. 키워드 추출(이슈 #11)이 이 파일을 읽는다.
    # collect_policy.py 를 직접 돌릴 때와 같은 자리에 같은 이름으로 쓴다.
    raw_written = 0
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for row in documents:
        if row.get("status") != "analyzed" or not row.get("_text"):
            continue
        (RAW_DIR / f"{row['document_id']}.txt").write_text(row["_text"],
                                                           encoding="utf-8")
        raw_written += 1

    excluded: dict[str, int] = {}
    for row in documents:
        reason = row.get("exclusion_reason")
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1

    detail = {"out": shown_path(DOCUMENTS_CSV),
              "documents_total": len(merged), "documents_added": added,
              "analyzed": sum(1 for r in documents if r["status"] == "analyzed"),
              "excluded": excluded, "dedupe": counts,
              "raw_files_written": raw_written,
              "validation_problems": problems}
    if problems:
        return merged, stage.fail(f"규격 검증 문제 {len(problems)}건 (완료기준 C2)",
                                  **detail)
    return merged, stage.ok(**detail)


# ──────────────────────────── 3단계 키워드 추출 ────────────────────────────

def extract_from_sample(documents: list[dict]) -> Stage:
    """샘플 키워드 파일을 document_id 기준으로 바꿔 쓴다.

    샘플 파일은 url 로 적혀 있다. document_id 는 해시로 만들어지므로 샘플에
    해시를 박아 두면 규칙이 바뀔 때마다 고쳐야 하기 때문이다.
    """
    stage = Stage("extract(sample)")
    rows = read_csv(SAMPLE_KEYWORDS)
    if not rows:
        return stage.fail("샘플 키워드 파일을 읽을 수 없다", path=str(SAMPLE_KEYWORDS))

    by_url = {}
    for doc in documents:
        if doc.get("status") != "analyzed":
            continue
        by_url[nz.normalize_url(doc["original_url"])] = doc["document_id"]

    out_rows, unmatched = [], []
    for row in rows:
        url = row.get("url", "")
        did = by_url.get(nz.normalize_url(url)) if url else None
        if not did:
            unmatched.append(url)
            continue
        out_rows.append({
            "document_id": did,
            "keyword_id": row.get("keyword_id", ""),
            "keyword": row.get("keyword", ""),
            "matched_terms": row.get("matched_terms", ""),
            "occurrences": row.get("occurrences", ""),
            "dictionary_version": row.get("dictionary_version", ""),
        })

    KEYWORDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with KEYWORDS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=KEYWORD_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)

    detail = {"out": shown_path(KEYWORDS_CSV),
              "rows": len(out_rows), "unmatched_urls": unmatched,
              "note": "키워드 추출기(이슈 #11, 담당 A)의 대역이다"}
    if not out_rows:
        return stage.empty("샘플 키워드가 문서와 맞지 않는다", **detail)
    return stage.ok(**detail)


def extract_live() -> Stage:
    """A의 키워드 추출기가 있으면 돌린다. 없으면 건너뛴다."""
    stage = Stage("extract")
    if not EXTRACTOR.exists():
        return stage.skip(
            "src/extract_keywords.py 가 없다. 키워드 추출은 이슈 #11(담당 A)이다. "
            "추출기가 들어오면 이 단계가 자동으로 실행된다",
            expected=shown_path(EXTRACTOR))
    try:
        proc = subprocess.run([sys.executable, str(EXTRACTOR)],
                              capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return stage.fail(f"추출기 실행 실패: {type(exc).__name__}: {exc}")

    detail = {"returncode": proc.returncode,
              "stdout_tail": (proc.stdout or "").strip()[-500:],
              "stderr_tail": (proc.stderr or "").strip()[-500:]}
    if proc.returncode != 0:
        return stage.fail(f"추출기가 종료 코드 {proc.returncode} 로 끝났다", **detail)
    rows = read_csv(KEYWORDS_CSV)
    detail["rows"] = len(rows)
    if not rows:
        return stage.empty("추출 결과가 비어 있다", **detail)
    return stage.ok(**detail)


# ──────────────────────────── 4단계 집계 ────────────────────────────

def aggregate_stage(documents: list[dict], as_of: str | None) -> Stage:
    stage = Stage("aggregate")
    if not documents:
        return stage.skip("문서가 없다")

    keywords = read_csv(KEYWORDS_CSV)
    as_of_date = None
    if as_of:
        from datetime import date as _date  # noqa: PLC0415
        as_of_date = _date.fromisoformat(as_of)

    stats, totals = ag.aggregate(documents, keywords, as_of=as_of_date)
    ag.write_stats(STATS_CSV, stats)
    problems = ag.validate_stats(stats)

    detail = {"out": shown_path(STATS_CSV),
              "stats_rows": len(stats),
              "period_combinations": len(totals),
              "validation_problems": problems}
    if problems:
        return stage.fail(f"규격 검증 문제 {len(problems)}건 (완료기준 C4)", **detail)
    if not stats:
        return stage.empty(
            "집계 행이 없다. document_keywords.csv 가 비어 있기 때문이다. "
            "키워드 추출은 이슈 #11(담당 A)이다", **detail)
    return stage.ok(**detail)


# ──────────────────────────── 5단계 보고서 ────────────────────────────

def report_stage(sources_path: Path, period_types: tuple[str, ...],
                 out_dir: Path | None) -> Stage:
    stage = Stage("report")
    import generate_report as gr  # noqa: PLC0415

    documents = read_csv(DOCUMENTS_CSV)
    if not documents:
        return stage.skip("문서가 없다")
    keywords = read_csv(KEYWORDS_CSV)
    stats = read_csv(STATS_CSV)
    sources = read_csv(sources_path)

    made = []
    for period_type in period_types:
        period = gr.pick_period(stats, period_type, None)
        if period is None:
            dates = sorted(r["published_date"] for r in documents
                           if r.get("status") == "analyzed" and r.get("published_date"))
            if not dates:
                continue
            from datetime import date as _date  # noqa: PLC0415
            start, end = ag.period_of(_date.fromisoformat(dates[-1]), period_type)
            period = (start.isoformat(), end.isoformat())

        report = gr.build_report(documents=documents, keywords=keywords,
                                 stats=stats, sources=sources,
                                 period_type=period_type, period=period)
        path = gr.output_path(report, out_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gr.render(report), encoding="utf-8")
        made.append(shown_path(path))

    if not made:
        return stage.empty("기간을 정할 수 없어 보고서를 만들지 못했다")
    return stage.ok(reports=made)


# ──────────────────────────── 실행 ────────────────────────────

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="종단 실행 (B 담당)")
    ap.add_argument("--sample", action="store_true",
                    help="data/sample/ 의 고정 샘플로 돌린다. 네트워크·API 키 불필요")
    ap.add_argument("--with-report", action="store_true", help="보고서까지 생성")
    ap.add_argument("--source-id", help="실제 자료 수집 시 특정 출처만")
    ap.add_argument("--limit", type=int, help="출처당 처리 항목 수 상한")
    ap.add_argument("--delay", type=float, default=1.0, help="요청 간 대기(초)")
    ap.add_argument("--as-of", help="부분 기간 판정 기준일 YYYY-MM-DD")
    ap.add_argument("--from", dest="date_from", default="2026-01-01",
                    help="샘플 모드의 수집 기간 시작일")
    ap.add_argument("--to", dest="date_to", default="2026-12-31",
                    help="샘플 모드의 수집 기간 종료일")
    ap.add_argument("--report-out-dir", type=Path, help="보고서 저장 폴더")
    args = ap.parse_args(argv)

    mode = "sample" if args.sample else "live"
    print(f"종단 실행 ({mode})")
    print()

    stages: list[Stage] = []

    # 1단계 수집
    if args.sample:
        documents, stage = collect_from_sample((args.date_from, args.date_to))
    else:
        documents, stage = collect_live(args.source_id, args.limit, args.delay)
    stages.append(stage)
    print(f"[1/5] {stage.name}: {stage.status}"
          + (f" — {stage.reason}" if stage.reason else ""))

    # 2단계 정제·중복
    merged, stage = normalize_stage(documents)
    stages.append(stage)
    print(f"[2/5] {stage.name}: {stage.status}"
          + (f" — {stage.reason}" if stage.reason else ""))

    # 3단계 추출
    stage = extract_from_sample(documents) if args.sample else extract_live()
    stages.append(stage)
    print(f"[3/5] {stage.name}: {stage.status}"
          + (f" — {stage.reason}" if stage.reason else ""))

    # 4단계 집계
    stage = aggregate_stage(merged, args.as_of)
    stages.append(stage)
    print(f"[4/5] {stage.name}: {stage.status}"
          + (f" — {stage.reason}" if stage.reason else ""))

    # 5단계 보고서
    if args.with_report:
        sources_path = SAMPLE_SOURCES if args.sample else CONFIG_SOURCES
        stage = report_stage(sources_path, ("weekly", "monthly"), args.report_out_dir)
    else:
        stage = Stage("report").skip("--with-report 를 주지 않았다")
    stages.append(stage)
    print(f"[5/5] {stage.name}: {stage.status}"
          + (f" — {stage.reason}" if stage.reason else ""))

    # 종단 실행 기록
    failed = [s.name for s in stages if s.status == "failed"]
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    log_path = PROCESSED_DIR / f"pipeline_log_{stamp}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "run_at": now(),
        "mode": mode,
        "args": {k: (str(v) if isinstance(v, Path) else v)
                 for k, v in vars(args).items()},
        "stages": [s.as_dict() for s in stages],
        "failed_stages": failed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    for name, path in (("documents.csv", DOCUMENTS_CSV),
                       ("document_keywords.csv", KEYWORDS_CSV),
                       ("keyword_stats.csv", STATS_CSV)):
        if path.exists():
            rows = max(0, sum(1 for _ in path.open(encoding="utf-8")) - 1)
            print(f"  {name}: {rows}행")
        else:
            print(f"  {name}: 없음")
    print(f"\n실행 기록 -> data/processed/{log_path.name}")

    if failed:
        print(f"\n실패한 단계: {', '.join(failed)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
