# 고정 fixture (가상 데이터)

이 폴더의 모든 자료는 **검증용으로 만든 가상 데이터**다. 실제 기사·정책 문서가 아니며, URL(`example.test`)과 제목, 본문도 실제 출처와 무관하다.

| 파일 | 내용 |
|---|---|
| `documents.csv` | 가상 문서 목록 (별칭 정규화, 복합어, 반복 등장, 키워드 없음, 중복, 기간 경계, 동률 순위 사례) |
| `keywords_fixture.csv` | fixture 전용 사전 (`dictionary_version=fixture-1`). 실제 사전 `config/keywords.csv` 와 별개 |
| `texts/` | 요약·본문 범위 문서의 가상 본문 |
| `expected/document_keywords.csv` | **수작업으로 계산한** 기대 추출 결과 |
| `expected/keyword_stats.csv` | **수작업으로 계산한** 기대 집계 결과 |

## `context/` — 맥락·확산 지표 정답 (설계 단계)

`docs/context_indicators.md` §3의 지표(함께 나온 키워드, 출처 분해, 표기 분해, 출처 수·유형 수·연속 기간 수)를 검증하기 위한 별도 fixture다. 기존 fixture의 정답에 영향을 주지 않도록 따로 두었다. 출처는 `news-a`, `news-b`, `policy-a` 세 곳이고, 주 단위 기간 W1~W3와 W5(W4에는 분석 문서가 없어 연속 기간을 건너뛰는 규칙을 시험함)로 구성했다.

| 파일 | 내용 |
|---|---|
| `context/documents.csv`, `context/document_keywords.csv` | 입력(가상 문서 12건, 중복 제외 1건 포함) |
| `context/expected/keyword_context.csv` | 함께 나온 키워드 (손 계산) |
| `context/expected/keyword_sources.csv` | 출처 분해 (손 계산) |
| `context/expected/keyword_terms.csv` | 표기 분해 (손 계산) |
| `context/expected/keyword_stats_extra.csv` | `source_count`, `type_count`, `streak` (손 계산) |

`tests/test_context_fixture.py`가 입력에서 독립적으로 다시 계산해 정답과 일치함을 확인한다. 집계 구현이 생기면 같은 정답과 정확히 일치해야 한다.

## 지켜야 할 점

- 기대값은 구현 코드의 출력을 복사해 만들지 않는다. 규칙 문서(`docs/extraction_rules.md`, `docs/data_dictionary.md`)를 보고 손으로 계산한다.
- 기대값을 바꿀 때는 `tests/test_fixture_consistency.py` 가 통과하는지 확인한다.
- 이 자료를 근거로 실제 추출 정확도나 탐지 성능을 주장하지 않는다.
