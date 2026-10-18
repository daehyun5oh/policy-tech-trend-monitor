"""종단 실행 검증 (이슈 #21)

완료기준 "고정 샘플로 CSV 3종이 생성되고, 오류 상황에서 사유가 기록된다" 를
확인한다. 네트워크에 접속하지 않는다.

표준 라이브러리 `unittest` 로 쓴다 (`docs/environment.md` 테스트 규칙).
출력 경로를 임시 폴더로 바꿔 실행하므로 실제 data/processed 를 건드리지 않는다.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pipeline as pl  # noqa: E402

WINDOW = ("2026-01-01", "2026-12-31")

# 임시 폴더로 바꿔치울 모듈 속성
PATCHED = ("PROCESSED_DIR", "RAW_DIR", "DOCUMENTS_CSV", "KEYWORDS_CSV", "STATS_CSV",
           "EXTRACTOR", "SAMPLE_INPUT", "SAMPLE_KEYWORDS")


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class SandboxCase(unittest.TestCase):
    """출력 경로만 임시 폴더로 바꾼다. 입력은 실제 data/sample/ 을 쓴다."""

    def setUp(self):
        self._saved = {name: getattr(pl, name) for name in PATCHED}
        self.addCleanup(self._restore)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)

        self.processed = self.tmp_path / "processed"
        self.raw = self.tmp_path / "raw"
        pl.PROCESSED_DIR = self.processed
        pl.RAW_DIR = self.raw
        pl.DOCUMENTS_CSV = self.processed / "documents.csv"
        pl.KEYWORDS_CSV = self.processed / "document_keywords.csv"
        pl.STATS_CSV = self.processed / "keyword_stats.csv"

    def _restore(self):
        for name, value in self._saved.items():
            setattr(pl, name, value)


# ──────────────────────────── 샘플 종단 실행 ────────────────────────────

class SampleRunTest(SandboxCase):

    def setUp(self):
        super().setUp()
        self.documents, s1 = pl.collect_from_sample(WINDOW)
        self.merged, s2 = pl.normalize_stage(self.documents)
        s3 = pl.extract_from_sample(self.documents)
        s4 = pl.aggregate_stage(self.merged, "2026-10-20")
        self.stages = [s1, s2, s3, s4]

    def test_all_stages_succeed(self):
        self.assertEqual([s.status for s in self.stages], ["ok", "ok", "ok", "ok"])

    def test_three_csv_files_created(self):
        for name in ("documents.csv", "document_keywords.csv", "keyword_stats.csv"):
            with self.subTest(name=name):
                self.assertTrue((self.processed / name).exists(),
                                f"{name} 이 만들어지지 않았다")

    def test_sample_input_has_seven_rows(self):
        """샘플 입력 7행: 정상 5건 + 발행일 없음 1건 + 중복 1건."""
        self.assertEqual(len(self.documents), 7)

    def test_undated_row_is_excluded(self):
        rows = read(self.processed / "documents.csv")
        undated = [r for r in rows if r["exclusion_reason"] == "no_published_date"]
        self.assertEqual(len(undated), 1)
        self.assertEqual(undated[0]["published_date"], "", "수집일로 대체하면 안 된다")

    def test_url_normalized_duplicate_is_dropped(self):
        """utm_source 만 다른 행은 같은 문서로 판정돼 한 행만 남는다."""
        self.assertEqual(self.stages[1].detail["dedupe"]["same_document"], 1)
        rows = read(self.processed / "documents.csv")
        self.assertEqual(len(rows), 6, "7행 중 1행은 같은 문서로 걸러져야 한다")
        ids = [r["document_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)), "document_id 중복")

    def test_analyzed_count(self):
        rows = read(self.processed / "documents.csv")
        self.assertEqual(sum(1 for r in rows if r["status"] == "analyzed"), 5)

    def test_hwp_block_removed_from_raw_text(self):
        """샘플 본문에 넣어둔 HWP 메타데이터가 정제된다."""
        texts = [p.read_text(encoding="utf-8") for p in self.raw.glob("*.txt")]
        self.assertTrue(texts, "원문 파일이 저장되지 않았다")
        joined = "\n".join(texts)
        self.assertNotIn("hwpjson", joined)
        self.assertNotIn("documentPr", joined)

    def test_phone_number_removed(self):
        """담당자 연락처 형태를 지운다 (기획안 §11)."""
        joined = "\n".join(p.read_text(encoding="utf-8")
                           for p in self.raw.glob("*.txt"))
        self.assertNotIn("044-000-0000", joined)

    def test_raw_text_written_for_analyzed_only(self):
        self.assertEqual(len(list(self.raw.glob("*.txt"))), 5)

    def test_keyword_rows_mapped_to_document_ids(self):
        """샘플 키워드는 url 로 적혀 있고, 파이프라인이 document_id 로 바꿔 넣는다."""
        rows = read(self.processed / "document_keywords.csv")
        self.assertEqual(len(rows), 6)
        self.assertEqual(list(rows[0].keys()), pl.KEYWORD_FIELDS)
        doc_ids = {r["document_id"] for r in read(self.processed / "documents.csv")}
        for row in rows:
            with self.subTest(document_id=row["document_id"]):
                self.assertIn(row["document_id"], doc_ids)
                self.assertTrue(row["document_id"], "document_id 가 비었다")
        self.assertEqual(self.stages[2].detail["unmatched_urls"], [])

    def test_stats_generated(self):
        rows = read(self.processed / "keyword_stats.csv")
        self.assertTrue(rows)
        self.assertLessEqual({r["source_type"] for r in rows}, {"news", "policy", "all"})
        # 일간은 이슈 #26 요청으로 뒤에 들어왔다(PR #46). 두 상태 모두에서
        # 통과하도록 '허용값 안에 있고 주간·월간은 반드시 있다' 로 본다
        period_types = {r["period_type"] for r in rows}
        self.assertLessEqual(period_types, {"daily", "weekly", "monthly"})
        self.assertIn("weekly", period_types)
        self.assertIn("monthly", period_types)

    def test_validation_passes_in_both_stages(self):
        self.assertEqual(self.stages[1].detail["validation_problems"], [])
        self.assertEqual(self.stages[3].detail["validation_problems"], [])


# ──────────────────────────── 빈 결과·오류 처리 ────────────────────────────

class EmptyAndErrorTest(SandboxCase):

    def test_normalize_skipped_when_no_documents(self):
        merged, stage = pl.normalize_stage([])
        self.assertEqual(stage.status, "skipped")
        self.assertIn("수집 결과가 없다", stage.reason)
        self.assertEqual(merged, [])

    def test_aggregate_skipped_when_no_documents(self):
        self.assertEqual(pl.aggregate_stage([], None).status, "skipped")

    def test_aggregate_empty_without_keywords(self):
        """키워드 파일이 없으면 집계 행이 0건이고 사유가 남는다."""
        documents, _ = pl.collect_from_sample(WINDOW)
        merged, _ = pl.normalize_stage(documents)
        stage = pl.aggregate_stage(merged, "2026-10-20")
        self.assertEqual(stage.status, "empty")
        self.assertIn("document_keywords.csv", stage.reason)
        self.assertIn("이슈 #11", stage.reason)
        self.assertEqual(stage.detail["stats_rows"], 0)

    def test_extract_live_skips_when_extractor_missing(self):
        """A의 추출기가 없으면 건너뛰고 사유를 남긴다."""
        pl.EXTRACTOR = ROOT / "src" / "no_such_extractor.py"
        stage = pl.extract_live()
        self.assertEqual(stage.status, "skipped")
        self.assertIn("이슈 #11", stage.reason)

    def test_collect_from_sample_fails_on_missing_input(self):
        pl.SAMPLE_INPUT = self.tmp_path / "없는파일.csv"
        documents, stage = pl.collect_from_sample(WINDOW)
        self.assertEqual(stage.status, "failed")
        self.assertEqual(documents, [])

    def test_extract_from_sample_fails_on_missing_file(self):
        pl.SAMPLE_KEYWORDS = self.tmp_path / "없는파일.csv"
        self.assertEqual(pl.extract_from_sample([]).status, "failed")

    def test_extract_from_sample_empty_when_urls_do_not_match(self):
        """문서와 키워드의 주소가 맞지 않으면 빈 결과로 기록한다."""
        stage = pl.extract_from_sample([])
        self.assertEqual(stage.status, "empty")
        self.assertTrue(stage.detail["unmatched_urls"])


# ──────────────────────────── Stage 기록 ────────────────────────────

class StageTest(unittest.TestCase):

    def test_stage_records_reason(self):
        out = pl.Stage("시험").fail("무엇이 왜 실패했는지", code=3).as_dict()
        self.assertEqual(out["stage"], "시험")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "무엇이 왜 실패했는지")
        self.assertEqual(out["code"], 3)

    def test_stage_default_is_skipped(self):
        self.assertEqual(pl.Stage("시험").status, "skipped")

    def test_shown_path_inside_and_outside_repo(self):
        inside = pl.shown_path(ROOT / "data" / "processed" / "documents.csv")
        self.assertEqual(inside.replace("\\", "/"), "data/processed/documents.csv")
        with tempfile.TemporaryDirectory() as tmp:
            outside = pl.shown_path(Path(tmp) / "x.md")
            self.assertIn(tmp, outside)


# ──────────────────────────── 재현성 (C8) ────────────────────────────

class DeterminismTest(SandboxCase):

    def test_sample_run_is_deterministic(self):
        first, _ = pl.collect_from_sample(WINDOW)
        second, _ = pl.collect_from_sample(WINDOW)
        key = lambda docs: [(d["document_id"], d["content_hash"], d["status"])  # noqa: E731
                            for d in docs]
        self.assertEqual(key(first), key(second))


if __name__ == "__main__":
    unittest.main()
