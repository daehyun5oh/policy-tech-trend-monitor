"""보고서 생성기 검증 (이슈 #19)

완료기준 C7(주간·월간 보고서를 수동 생성할 수 있고, 같은 조건의 CSV와 수치가
일치한다)을 고정 fixture로 확인한다. 네트워크에 접속하지 않는다.

fixture (tests/fixtures/report_*.csv)

| 문서 | 유형 | 출처 | 재배포 조건 | 발행일 | 키워드(출현) |
|---|---|---|---|---|---|
| policy-0001 | policy | 시험정책 | kogl_per_item | 2026-10-05 | 인공지능(5), 양자(2) |
| policy-0002 | policy | 시험정책 | kogl_per_item | 2026-10-06 | 인공지능(3) |
| cn-0001 | policy | 시험중국 | **local_only_no_publish** | 2026-10-07 | 인공지능(4) |
| news-0001 | news | 시험뉴스 | text_only_no_images | 2026-10-05 | 인공지능(2) |
| policy-0010 | policy | 시험정책 | kogl_per_item | (없음) | — |

주간 A주 2026-10-05 ~ 10-11 수작업 정답 (source_type=all)
- 분석 문서 4건 (policy 3, news 1). 발행일 없는 1건은 기간 배정 불가
- 인공지능: 문서 4건, 출현 14회(5+3+4+2), 비율 100.00%, 1위
- 양자: 문서 1건, 출현 2회, 비율 25.00%, 2위
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import aggregate as ag  # noqa: E402
import generate_report as gr  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WEEK_A = ("2026-10-05", "2026-10-11")
MONTH = ("2026-10-01", "2026-10-31")
GENERATED_AT = "2026-10-17T14:00:00+09:00"


@pytest.fixture(scope="module")
def data():
    documents = gr.read_csv(FIXTURES / "report_documents.csv")
    keywords = gr.read_csv(FIXTURES / "report_document_keywords.csv")
    sources = gr.read_csv(FIXTURES / "report_sources.csv")
    stats, _ = ag.aggregate(documents, keywords, as_of=date(2026, 10, 20))
    return documents, keywords, stats, sources


def make_report(data, *, period_type="weekly", period=WEEK_A, source_type="all",
                keywords_override=None, top_n=10, evidence_limit=5):
    documents, keywords, stats, sources = data
    return gr.build_report(
        documents=documents,
        keywords=keywords if keywords_override is None else keywords_override,
        stats=stats, sources=sources,
        period_type=period_type, period=period, source_type=source_type,
        top_n=top_n, evidence_limit=evidence_limit,
        generated_at=GENERATED_AT)


# ──────────────────────────── 수치 일치 (완료기준 C7) ────────────────────────────

def test_top_keywords_match_stats_csv(data):
    """보고서 수치는 keyword_stats 행을 그대로 옮긴 것이어야 한다."""
    _, _, stats, _ = data
    report = make_report(data)
    expected = {r["keyword_id"]: r for r in stats
                if r["period_type"] == "weekly"
                and (r["period_start"], r["period_end"]) == WEEK_A
                and r["source_type"] == "all"}
    assert len(report["top_keywords"]) == len(expected)
    for row in report["top_keywords"]:
        src = expected[row["keyword_id"]]
        assert row["document_count"] == src["document_count"]
        assert row["occurrence_count"] == src["occurrence_count"]
        assert row["document_share"] == src["document_share"]
        assert row["rank"] == src["rank"]


def test_hand_computed_numbers(data):
    report = make_report(data)
    by_id = {r["keyword_id"]: r for r in report["top_keywords"]}
    assert by_id["kw-ai"]["document_count"] == 4
    assert by_id["kw-ai"]["occurrence_count"] == 14
    assert by_id["kw-ai"]["document_share"] == "100.00"
    assert by_id["kw-ai"]["rank"] == 1
    assert by_id["kw-quantum"]["document_count"] == 1
    assert by_id["kw-quantum"]["document_share"] == "25.00"
    assert by_id["kw-quantum"]["rank"] == 2


def test_document_counts(data):
    report = make_report(data)
    counts = report["counts"]
    assert counts["analyzed"] == {"news": 1, "policy": 3, "all": 4}
    assert counts["undated"] == 1
    assert counts["excluded"].get("no_published_date") == 1


def test_rendered_text_contains_numbers(data):
    report = make_report(data)
    text = gr.render(report)
    assert "| 1 | 인공지능 | 4건 | 100.00% | 14회 |" in text
    assert "| 2 | 양자 | 1건 | 25.00% | 2회 |" in text


# ──────────────────────── 재배포 제한 출처의 제목 ────────────────────────

def test_restricted_source_title_is_withheld(data):
    """local_only_no_publish 출처는 제목을 보고서에 넣지 않는다."""
    report = make_report(data)
    items = report["evidence"]["kw-ai"]
    cn = [i for i in items if i["source_name"] == "시험중국"]
    assert len(cn) == 1
    assert cn[0]["title_withheld"] is True
    assert cn[0]["title"] == ""
    assert cn[0]["original_url"] == "https://cn.example/view/9"
    assert report["restricted_titles_used"] is True


def test_restricted_title_absent_from_rendered_text(data):
    report = make_report(data)
    text = gr.render(report)
    assert "중국 정책 문서 제목" not in text, "제한 출처 제목이 보고서에 새어 나왔다"
    assert "제목 비공개" in text
    assert "https://cn.example/view/9" in text


def test_allowed_source_titles_are_shown(data):
    report = make_report(data)
    text = gr.render(report)
    assert "국내 정책 문서 하나" in text
    assert "국내 뉴스 하나" in text


def test_restriction_notice_only_when_used(data):
    """제한 출처가 없는 기간에는 안내 문구를 넣지 않는다."""
    report = make_report(data, source_type="news")
    assert report["restricted_titles_used"] is False
    assert "제목을 싣지 않고" not in gr.render(report)


def test_title_allowed_helper():
    mapping = {"가": "local_only_no_publish", "나": "kogl_per_item"}
    assert gr.title_allowed("나", mapping) is True
    assert gr.title_allowed("가", mapping) is False
    assert gr.title_allowed("없는출처", mapping) is True


# ──────────────────────────── 근거 문서 ────────────────────────────

def test_evidence_sorted_by_date_desc(data):
    report = make_report(data)
    dates = [i["published_date"] for i in report["evidence"]["kw-ai"]]
    assert dates == sorted(dates, reverse=True)


def test_evidence_limit(data):
    report = make_report(data, evidence_limit=2)
    assert len(report["evidence"]["kw-ai"]) == 2
    text = gr.render(report)
    assert "위는 최근 2건입니다" in text


def test_evidence_respects_source_type(data):
    report = make_report(data, source_type="policy")
    names = {i["source_name"] for i in report["evidence"]["kw-ai"]}
    assert "시험뉴스" not in names


# ──────────────────────────── 기간 ────────────────────────────

def test_monthly_report(data):
    report = make_report(data, period_type="monthly", period=MONTH)
    by_id = {r["keyword_id"]: r for r in report["top_keywords"]}
    assert by_id["kw-ai"]["document_count"] == 4
    text = gr.render(report)
    assert "월간 기술키워드 보고서 — 2026-10-01 ~ 2026-10-31" in text


def test_partial_period_notice(data):
    documents, keywords, _, sources = data
    stats, _ = ag.aggregate(documents, keywords, as_of=date(2026, 10, 7))
    report = gr.build_report(documents=documents, keywords=keywords, stats=stats,
                             sources=sources, period_type="weekly", period=WEEK_A,
                             generated_at=GENERATED_AT)
    assert report["is_partial_period"] == "yes"
    text = gr.render(report)
    assert "부분 기간입니다" in text


def test_no_partial_notice_when_period_closed(data):
    report = make_report(data)
    assert report["is_partial_period"] == "no"
    assert "부분 기간입니다" not in gr.render(report)


def test_pick_period_defaults_to_latest(data):
    _, _, stats, _ = data
    assert gr.pick_period(stats, "weekly", None) == WEEK_A
    assert gr.pick_period(stats, "monthly", None) == MONTH
    assert gr.pick_period(stats, "weekly", "2020-01-06") is None


# ──────────────────────────── 키워드가 없을 때 ────────────────────────────

def test_report_without_keywords(data):
    """추출 결과가 없어도 보고서는 만들어지고, 이유를 적는다."""
    documents, _, _, sources = data
    stats, _ = ag.aggregate(documents, [], as_of=date(2026, 10, 20))
    report = gr.build_report(documents=documents, keywords=[], stats=stats,
                             sources=sources, period_type="weekly", period=WEEK_A,
                             generated_at=GENERATED_AT)
    assert report["top_keywords"] == []
    text = gr.render(report)
    assert "이 기간에 집계된 키워드가 없습니다" in text
    assert "document_keywords.csv" in text
    # 문서 수는 그대로 나와야 한다
    assert "| all | 4건 |" in text


def test_dictionary_version_shown(data):
    report = make_report(data)
    assert report["dictionary_versions"] == ["2026-10-17"]
    assert "2026-10-17" in gr.render(report)


def test_dictionary_version_not_applicable_without_keywords(data):
    documents, _, stats, sources = data
    report = gr.build_report(documents=documents, keywords=[], stats=stats,
                             sources=sources, period_type="weekly", period=WEEK_A,
                             generated_at=GENERATED_AT)
    assert "**사전 버전** 해당 없음" in gr.render(report)


# ──────────────────────────── 해석 한계 ────────────────────────────

def test_interpretation_limits_always_present(data):
    text = gr.render(make_report(data))
    assert "## 5. 읽을 때 주의할 점" in text
    for line in gr.INTERPRETATION_LIMITS:
        assert line in text


def test_no_forward_looking_claims(data):
    """유망성·효과를 단정하는 표현을 쓰지 않는다 (기획안 §5).

    단어 자체가 아니라 **단정하는 표현**을 본다. 해석 한계 절에는
    "미래 유망성을 뜻하지 않는다" 처럼 부정하는 문장이 들어가므로,
    단어만으로 거르면 그 문장까지 걸린다.
    """
    text = gr.render(make_report(data))
    banned = (
        "유망하다", "유망한 기술", "유망성이 높", "유망 기술로",
        "급증했", "급증한", "급증하고",
        "전망된다", "예측된다", "기대된다",
        "신흥기술을 탐지", "신흥기술 탐지",
    )
    for phrase in banned:
        assert phrase not in text, f"단정 표현이 들어갔다: {phrase}"


def test_interpretation_limits_deny_promise(data):
    """해석 한계 절이 유망성·급증 판정을 부정하는 문장을 담는지."""
    text = gr.render(make_report(data))
    assert "미래 유망성을 뜻하지 않습니다" in text
    assert "신규 또는 급증으로 판정하지 않습니다" in text


def test_undated_documents_are_noted(data):
    text = gr.render(make_report(data))
    assert "발행일을 확인할 수 없어" in text
    assert "수집일로 대체하지 않았습니다" in text


# ──────────────────────────── 파일 이름·쓰기 ────────────────────────────

def test_output_path_naming(data, tmp_path):
    report = make_report(data)
    path = gr.output_path(report, tmp_path)
    assert path.name == "report_weekly_2026-10-05_2026-10-11.md"


def test_write_and_reread(data, tmp_path):
    report = make_report(data)
    path = gr.output_path(report, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gr.render(report), encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# 주간 기술키워드 보고서")
    assert "중국 정책 문서 제목" not in text


def test_render_is_deterministic(data):
    assert gr.render(make_report(data)) == gr.render(make_report(data))


# ──────────────────────────── CSV 경로 (회귀 방지) ────────────────────────────

def test_stats_loaded_from_csv(data, tmp_path):
    """keyword_stats.csv 를 거쳐도 같은 결과가 나와야 한다.

    CSV 를 읽으면 숫자가 문자열이 된다. 메모리 객체만으로 검증하다가
    보고서 생성에서 TypeError 가 났던 회귀를 막는다.
    """
    documents, keywords, stats, sources = data
    path = tmp_path / "keyword_stats.csv"
    ag.write_stats(path, stats)
    from_csv = ag.read_csv(path)
    assert isinstance(from_csv[0]["document_count"], str)

    report = gr.build_report(
        documents=documents, keywords=keywords, stats=from_csv, sources=sources,
        period_type="weekly", period=WEEK_A, generated_at=GENERATED_AT)

    by_id = {r["keyword_id"]: r for r in report["top_keywords"]}
    assert by_id["kw-ai"]["document_count"] == 4
    assert by_id["kw-ai"]["rank"] == 1
    assert isinstance(by_id["kw-ai"]["document_count"], int)

    text = gr.render(report)
    assert "| 1 | 인공지능 | 4건 | 100.00% | 14회 |" in text


def test_normalize_stats_row_handles_bad_values():
    row = {"document_count": "", "occurrence_count": None,
           "total_documents": "x", "rank": "3", "document_share": "해당 없음"}
    out = gr.normalize_stats_row(row)
    assert out["document_count"] == 0
    assert out["occurrence_count"] == 0
    assert out["total_documents"] == 0
    assert out["rank"] == 3
    assert out["document_share"] == "해당 없음"
