"""공통 CSV 규격 상수 (기획안 §6).

B가 작성하는 docs/data_dictionary.md(#4)와 값이 일치해야 한다.
'제안' 표시가 있는 값은 기획안에 명시되지 않아 A가 임시로 정한 것이므로,
data_dictionary.md에서 확정되면 이 파일을 함께 고친다.
"""

# 기획안 §6 '주요 필드'. 기획안은 이 목록을 '주요' 필드로 적었으므로 추가 열은 허용한다.
DOCUMENT_COLUMNS = [
    "document_id", "source_type", "source_name", "source_document_id", "title",
    "original_url", "published_date", "collected_at", "analysis_scope",
    "content_hash", "status", "exclusion_reason", "duplicate_of",
]
# 선택 열: 있으면 값을 검사하고 수집기가 지우지 않는다 (docs/data_dictionary.md §2 text_chars).
# text_chars: 정제를 마친 본문 글자 수(제목 제외). 제목만 분석한 행은 0, 제외·실패 행은 빈 값.
DOCUMENT_OPTIONAL_COLUMNS = ["text_chars"]
KEYWORD_COLUMNS = [
    "document_id", "keyword_id", "keyword", "matched_terms", "occurrences",
    "dictionary_version",
]
STATS_COLUMNS = [
    "period_type", "period_start", "period_end", "source_type", "keyword_id",
    "keyword", "document_count", "occurrence_count", "total_documents",
    "document_share", "rank",
]

# 기획안: 문서의 source_type은 news 또는 policy, 집계에서는 all도 허용
SOURCE_TYPES = ("news", "policy")
STATS_SOURCE_TYPES = ("news", "policy", "all")

# 제안: 기획안은 '분석 완료·제외·실패'만 적었고 저장 값은 정하지 않았다.
STATUS_ANALYZED = "analyzed"
STATUS_EXCLUDED = "excluded"
STATUS_FAILED = "failed"
STATUSES = (STATUS_ANALYZED, STATUS_EXCLUDED, STATUS_FAILED)

# 제안: 기획안은 '제목·요약·본문 중 실제 분석 범위'라고만 적었다.
SCOPES = ("title", "title_summary", "title_body")

# 제안: 기획안은 '주간·월간'만 적었고 저장 값은 정하지 않았다. 일간(daily)은 대시보드에서 오늘의 키워드를 보기 위해 추가한 제안이다.
PERIOD_TYPES = ("daily", "weekly", "monthly")

# 선택 열: 있으면 yes/no여야 하며, 대시보드는 이 값을 부분 기간 판정에 우선 사용한다 (B의 data_dictionary.md §6)
STATS_OPTIONAL_COLUMNS = ["is_partial_period"]
PARTIAL_VALUES = ("yes", "no")

# 기획안 §4: 분모가 0이면 비율·순위를 '해당 없음'으로 표시
NOT_APPLICABLE = "해당 없음"
