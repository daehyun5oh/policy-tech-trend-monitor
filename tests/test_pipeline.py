"""종단 실행 검증 (이슈 #21)

완료기준 "고정 샘플로 CSV 3종이 생성되고, 오류 상황에서 사유가 기록된다" 를
확인한다. 네트워크에 접속하지 않는다.

출력 경로를 tmp_path 로 바꿔 실행하므로 실제 data/processed 를 건드리지 않는다.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pipeline as pl  # noqa: E402

WINDOW = ("2026-01-01", "2026-12-31")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """출력 경로만 tmp_path 로 바꾼다. 입력은 실제 data/sample/ 을 쓴다."""
    processed = tmp_path / "processed"
    raw = tmp_path / "raw"
    monkeypatch.setattr(pl, "PROCESSED_DIR", processed)
    monkeypatch.setattr(pl, "RAW_DIR", raw)
    monkeypatch.setattr(pl, "DOCUMENTS_CSV", processed / "documents.csv")
    monkeypatch.setattr(pl, "KEYWORDS_CSV", processed / "document_keywords.csv")
    monkeypatch.setattr(pl, "STATS_CSV", processed / "keyword_stats.csv")
    return {"processed": processed, "raw": raw}


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ──────────────────────────── 샘플 종단 실행 ────────────────────────────

@pytest.fixture
def ran(sandbox):
    documents, s1 = pl.collect_from_sample(WINDOW)
    merged, s2 = pl.normalize_stage(documents)
    s3 = pl.extract_from_sample(documents)
    s4 = pl.aggregate_stage(merged, "2026-10-20")
    return {"sandbox": sandbox, "documents": documents, "merged": merged,
            "stages": [s1, s2, s3, s4]}


def test_all_stages_succeed(ran):
    assert [s.status for s in ran["stages"]] == ["ok", "ok", "ok", "ok"]


def test_three_csv_files_created(ran):
    processed = ran["sandbox"]["processed"]
    for name in ("documents.csv", "document_keywords.csv", "keyword_stats.csv"):
        assert (processed / name).exists(), f"{name} 이 만들어지지 않았다"


def test_sample_input_has_seven_rows(ran):
    """샘플 입력 7행: 정상 5건 + 발행일 없음 1건 + 중복 1건."""
    assert len(ran["documents"]) == 7


def test_undated_row_is_excluded(ran):
    rows = read(ran["sandbox"]["processed"] / "documents.csv")
    undated = [r for r in rows if r["exclusion_reason"] == "no_published_date"]
    assert len(undated) == 1
    assert undated[0]["published_date"] == "", "수집일로 대체하면 안 된다"


def test_url_normalized_duplicate_is_dropped(ran):
    """utm_source 만 다른 행은 같은 문서로 판정돼 한 행만 남는다."""
    s2 = ran["stages"][1]
    assert s2.detail["dedupe"]["same_document"] == 1
    rows = read(ran["sandbox"]["processed"] / "documents.csv")
    assert len(rows) == 6, "7행 중 1행은 같은 문서로 걸러져야 한다"
    ids = [r["document_id"] for r in rows]
    assert len(ids) == len(set(ids)), "document_id 중복"


def test_analyzed_count(ran):
    rows = read(ran["sandbox"]["processed"] / "documents.csv")
    assert sum(1 for r in rows if r["status"] == "analyzed") == 5


def test_hwp_block_removed_from_raw_text(ran):
    """샘플 본문에 넣어둔 HWP 메타데이터가 정제된다."""
    raw_dir = ran["sandbox"]["raw"]
    texts = [p.read_text(encoding="utf-8") for p in raw_dir.glob("*.txt")]
    assert texts, "원문 파일이 저장되지 않았다"
    joined = "\n".join(texts)
    assert "hwpjson" not in joined
    assert "documentPr" not in joined


def test_phone_number_removed(ran):
    """담당자 연락처 형태를 지운다 (기획안 §11)."""
    raw_dir = ran["sandbox"]["raw"]
    joined = "\n".join(p.read_text(encoding="utf-8") for p in raw_dir.glob("*.txt"))
    assert "044-000-0000" not in joined


def test_raw_text_written_for_analyzed_only(ran):
    raw_dir = ran["sandbox"]["raw"]
    assert len(list(raw_dir.glob("*.txt"))) == 5


def test_keyword_rows_mapped_to_document_ids(ran):
    """샘플 키워드는 url 로 적혀 있고, 파이프라인이 document_id 로 바꿔 넣는다."""
    rows = read(ran["sandbox"]["processed"] / "document_keywords.csv")
    assert len(rows) == 6
    assert list(rows[0].keys()) == pl.KEYWORD_FIELDS
    doc_ids = {r["document_id"] for r in read(
        ran["sandbox"]["processed"] / "documents.csv")}
    for row in rows:
        assert row["document_id"] in doc_ids
        assert row["document_id"], "document_id 가 비었다"
    assert ran["stages"][2].detail["unmatched_urls"] == []


def test_stats_generated(ran):
    rows = read(ran["sandbox"]["processed"] / "keyword_stats.csv")
    assert rows
    assert {r["source_type"] for r in rows} <= {"news", "policy", "all"}
    assert {r["period_type"] for r in rows} == {"weekly", "monthly"}


def test_validation_passes_in_both_stages(ran):
    assert ran["stages"][1].detail["validation_problems"] == []
    assert ran["stages"][3].detail["validation_problems"] == []


# ──────────────────────────── 빈 결과·오류 처리 ────────────────────────────

def test_normalize_skipped_when_no_documents(sandbox):
    merged, stage = pl.normalize_stage([])
    assert stage.status == "skipped"
    assert "수집 결과가 없다" in stage.reason
    assert merged == []


def test_aggregate_skipped_when_no_documents(sandbox):
    stage = pl.aggregate_stage([], None)
    assert stage.status == "skipped"


def test_aggregate_empty_without_keywords(sandbox):
    """키워드 파일이 없으면 집계 행이 0건이고 사유가 남는다."""
    documents, _ = pl.collect_from_sample(WINDOW)
    merged, _ = pl.normalize_stage(documents)
    stage = pl.aggregate_stage(merged, "2026-10-20")
    assert stage.status == "empty"
    assert "document_keywords.csv" in stage.reason
    assert "이슈 #11" in stage.reason
    assert stage.detail["stats_rows"] == 0


def test_extract_live_skips_when_extractor_missing(sandbox, monkeypatch):
    """A의 추출기가 없으면 건너뛰고 사유를 남긴다."""
    monkeypatch.setattr(pl, "EXTRACTOR", ROOT / "src" / "no_such_extractor.py")
    stage = pl.extract_live()
    assert stage.status == "skipped"
    assert "이슈 #11" in stage.reason


def test_collect_from_sample_fails_on_missing_input(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "SAMPLE_INPUT", tmp_path / "없는파일.csv")
    documents, stage = pl.collect_from_sample(WINDOW)
    assert stage.status == "failed"
    assert documents == []


def test_extract_from_sample_fails_on_missing_file(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "SAMPLE_KEYWORDS", tmp_path / "없는파일.csv")
    stage = pl.extract_from_sample([])
    assert stage.status == "failed"


def test_extract_from_sample_empty_when_urls_do_not_match(sandbox):
    """문서와 키워드의 주소가 맞지 않으면 빈 결과로 기록한다."""
    stage = pl.extract_from_sample([])
    assert stage.status == "empty"
    assert stage.detail["unmatched_urls"]


# ──────────────────────────── Stage 기록 ────────────────────────────

def test_stage_records_reason():
    stage = pl.Stage("시험").fail("무엇이 왜 실패했는지", code=3)
    out = stage.as_dict()
    assert out["stage"] == "시험"
    assert out["status"] == "failed"
    assert out["reason"] == "무엇이 왜 실패했는지"
    assert out["code"] == 3


def test_stage_default_is_skipped():
    assert pl.Stage("시험").status == "skipped"


def test_shown_path_inside_and_outside_repo(tmp_path):
    inside = pl.shown_path(ROOT / "data" / "processed" / "documents.csv")
    assert inside.replace("\\", "/") == "data/processed/documents.csv"
    outside = pl.shown_path(tmp_path / "x.md")
    assert str(tmp_path) in outside


# ──────────────────────────── 재현성 (C8) ────────────────────────────

def test_sample_run_is_deterministic(sandbox):
    first, _ = pl.collect_from_sample(WINDOW)
    second, _ = pl.collect_from_sample(WINDOW)
    key = lambda docs: [(d["document_id"], d["content_hash"], d["status"])  # noqa: E731
                        for d in docs]
    assert key(first) == key(second)
