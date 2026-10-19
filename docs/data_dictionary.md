# 데이터 사전

기획안 §6(공통 CSV 및 전달 규격)의 필드를 구현 기준으로 풀어 적은 문서다. 뉴스 수집기(A)와 정책 수집기(B)가 같은 규격으로 출력하고, 대시보드와 보고서가 같은 CSV를 읽는다.

**담당** B (공통 CSV·통합·중복 처리). 규격을 바꿀 때는 양쪽 수집기와 화면·보고서에 함께 반영한다.

## 1. CSV 공통 규칙

| 항목 | 규칙 |
|---|---|
| 인코딩 | UTF-8 (BOM 없음) |
| 헤더 | 첫 줄에 필드명 포함. 필드 순서는 이 문서의 순서를 따른다 |
| 줄바꿈 | `LF` |
| 날짜 | `YYYY-MM-DD` (예: `2026-09-01`) |
| 일시 | 시간대를 포함한 ISO 8601 (예: `2026-09-01T14:30:00+09:00`) |
| 시간대 | 한국 표준시(KST, UTC+09:00)로 통일 |
| 인용 | 쉼표·줄바꿈·따옴표가 있는 필드는 표준 CSV 인용 규칙(`"` 감싸기, 내부 `"` 는 `""`) |
| 결측 | 빈 문자열. `null`, `NA`, `-` 등을 쓰지 않는다 |
| 불리언 | 쓰지 않는다. 상태는 열거값으로 표현한다 |

**결측 표기 원칙** — 값이 없는 것과 "확인하지 못한 것"을 구분한다. 확인하지 못해 비운 경우 `documents.csv` 의 `exclusion_reason` 또는 실행 기록에 사유를 남긴다.

## 2. `documents.csv`

행 단위: **수집 문서 1건**

| 필드 | 필수 | 타입 | 허용값·형식 | 설명 |
|---|---|---|---|---|
| `document_id` | 필수 | 문자열 | `{source_type}-{12자리 해시}` | 재실행해도 유지되는 내부 식별자. 생성 규칙은 §5 |
| `source_type` | 필수 | 열거 | `news` / `policy` | 자료 유형 |
| `source_name` | 필수 | 문자열 | `config/sources.csv` 의 `source_name` 과 일치 | 출처 이름 |
| `source_document_id` | 선택 | 문자열 | — | 출처가 제공하는 원문 식별자. 있으면 반드시 보존 |
| `title` | 필수 | 문자열 | 1자 이상 | 원문 제목. 앞뒤 공백 제거 |
| `original_url` | 필수 | 문자열 | `http://` 또는 `https://` 로 시작 | 원문 주소. 정규화 규칙은 §5 |
| `published_date` | 필수 | 날짜 | `YYYY-MM-DD` | 원문 발행일. 확인 불가 시 이 문서는 `status=excluded` |
| `collected_at` | 필수 | 일시 | ISO 8601 + 시간대 | 수집 시각 |
| `analysis_scope` | 필수 | 열거 | `title` / `title_summary` / `title_body` | 실제로 분석한 범위 |
| `content_hash` | 필수 | 문자열 | 소문자 16진수 64자 (SHA-256) | 정제 텍스트의 해시. 계산 대상은 §5 |
| `status` | 필수 | 열거 | `analyzed` / `excluded` / `failed` | 처리 결과 |
| `exclusion_reason` | 조건부 | 열거 | §3 표 참조 | `status` 가 `excluded` 또는 `failed` 일 때 필수 |
| `duplicate_of` | 조건부 | 문자열 | 다른 행의 `document_id` | `exclusion_reason=duplicate` 일 때 필수 |

**필수값 규칙 (완료 기준 C2)**

- `status=analyzed` 인 행은 `document_id`, `title`, `original_url`, `published_date` 가 모두 채워져 있어야 한다. 누락 0건이어야 한다
- `document_id` 는 파일 전체에서 중복되지 않아야 한다
- `duplicate_of` 가 가리키는 `document_id` 는 같은 파일 안에 존재해야 하고, 그 행의 `status` 는 `analyzed` 여야 한다

### 선택 열 — `text_chars` (합의·구현 완료)

| 필드 | 필수 | 타입 | 허용값·형식 | 설명 |
|---|---|---|---|---|
| `text_chars` | 선택 | 정수 | 0 이상. 모르면 빈 값 | **정제를 마친 본문의 글자 수.** 제목은 세지 않는다. 공백·줄바꿈은 정제 결과 그대로 센다 |

**왜 필요한가** `analysis_scope` 는 분석한 범위의 **종류**만 알려 주고 **분량**은 알려 주지 않는다. 과기정통부 보도자료(본문 중간값 98자)와 백악관 OSTP(8,056자)가 똑같이 `title_body` 로 기록돼, 화면에서 두 출처를 구분할 수 없다 (`docs/policy_quality_review.md` §1). 출처 간 문서 빈도를 견줄 때 이 차이를 보여 줄 수단이 필요하다. 원문을 주고받지 않아도 분량을 알 수 있다는 점도 이점이다(재배포 제한 출처 때문).

**정하는 방식**

- `status=analyzed` 이고 본문을 받은 행만 채운다. 제목만 분석한 행(`analysis_scope=title`)은 `0`
- `status` 가 `excluded` 또는 `failed` 인 행은 빈 값
- 값을 세는 시점은 정제(§5) 이후다. 같은 입력이면 같은 값이 나온다

**어디서 채우는가**

| 파일 | 담당 | 몫 |
|---|---|---|
| `src/schema.py` | A | `DOCUMENT_OPTIONAL_COLUMNS` 에 등록 |
| `src/collect_news.py` | A | 뉴스 행을 채우고, **정책 행의 값을 지우지 않는다** |
| `src/validate_csv.py` | A | 값이 있으면 0 이상의 정수인지 검사 |
| `src/normalize.py` | B | 정책 행을 채운다. `build_document()` 가 본문 글자 수를 세고, 제외·실패·중복 행은 빈 값으로 되돌린다 |
| `src/collect_policy.py`, `src/collect_html.py` | B | 위 함수를 그대로 쓴다 |
| `dashboard/app.py` | A | 출처별 분량 표시 (예정) |

**한쪽만 고치면 유지되지 않는다.** `collect_news.py` 는 `documents.csv` 를 쓸 때 `schema.py` 의 열 목록으로 **뉴스가 아닌 행까지 다시 쓴다.** 그래서 선택 열을 더할 때는 양쪽 수집기와 `schema.py` 를 함께 고쳐야 한다. 2026-10-19 에 두 수집기를 번갈아 돌려 **정책 행의 값이 보존되는 것**을 확인했다.

## 3. `status` 와 `exclusion_reason` 허용값

| `status` | `exclusion_reason` | 의미 |
|---|---|---|
| `analyzed` | (빈 값) | 분석 완료. 집계에 포함된다 |
| `excluded` | `duplicate` | 중복 문서. 대표 문서를 `duplicate_of` 에 적는다 |
| `excluded` | `no_published_date` | 발행일을 확인할 수 없어 기간 집계에서 제외 (기획안 §3) |
| `excluded` | `out_of_period` | 수집 기간 밖의 문서 |
| `excluded` | `not_target_topic` | 디지털·ICT 범위에 해당하지 않는 문서 |
| `excluded` | `no_text` | 제목조차 확보하지 못한 문서 |
| `failed` | `fetch_error` | 요청 실패(네트워크·4xx·5xx) |
| `failed` | `parse_error` | 본문 추출 실패 |
| `failed` | `pdf_scan_unsupported` | 스캔 PDF. OCR은 필수 범위 밖 (기획안 §3) |
| `failed` | `access_restricted` | 로그인·유료 등 접근 제한. 우회하지 않는다 (기획안 §11) |

새 사유가 필요하면 이 표에 먼저 추가한 뒤 코드에 반영한다. 코드에만 있는 값은 허용하지 않는다.

## 4. `document_keywords.csv`

행 단위: **분석 문서 × 표준 키워드** (키워드가 하나도 없는 문서는 행이 생기지 않는다)

| 필드 | 필수 | 타입 | 허용값·형식 | 설명 |
|---|---|---|---|---|
| `document_id` | 필수 | 문자열 | `documents.csv` 에 존재하고 `status=analyzed` | 문서 식별자 |
| `keyword_id` | 필수 | 문자열 | `config/keywords.csv` 의 `keyword_id` | 표준 키워드 식별자 |
| `keyword` | 필수 | 문자열 | `config/keywords.csv` 의 표준 표기 | 표준 키워드 이름 |
| `matched_terms` | 필수 | JSON 배열 | 예: `["AI","인공지능"]` | 실제로 매칭된 표기 목록. 셀 안에 JSON으로 저장하므로 CSV 인용 필요 |
| `occurrences` | 필수 | 정수 | 1 이상 | 해당 문서에서 매칭된 총 횟수 |
| `dictionary_version` | 필수 | 문자열 | 예: `2026-10-17` | 사용한 사전 버전 |

**규칙**

- 한 문서에 같은 `keyword_id` 가 두 행 이상 나오지 않는다. 표기가 여러 개면 `matched_terms` 에 모은다
- `occurrences` 는 보조 지표다. 주 지표는 문서 빈도다 (기획안 §4)
- 복합어 중복 매칭 규칙(예: `생성형 AI` 와 `AI`)은 `config/keywords.csv` 와 공통 추출 규칙 문서에 적는다. **A와 공동 결정 사항**

## 5. 식별자·해시·URL 정규화 규칙

**`document_id` 생성**

1. 출처가 원문 식별자(`source_document_id`)를 제공하면 `{source_type}-{sha256(source_name + "|" + source_document_id) 앞 12자}`
2. 없으면 정규화한 URL을 쓴다. `{source_type}-{sha256(정규화 URL) 앞 12자}`

같은 입력이면 재실행해도 같은 값이 나와야 한다 (완료 기준 C8).

**URL 정규화**

- 스킴을 소문자로, 호스트를 소문자로
- 기본 포트(`:80`, `:443`) 제거
- 조각(`#...`) 제거
- 추적용 질의 매개변수 제거: `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `fbclid`, `gclid`
- 남은 질의 매개변수는 이름 기준 오름차순 정렬
- 경로 끝의 `/` 는 경로가 `/` 하나뿐인 경우를 빼고 제거

**`content_hash` 계산**

정제를 마친 텍스트(§6 2단계 결과)에 대해 아래 순서로 계산한다.

1. 유니코드 NFKC 정규화
2. 연속 공백을 공백 하나로 축약, 앞뒤 공백 제거
3. UTF-8 바이트로 SHA-256, 소문자 16진수

해시 대상 텍스트는 `analysis_scope` 와 일치해야 한다. 제목만 분석한 문서는 제목만 해시한다.

**중복 제거 순서** (기획안 §4 3단계)

1. 같은 출처 안에서 `source_document_id` 일치
2. 정규화한 `original_url` 일치
3. `content_hash` 일치

먼저 수집된 문서를 대표로 남긴다.

**중복은 두 종류로 갈린다.** `document_id` 가 원문 식별자 또는 정규화 URL에서 만들어지기 때문이다. 구현(이슈 #9)에서 확인한 사실이다.

| 종류 | 걸리는 단계 | `document_id` | CSV 처리 |
|---|---|---|---|
| **같은 문서** | 1단계 또는 2단계 | 대표와 **같다** | 한 행만 남는다. 둘째 행은 병합 단계에서 걸러진다 |
| **내용 중복** | 3단계만 | 대표와 **다르다** | 행을 남기고 `status=excluded`, `exclusion_reason=duplicate`, `duplicate_of=대표 ID` 로 연결한다 |

1·2단계에 걸린 문서는 식별자나 주소가 같아 글자 그대로 같은 문서다. `duplicate_of` 가 자기 자신을 가리키게 되므로 CSV에 두 행을 남기지 않는다. 대신 실행 기록(`collect_log_*.json`)의 `same_document` 건수로 남긴다.

3단계에만 걸린 문서는 주소가 다르므로 두 행이 모두 의미가 있다. 이때만 `duplicate_of` 연결이 생긴다.

유사 기사(제목이 비슷하지만 본문이 다른 경우) 판별은 후속 과제다 (기획안 §2).

## 6. `keyword_stats.csv`

행 단위: **기간 × 자료 유형 × 표준 키워드**

| 필드 | 필수 | 타입 | 허용값·형식 | 설명 |
|---|---|---|---|---|
| `period_type` | 필수 | 열거 | `weekly` / `monthly` | 집계 단위 |
| `period_start` | 필수 | 날짜 | `YYYY-MM-DD` | 기간 시작일. 포함 |
| `period_end` | 필수 | 날짜 | `YYYY-MM-DD` | 기간 종료일. 포함 |
| `source_type` | 필수 | 열거 | `news` / `policy` / `all` | 자료 유형. `all` 은 통합 |
| `keyword_id` | 필수 | 문자열 | — | 표준 키워드 식별자 |
| `keyword` | 필수 | 문자열 | — | 표준 키워드 이름 |
| `document_count` | 필수 | 정수 | 0 이상 | 문서 빈도 |
| `occurrence_count` | 필수 | 정수 | 0 이상 | 출현 횟수 합 |
| `total_documents` | 필수 | 정수 | 0 이상 | 해당 기간·유형의 분석 완료 문서 수 (키워드 없는 문서 포함) |
| `document_share` | 필수 | 실수 또는 문자열 | 소수 2자리, 또는 `해당 없음` | 문서 비율(%). `total_documents=0` 이면 `해당 없음` |
| `rank` | 필수 | 정수 또는 문자열 | 1 이상, 또는 `해당 없음` | 문서 빈도 내림차순 순위 |
| `is_partial_period` | 필수 | 열거 | `yes` / `no` | 진행 중인 주·월이면 `yes` |

**계산 규칙**

- **기간 경계** — 주간은 월요일 시작 일요일 종료, 월간은 달력 월. 시작일과 종료일을 모두 포함한다
- **기준 날짜** — `published_date` 를 쓴다. `collected_at` 을 쓰지 않는다
- **문서 빈도** — 해당 키워드를 포함한 중복 제거 후 문서 수. 한 문서에서 여러 번 등장해도 1건
- **통합(`all`)** — 뉴스와 정책의 `document_count` 를 더하지 않는다. `document_id` 의 **합집합** 으로 다시 센다
- **문서 비율** — `document_count / total_documents * 100`. 소수 둘째 자리에서 반올림
- **순위** — `document_count` 내림차순. 동률은 공동 순위(1, 2, 2, 4 방식). 화면 정렬은 `keyword` 이름순
- **부분 기간** — 집계 실행일이 해당 기간 안에 있으면 `is_partial_period=yes`. 보고서와 화면에 그대로 표시한다
- **빈 결과** — 키워드가 한 번도 안 나온 기간·유형에는 행을 만들지 않는다. 다만 `total_documents` 를 확인할 수 있도록 실행 기록에 분석 문서 수를 남긴다

**하지 않는 것** — 기간별 증감을 `신규` 또는 `급증` 으로 자동 판정하지 않는다 (기획안 §2, §4).

## 7. 검증 항목 연결

| 완료 기준 | 이 문서에서 확인하는 내용 |
|---|---|
| C2 | §2 필수값 규칙, `document_id` 중복 0건, `duplicate_of` 참조 유효성 |
| C3 | §4 복합어·반복·동의어 규칙, §5 중복 제거 순서 |
| C4 | §6 계산 규칙(문서 빈도·출현 횟수·비율·동률 순위·기간 경계) |
| C8 | §5 `document_id`·`content_hash` 생성 규칙의 결정성 |

## 8. 변경 이력

| 날짜 | 변경 | 담당 |
|---|---|---|
| 2026-10-17 | 기획안 §6을 기준으로 초안 작성. `is_partial_period` 필드 추가 제안 | B |
| 2026-10-17 | §5 중복 제거에 "같은 문서"와 "내용 중복" 구분을 추가. 구현(이슈 #9) 중 1·2단계 일치는 `document_id` 까지 같아져 `duplicate_of` 연결이 성립하지 않는 것을 확인했다 | B |
| 2026-10-18 | §2에 선택 열 `text_chars` 를 제안으로 추가. PR #50에서 A가 물은 "분석 범위만으로는 본문 길이를 알 수 없다"에 대한 답이다. 수집기 양쪽과 `schema.py` 를 함께 고쳐야 유지되는 것을 확인해 담당별 변경 목록을 함께 적었다 | B |
| 2026-10-19 | `text_chars` 합의. A 쪽(`schema.py`, `collect_news.py`, `validate_csv.py`) 구현 완료. 뉴스 행: 요약·본문을 받은 행은 글자 수, 제목만 분석한 행은 `0`, 제외·실패 행은 빈 값 | A |

`is_partial_period` 는 기획안 §6 필드 목록에는 없지만, §4의 "진행 중인 주·월은 부분 기간으로 표시한다" 를 CSV에서 구분하려면 필요하다. **합의 완료** — A의 `src/schema.py` 가 `STATS_OPTIONAL_COLUMNS` 로 받아들이고, 대시보드와 검증기가 쓰고 있다(이슈 #18 대조에서 확인).

`text_chars` 는 **합의·구현이 끝났다.** A 쪽(2026-10-19): `src/schema.py` 의 `DOCUMENT_OPTIONAL_COLUMNS`, `src/collect_news.py`(뉴스 행을 채우고 정책 행의 값을 보존), `src/validate_csv.py`(0 이상의 정수인지 검사). B 쪽(2026-10-19): `src/normalize.py` 가 정책 행을 채우고, 제외·실패·중복 행은 빈 값으로 되돌린다. 두 수집기를 번갈아 돌려 값이 보존되는 것을 확인했다. §2의 목록 참고.
| 2026-10-19 | `text_chars` B 쪽 구현 완료(`normalize.py`). 정책 행을 채우고 제외·실패·중복 행은 빈 값으로 둔다. 두 수집기를 번갈아 돌려 값이 보존되는 것을 확인했다 | B |
