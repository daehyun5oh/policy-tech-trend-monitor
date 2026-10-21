"""정제·중복 제거 모듈 검증 (이슈 #9)

완료기준 C2(필수값 누락·ID 중복·대표 문서 연결)와 C8(결정성)을 고정 입력으로
확인한다. 네트워크에 접속하지 않는다.

표준 라이브러리 `unittest` 로 쓴다 (`docs/environment.md` 테스트 규칙).

실행
    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import normalize as nz  # noqa: E402

WINDOW = ("2026-05-01", "2026-10-31")
NOW = "2026-10-17T14:00:00+09:00"


def make(title="제목", url="https://example.go.kr/view.do?nttSeqNo=1",
         body="본문", published="2026.10.15", **kw):
    return nz.build_document(
        source_type="policy", source_name="시험출처", title=title,
        original_url=url, raw_body=body, published_raw=published,
        collected_at=NOW, window=WINDOW, **kw)


def rows_for_csv(docs):
    return [{k: d.get(k, "") for k in nz.DOCUMENT_FIELDS} for d in docs]


# ──────────────────────────── URL 정규화 ────────────────────────────

class NormalizeUrlTest(unittest.TestCase):

    def test_normalize_url(self):
        cases = [
            ("HTTPS://Example.GO.KR/a/b/", "https://example.go.kr/a/b"),
            ("https://example.go.kr:443/a", "https://example.go.kr/a"),
            ("http://example.go.kr:80/a", "http://example.go.kr/a"),
            ("https://example.go.kr/a#frag", "https://example.go.kr/a"),
            ("https://example.go.kr/a?utm_source=x&id=1", "https://example.go.kr/a?id=1"),
            ("https://example.go.kr/a?b=2&a=1", "https://example.go.kr/a?a=1&b=2"),
            ("https://example.go.kr/", "https://example.go.kr/"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(nz.normalize_url(raw), expected)

    def test_normalize_url_keeps_tracking_free_params(self):
        url = "https://example.go.kr/v?fbclid=zz&gclid=yy&nttSeqNo=7"
        self.assertEqual(nz.normalize_url(url), "https://example.go.kr/v?nttSeqNo=7")


# ──────────────────────────── 날짜 ────────────────────────────

class PublishedDateTest(unittest.TestCase):

    def test_parse_published_date(self):
        cases = [
            ("2026.10.15", "2026-10-15"),
            ("2026-10-15", "2026-10-15"),
            ("2026/10/15", "2026-10-15"),
            ("등록일 : 2026.10.15 ", "2026-10-15"),
            ("Thu, 15 Oct 2026 09:00:00 +0900", "2026-10-15"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(nz.parse_published_date(raw), expected)

    def test_parse_published_date_unparseable(self):
        for raw in ["", "   ", "날짜없음", "2026.13.45"]:
            with self.subTest(raw=raw):
                self.assertIsNone(nz.parse_published_date(raw))

    def test_window_is_inclusive(self):
        self.assertTrue(nz.in_window("2026-05-01", *WINDOW))
        self.assertTrue(nz.in_window("2026-10-31", *WINDOW))
        self.assertFalse(nz.in_window("2026-04-30", *WINDOW))
        self.assertFalse(nz.in_window("2026-11-01", *WINDOW))


# ──────────────────────────── 텍스트 정제 ────────────────────────────

class CleanTextTest(unittest.TestCase):

    def test_clean_text_removes_escaped_hwp_block(self):
        """HWP 블록이 이스케이프된 상태로 와도 제거된다. 이슈 #8에서 고친 버그."""
        raw = ('본문 시작&lt;!--[data-hwpjson]{&#034;documentPr&#034;: '
               '{&#034;dn&#034;: &#034;test.hwp&#034;}}--&gt;본문 끝')
        out = nz.clean_text(raw)
        self.assertNotIn("hwpjson", out)
        self.assertNotIn("documentPr", out)
        self.assertIn("본문 시작", out)

    def test_clean_text_removes_unclosed_hwp_block(self):
        """닫는 --> 없이 잘려 와도 시작 지점부터 버린다."""
        raw = "본문&lt;!--[data-hwpjson]{&#034;a&#034;: 1"
        out = nz.clean_text(raw)
        self.assertNotIn("hwpjson", out)
        self.assertTrue(out.startswith("본문"))

    def test_clean_text_double_escaped_entities(self):
        self.assertNotIn("\xa0", nz.clean_text("가&amp;nbsp;나"))

    def test_clean_text_strips_phone_numbers(self):
        """담당자 연락처를 지운다 (기획안 §11)."""
        out = nz.clean_text("문의 044-000-1234 기초연구진흥과")
        self.assertNotIn("044-000-1234", out)
        self.assertIn("기초연구진흥과", out)

    def test_clean_text_keeps_phone_when_disabled(self):
        out = nz.clean_text("문의 044-000-1234", strip_phone=False)
        self.assertIn("044-000-1234", out)

    def test_clean_text_drops_script_and_style(self):
        raw = "<style>p{color:red}</style>본문<script>alert(1)</script>"
        self.assertEqual(nz.clean_text(raw), "본문")

    def test_clean_text_block_tags_become_newlines(self):
        self.assertEqual(nz.clean_text("<p>첫째</p><p>둘째</p>"), "첫째\n둘째")


# ──────────────────────────── 해시·식별자 결정성 ────────────────────────────

class HashAndIdTest(unittest.TestCase):

    def test_content_hash_is_deterministic(self):
        self.assertEqual(nz.content_hash("가 나"), nz.content_hash("가 나"))

    def test_content_hash_ignores_whitespace_shape(self):
        self.assertEqual(nz.content_hash("가  나\n"), nz.content_hash(" 가 나 "))

    def test_content_hash_length(self):
        self.assertEqual(len(nz.content_hash("x")), 64)

    def test_document_id_prefers_source_document_id(self):
        a = nz.make_document_id("policy", "출처", "nttSeqNo=1", "https://a.kr/1")
        b = nz.make_document_id("policy", "출처", "nttSeqNo=1", "https://b.kr/2")
        self.assertEqual(a, b, "원문 식별자가 같으면 URL이 달라도 같은 ID")

    def test_document_id_falls_back_to_url(self):
        a = nz.make_document_id("policy", "출처", "", "https://a.kr/1")
        b = nz.make_document_id("policy", "출처", "", "https://a.kr/1")
        c = nz.make_document_id("policy", "출처", "", "https://a.kr/2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_document_id_has_source_type_prefix(self):
        self.assertTrue(
            nz.make_document_id("policy", "출처", "", "https://a.kr/1").startswith("policy-"))

    def test_extract_source_document_id(self):
        self.assertEqual(nz.extract_source_document_id("https://a.kr/v?nttSeqNo=99"),
                         "nttSeqNo=99")
        self.assertEqual(nz.extract_source_document_id("https://a.kr/v"), "")


# ──────────────────────────── 문서 행 만들기 ────────────────────────────

class BuildDocumentTest(unittest.TestCase):

    def test_build_document_analyzed(self):
        row = make()
        self.assertEqual(row["status"], "analyzed")
        self.assertEqual(row["exclusion_reason"], "")
        self.assertEqual(row["published_date"], "2026-10-15")
        self.assertEqual(row["analysis_scope"], "title_body")

    def test_build_document_title_only_scope(self):
        self.assertEqual(make(body="")["analysis_scope"], "title")

    def test_build_document_excludes_missing_date(self):
        """발행일을 확인할 수 없으면 수집일로 대체하지 않고 제외한다 (기획안 §3)."""
        row = make(published="확인불가")
        self.assertEqual(row["status"], "excluded")
        self.assertEqual(row["exclusion_reason"], "no_published_date")
        self.assertEqual(row["published_date"], "")

    def test_build_document_excludes_out_of_period(self):
        self.assertEqual(make(published="2026.01.05")["exclusion_reason"], "out_of_period")

    def test_build_document_excludes_empty_title(self):
        self.assertEqual(make(title="   ")["exclusion_reason"], "no_text")

    def test_failed_document_requires_known_reason(self):
        with self.assertRaises(ValueError):
            nz.failed_document(source_type="policy", source_name="출처",
                               original_url="https://a.kr/1", collected_at=NOW,
                               reason="잘못된사유")


# ──────────────────────────── 중복 제거 ────────────────────────────

class DedupeTest(unittest.TestCase):

    def test_dedupe_by_source_document_id(self):
        """1단계: 원문 식별자가 같으면 document_id 까지 같다 → 같은 문서."""
        a = make(url="https://a.kr/view.do?nttSeqNo=5", body="본문 하나")
        b = make(url="https://a.kr/other.do?nttSeqNo=5", body="본문 둘")
        self.assertEqual(a["document_id"], b["document_id"])
        counts = nz.dedupe([a, b])
        self.assertEqual(counts["same_document"], 1)
        self.assertEqual(counts["duplicate"], 0)
        self.assertEqual(a["status"], "analyzed")
        self.assertEqual(b["exclusion_reason"], "duplicate")
        self.assertIs(b.get("_same_id"), True)

    def test_dedupe_by_normalized_url(self):
        """2단계: 주소가 정규화 후 같으면 document_id 까지 같다 → 같은 문서."""
        a = make(url="https://a.kr/notice/7", body="본문 하나")
        b = make(url="HTTPS://A.KR/notice/7/?utm_source=x", body="본문 둘")
        self.assertEqual(a["document_id"], b["document_id"])
        self.assertEqual(nz.dedupe([a, b])["same_document"], 1)

    def test_dedupe_by_content_hash(self):
        """3단계: 주소·식별자가 달라 ID가 다르면 duplicate_of 로 연결한다."""
        a = make(url="https://a.kr/v?nttSeqNo=1", body="같은 본문")
        b = make(url="https://b.kr/v?nttSeqNo=2", body="같은 본문")
        self.assertNotEqual(a["document_id"], b["document_id"])
        counts = nz.dedupe([a, b])
        self.assertEqual(counts["duplicate"], 1)
        self.assertEqual(counts["same_document"], 0)
        self.assertEqual(b["duplicate_of"], a["document_id"])
        self.assertNotEqual(b["duplicate_of"], b["document_id"])

    def test_dedupe_keeps_first_as_representative(self):
        a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
        b = make(url="https://b.kr/v?nttSeqNo=2", body="본문 하나")
        nz.dedupe([a, b])
        self.assertEqual(a["status"], "analyzed")
        self.assertEqual(b["status"], "excluded")
        self.assertEqual(b["duplicate_of"], a["document_id"])

    def test_dedupe_against_existing_rows(self):
        """이전 실행 결과와도 비교한다."""
        first = make(url="https://a.kr/v?nttSeqNo=7", body="본문")
        nz.dedupe([first])
        existing = [{k: first[k] for k in nz.DOCUMENT_FIELDS}]
        again = make(url="https://a.kr/v?nttSeqNo=7", body="본문")
        counts = nz.dedupe([again], existing)
        self.assertEqual(counts["same_document"], 1)
        self.assertEqual(again["status"], "excluded")

    def test_dedupe_content_match_against_existing(self):
        first = make(url="https://a.kr/v?nttSeqNo=7", body="같은 본문")
        nz.dedupe([first])
        existing = [{k: first[k] for k in nz.DOCUMENT_FIELDS}]
        other = make(url="https://b.kr/v?nttSeqNo=9", body="같은 본문")
        counts = nz.dedupe([other], existing)
        self.assertEqual(counts["duplicate"], 1)
        self.assertEqual(other["duplicate_of"], first["document_id"])

    def test_dedupe_does_not_touch_excluded_rows(self):
        out = make(published="2026.01.01")
        nz.dedupe([out])
        self.assertEqual(out["exclusion_reason"], "out_of_period",
                         "제외 사유가 덮이지 않아야 한다")

    def test_dedupe_allows_different_documents(self):
        a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
        b = make(url="https://a.kr/v?nttSeqNo=2", body="본문 둘")
        self.assertEqual(nz.dedupe([a, b])["duplicate"], 0)


# ──────────────────────────── 검증 함수 (완료기준 C2) ────────────────────────────

class ValidateTest(unittest.TestCase):

    def test_validate_passes_on_clean_set(self):
        """같은 문서(c)는 merge 단계에서 걸러지므로 CSV에는 a, b 와 내용중복 d 가 남는다."""
        a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
        b = make(url="https://a.kr/v?nttSeqNo=2", body="본문 둘")
        d = make(url="https://b.kr/v?nttSeqNo=3", body="본문 하나")
        nz.dedupe([a, b, d])
        merged, _ = nz.merge_documents([], [a, b, d])
        self.assertEqual(nz.validate_documents(merged), [])

    def test_merge_drops_same_document_rows(self):
        a = make(url="https://a.kr/v?nttSeqNo=1", body="본문")
        b = make(url="https://a.kr/v?nttSeqNo=1", body="본문")
        nz.dedupe([a, b])
        merged, added = nz.merge_documents([], [a, b])
        self.assertEqual(added, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(nz.validate_documents(merged), [])

    def test_validate_catches_duplicate_document_id(self):
        a = make(url="https://a.kr/v?nttSeqNo=1")
        problems = nz.validate_documents(rows_for_csv([a, a]))
        self.assertTrue(any("중복" in p for p in problems))

    def test_validate_catches_missing_required_field(self):
        rows = rows_for_csv([make()])
        rows[0]["published_date"] = ""
        problems = nz.validate_documents(rows)
        self.assertTrue(any("published_date" in p for p in problems))

    def test_validate_catches_duplicate_without_link(self):
        a = make(url="https://a.kr/v?nttSeqNo=1", body="같은 본문")
        b = make(url="https://b.kr/v?nttSeqNo=2", body="같은 본문")
        nz.dedupe([a, b])
        rows = rows_for_csv([a, b])
        rows[1]["duplicate_of"] = ""
        problems = nz.validate_documents(rows)
        self.assertTrue(any("duplicate_of" in p for p in problems))

    def test_validate_catches_dangling_duplicate_of(self):
        rows = rows_for_csv([make()])
        rows[0]["status"] = "excluded"
        rows[0]["exclusion_reason"] = "duplicate"
        rows[0]["duplicate_of"] = "policy-없는아이디"
        problems = nz.validate_documents(rows)
        self.assertTrue(any("가리키는 문서가 없다" in p for p in problems))

    def test_validate_catches_bad_enum(self):
        rows = rows_for_csv([make()])
        rows[0]["status"] = "done"
        problems = nz.validate_documents(rows)
        self.assertTrue(any("status" in p for p in problems))


# ──────────────────────────── CSV 왕복 ────────────────────────────

# ──────────────────────── 선택 열 text_chars (PR #60 합의) ────────────────────────

class TextCharsTest(unittest.TestCase):
    """정제를 마친 본문 글자 수. 제목은 세지 않는다.

    규격은 `docs/data_dictionary.md` §2 이고, A의 `collect_news.py` 가 뉴스 행에
    같은 규칙을 쓴다. 제목만 분석한 행은 `0`, 제외·실패 행은 빈 값이다.
    """

    def test_column_is_in_document_fields(self):
        self.assertIn("text_chars", nz.DOCUMENT_FIELDS)

    def test_counts_body_only(self):
        """제목 길이는 포함하지 않는다."""
        row = make(title="아주 긴 제목을 넣어 본다", body="본문 열 글자")
        self.assertEqual(row["text_chars"], str(len("본문 열 글자")))

    def test_counts_text_after_cleaning(self):
        """정제 뒤의 글자 수다. 태그는 세지 않는다."""
        row = make(body="<p>첫째</p><p>둘째</p>")
        self.assertEqual(row["text_chars"], str(len("첫째\n둘째")))

    def test_zero_when_no_body(self):
        row = make(body="")
        self.assertEqual(row["analysis_scope"], "title")
        self.assertEqual(row["text_chars"], "0")

    def test_empty_when_excluded(self):
        for kw in ({"published": "확인불가"}, {"published": "2026.01.05"}, {"title": "  "}):
            with self.subTest(**kw):
                row = make(body="본문이 있어도", **kw)
                self.assertEqual(row["status"], "excluded")
                self.assertEqual(row["text_chars"], "")

    def test_empty_when_failed(self):
        row = nz.failed_document(source_type="policy", source_name="출처",
                                 original_url="https://a.kr/1", collected_at=NOW,
                                 reason="fetch_error")
        self.assertEqual(row["text_chars"], "")

    def test_cleared_when_deduped(self):
        """중복으로 제외된 행도 빈 값이 된다."""
        a = make(url="https://a.kr/v?nttSeqNo=1", body="같은 본문")
        b = make(url="https://b.kr/v?nttSeqNo=2", body="같은 본문")
        nz.dedupe([a, b])
        self.assertEqual(a["text_chars"], str(len("같은 본문")))
        self.assertEqual(b["text_chars"], "")

    def test_survives_csv_round_trip(self):
        a = make(body="본문 열두 글자다")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documents.csv"
            merged, _ = nz.merge_documents([], [a])
            nz.write_documents(path, merged)
            loaded = nz.read_documents(path)
        self.assertEqual(loaded[0]["text_chars"], str(len("본문 열두 글자다")))

    def test_validate_rejects_non_integer(self):
        rows = rows_for_csv([make()])
        rows[0]["text_chars"] = "스물"
        problems = nz.validate_documents(rows)
        self.assertTrue(any("text_chars" in p for p in problems))

    def test_validate_rejects_value_on_excluded_row(self):
        rows = rows_for_csv([make(published="2026.01.05")])
        rows[0]["text_chars"] = "100"
        problems = nz.validate_documents(rows)
        self.assertTrue(any("text_chars" in p for p in problems))

    def test_validate_allows_empty_value(self):
        """선택 열이라 비어 있어도 된다. 예전 CSV와도 맞는다."""
        rows = rows_for_csv([make()])
        rows[0]["text_chars"] = ""
        self.assertEqual(nz.validate_documents(rows), [])


class CsvRoundTripTest(unittest.TestCase):

    def test_csv_round_trip(self):
        a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
        b = make(url="https://a.kr/v?nttSeqNo=2", body="쉼표, 줄바꿈\n포함 본문")
        nz.dedupe([a, b])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documents.csv"
            merged, added = nz.merge_documents([], [a, b])
            nz.write_documents(path, merged)
            self.assertEqual(added, 2)

            loaded = nz.read_documents(path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["document_id"], a["document_id"])
            self.assertEqual(nz.validate_documents(loaded), [])

    def test_merge_documents_skips_existing_ids(self):
        a = make(url="https://a.kr/v?nttSeqNo=1")
        merged, added = nz.merge_documents(rows_for_csv([a]), [a])
        self.assertEqual(added, 0)
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
