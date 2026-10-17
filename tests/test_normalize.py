"""정제·중복 제거 모듈 검증 (이슈 #9)

완료기준 C2(필수값 누락·ID 중복·대표 문서 연결)와 C8(결정성)을 고정 입력으로
확인한다. 네트워크에 접속하지 않는다.

실행
    .venv\\Scripts\\python.exe -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


# ──────────────────────────── URL 정규화 ────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("HTTPS://Example.GO.KR/a/b/", "https://example.go.kr/a/b"),
    ("https://example.go.kr:443/a", "https://example.go.kr/a"),
    ("http://example.go.kr:80/a", "http://example.go.kr/a"),
    ("https://example.go.kr/a#frag", "https://example.go.kr/a"),
    ("https://example.go.kr/a?utm_source=x&id=1", "https://example.go.kr/a?id=1"),
    ("https://example.go.kr/a?b=2&a=1", "https://example.go.kr/a?a=1&b=2"),
    ("https://example.go.kr/", "https://example.go.kr/"),
])
def test_normalize_url(raw, expected):
    assert nz.normalize_url(raw) == expected


def test_normalize_url_keeps_tracking_free_params():
    url = "https://example.go.kr/v?fbclid=zz&gclid=yy&nttSeqNo=7"
    assert nz.normalize_url(url) == "https://example.go.kr/v?nttSeqNo=7"


# ──────────────────────────── 날짜 ────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026.10.15", "2026-10-15"),
    ("2026-10-15", "2026-10-15"),
    ("2026/10/15", "2026-10-15"),
    ("등록일 : 2026.10.15 ", "2026-10-15"),
    ("Thu, 15 Oct 2026 09:00:00 +0900", "2026-10-15"),
])
def test_parse_published_date(raw, expected):
    assert nz.parse_published_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "날짜없음", "2026.13.45"])
def test_parse_published_date_unparseable(raw):
    assert nz.parse_published_date(raw) is None


def test_window_is_inclusive():
    assert nz.in_window("2026-05-01", *WINDOW)
    assert nz.in_window("2026-10-31", *WINDOW)
    assert not nz.in_window("2026-04-30", *WINDOW)
    assert not nz.in_window("2026-11-01", *WINDOW)


# ──────────────────────────── 텍스트 정제 ────────────────────────────

def test_clean_text_removes_escaped_hwp_block():
    """HWP 블록이 이스케이프된 상태로 와도 제거된다. 이슈 #8에서 고친 버그."""
    raw = ('본문 시작&lt;!--[data-hwpjson]{&#034;documentPr&#034;: '
           '{&#034;dn&#034;: &#034;test.hwp&#034;}}--&gt;본문 끝')
    out = nz.clean_text(raw)
    assert "hwpjson" not in out
    assert "documentPr" not in out
    assert "본문 시작" in out


def test_clean_text_removes_unclosed_hwp_block():
    """닫는 --> 없이 잘려 와도 시작 지점부터 버린다."""
    raw = "본문&lt;!--[data-hwpjson]{&#034;a&#034;: 1"
    out = nz.clean_text(raw)
    assert "hwpjson" not in out
    assert out.startswith("본문")


def test_clean_text_double_escaped_entities():
    assert "\xa0" not in nz.clean_text("가&amp;nbsp;나")


def test_clean_text_strips_phone_numbers():
    """담당자 연락처를 지운다 (기획안 §11)."""
    out = nz.clean_text("문의 044-202-4532 기초연구진흥과")
    assert "044-202-4532" not in out
    assert "기초연구진흥과" in out


def test_clean_text_keeps_phone_when_disabled():
    out = nz.clean_text("문의 044-202-4532", strip_phone=False)
    assert "044-202-4532" in out


def test_clean_text_drops_script_and_style():
    raw = "<style>p{color:red}</style>본문<script>alert(1)</script>"
    out = nz.clean_text(raw)
    assert out == "본문"


def test_clean_text_block_tags_become_newlines():
    out = nz.clean_text("<p>첫째</p><p>둘째</p>")
    assert out == "첫째\n둘째"


# ──────────────────────────── 해시·식별자 결정성 ────────────────────────────

def test_content_hash_is_deterministic():
    assert nz.content_hash("가 나") == nz.content_hash("가 나")


def test_content_hash_ignores_whitespace_shape():
    assert nz.content_hash("가  나\n") == nz.content_hash(" 가 나 ")


def test_content_hash_length():
    assert len(nz.content_hash("x")) == 64


def test_document_id_prefers_source_document_id():
    a = nz.make_document_id("policy", "출처", "nttSeqNo=1", "https://a.kr/1")
    b = nz.make_document_id("policy", "출처", "nttSeqNo=1", "https://b.kr/2")
    assert a == b, "원문 식별자가 같으면 URL이 달라도 같은 ID"


def test_document_id_falls_back_to_url():
    a = nz.make_document_id("policy", "출처", "", "https://a.kr/1")
    b = nz.make_document_id("policy", "출처", "", "https://a.kr/1")
    c = nz.make_document_id("policy", "출처", "", "https://a.kr/2")
    assert a == b and a != c


def test_document_id_has_source_type_prefix():
    assert nz.make_document_id("policy", "출처", "", "https://a.kr/1").startswith("policy-")


def test_extract_source_document_id():
    assert nz.extract_source_document_id("https://a.kr/v?nttSeqNo=99") == "nttSeqNo=99"
    assert nz.extract_source_document_id("https://a.kr/v") == ""


# ──────────────────────────── 문서 행 만들기 ────────────────────────────

def test_build_document_analyzed():
    row = make()
    assert row["status"] == "analyzed"
    assert row["exclusion_reason"] == ""
    assert row["published_date"] == "2026-10-15"
    assert row["analysis_scope"] == "title_body"


def test_build_document_title_only_scope():
    row = make(body="")
    assert row["analysis_scope"] == "title"


def test_build_document_excludes_missing_date():
    """발행일을 확인할 수 없으면 수집일로 대체하지 않고 제외한다 (기획안 §3)."""
    row = make(published="확인불가")
    assert row["status"] == "excluded"
    assert row["exclusion_reason"] == "no_published_date"
    assert row["published_date"] == ""


def test_build_document_excludes_out_of_period():
    row = make(published="2026.01.05")
    assert row["exclusion_reason"] == "out_of_period"


def test_build_document_excludes_empty_title():
    row = make(title="   ")
    assert row["exclusion_reason"] == "no_text"


def test_failed_document_requires_known_reason():
    with pytest.raises(ValueError):
        nz.failed_document(source_type="policy", source_name="출처",
                           original_url="https://a.kr/1", collected_at=NOW,
                           reason="잘못된사유")


# ──────────────────────────── 중복 제거 ────────────────────────────

def test_dedupe_by_source_document_id():
    """1단계: 원문 식별자가 같으면 document_id 까지 같다 → 같은 문서."""
    a = make(url="https://a.kr/view.do?nttSeqNo=5", body="본문 하나")
    b = make(url="https://a.kr/other.do?nttSeqNo=5", body="본문 둘")
    assert a["document_id"] == b["document_id"]
    counts = nz.dedupe([a, b])
    assert counts["same_document"] == 1
    assert counts["duplicate"] == 0
    assert a["status"] == "analyzed"
    assert b["exclusion_reason"] == "duplicate"
    assert b.get("_same_id") is True


def test_dedupe_by_normalized_url():
    """2단계: 주소가 정규화 후 같으면 document_id 까지 같다 → 같은 문서."""
    a = make(url="https://a.kr/notice/7", body="본문 하나")
    b = make(url="HTTPS://A.KR/notice/7/?utm_source=x", body="본문 둘")
    assert a["document_id"] == b["document_id"]
    counts = nz.dedupe([a, b])
    assert counts["same_document"] == 1


def test_dedupe_by_content_hash():
    """3단계: 주소·식별자가 달라 ID가 다르면 duplicate_of 로 연결한다."""
    a = make(url="https://a.kr/v?nttSeqNo=1", body="같은 본문")
    b = make(url="https://b.kr/v?nttSeqNo=2", body="같은 본문")
    assert a["document_id"] != b["document_id"]
    counts = nz.dedupe([a, b])
    assert counts["duplicate"] == 1
    assert counts["same_document"] == 0
    assert b["duplicate_of"] == a["document_id"]
    assert b["duplicate_of"] != b["document_id"]


def test_dedupe_keeps_first_as_representative():
    a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
    b = make(url="https://b.kr/v?nttSeqNo=2", body="본문 하나")
    nz.dedupe([a, b])
    assert a["status"] == "analyzed" and b["status"] == "excluded"
    assert b["duplicate_of"] == a["document_id"]


def test_dedupe_against_existing_rows():
    """이전 실행 결과와도 비교한다."""
    first = make(url="https://a.kr/v?nttSeqNo=7", body="본문")
    nz.dedupe([first])
    existing = [{k: first[k] for k in nz.DOCUMENT_FIELDS}]
    again = make(url="https://a.kr/v?nttSeqNo=7", body="본문")
    counts = nz.dedupe([again], existing)
    assert counts["same_document"] == 1
    assert again["status"] == "excluded"


def test_dedupe_content_match_against_existing():
    first = make(url="https://a.kr/v?nttSeqNo=7", body="같은 본문")
    nz.dedupe([first])
    existing = [{k: first[k] for k in nz.DOCUMENT_FIELDS}]
    other = make(url="https://b.kr/v?nttSeqNo=9", body="같은 본문")
    counts = nz.dedupe([other], existing)
    assert counts["duplicate"] == 1
    assert other["duplicate_of"] == first["document_id"]


def test_dedupe_does_not_touch_excluded_rows():
    out = make(published="2026.01.01")
    nz.dedupe([out])
    assert out["exclusion_reason"] == "out_of_period", "제외 사유가 덮이지 않아야 한다"


def test_dedupe_allows_different_documents():
    a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
    b = make(url="https://a.kr/v?nttSeqNo=2", body="본문 둘")
    counts = nz.dedupe([a, b])
    assert counts["duplicate"] == 0


# ──────────────────────────── 검증 함수 (완료기준 C2) ────────────────────────────

def rows_for_csv(docs):
    return [{k: d.get(k, "") for k in nz.DOCUMENT_FIELDS} for d in docs]


def test_validate_passes_on_clean_set():
    """같은 문서(c)는 merge 단계에서 걸러지므로 CSV에는 a, b 와 내용중복 d 가 남는다."""
    a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
    b = make(url="https://a.kr/v?nttSeqNo=2", body="본문 둘")
    d = make(url="https://b.kr/v?nttSeqNo=3", body="본문 하나")
    nz.dedupe([a, b, d])
    merged, _ = nz.merge_documents([], [a, b, d])
    assert nz.validate_documents(merged) == []


def test_merge_drops_same_document_rows():
    a = make(url="https://a.kr/v?nttSeqNo=1", body="본문")
    b = make(url="https://a.kr/v?nttSeqNo=1", body="본문")
    nz.dedupe([a, b])
    merged, added = nz.merge_documents([], [a, b])
    assert added == 1
    assert len(merged) == 1
    assert nz.validate_documents(merged) == []


def test_validate_catches_duplicate_document_id():
    a = make(url="https://a.kr/v?nttSeqNo=1")
    rows = rows_for_csv([a, a])
    problems = nz.validate_documents(rows)
    assert any("중복" in p for p in problems)


def test_validate_catches_missing_required_field():
    a = make()
    rows = rows_for_csv([a])
    rows[0]["published_date"] = ""
    problems = nz.validate_documents(rows)
    assert any("published_date" in p for p in problems)


def test_validate_catches_duplicate_without_link():
    a = make(url="https://a.kr/v?nttSeqNo=1", body="같은 본문")
    b = make(url="https://b.kr/v?nttSeqNo=2", body="같은 본문")
    nz.dedupe([a, b])
    rows = rows_for_csv([a, b])
    rows[1]["duplicate_of"] = ""
    problems = nz.validate_documents(rows)
    assert any("duplicate_of" in p for p in problems)


def test_validate_catches_dangling_duplicate_of():
    a = make()
    rows = rows_for_csv([a])
    rows[0]["status"] = "excluded"
    rows[0]["exclusion_reason"] = "duplicate"
    rows[0]["duplicate_of"] = "policy-없는아이디"
    problems = nz.validate_documents(rows)
    assert any("가리키는 문서가 없다" in p for p in problems)


def test_validate_catches_bad_enum():
    a = make()
    rows = rows_for_csv([a])
    rows[0]["status"] = "done"
    problems = nz.validate_documents(rows)
    assert any("status" in p for p in problems)


# ──────────────────────────── CSV 왕복 ────────────────────────────

def test_csv_round_trip(tmp_path):
    a = make(url="https://a.kr/v?nttSeqNo=1", body="본문 하나")
    b = make(url="https://a.kr/v?nttSeqNo=2", body="쉼표, 줄바꿈\n포함 본문")
    nz.dedupe([a, b])
    path = tmp_path / "documents.csv"
    merged, added = nz.merge_documents([], [a, b])
    nz.write_documents(path, merged)
    assert added == 2

    loaded = nz.read_documents(path)
    assert len(loaded) == 2
    assert loaded[0]["document_id"] == a["document_id"]
    assert nz.validate_documents(loaded) == []


def test_merge_documents_skips_existing_ids():
    a = make(url="https://a.kr/v?nttSeqNo=1")
    merged, added = nz.merge_documents(rows_for_csv([a]), [a])
    assert added == 0
    assert len(merged) == 1
