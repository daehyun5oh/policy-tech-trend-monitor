# 검증 기록

기획안 §10의 완료 기준 C1~C10에 대한 검증 방법과 증거를 남기는 문서다. 각 이슈를 닫을 때 해당 줄을 채운다.

**기록할 것** (기획안 §10) 입력 버전, 실행 환경, 코드 커밋, 담당자, 실행일, 결과, 미해결 항목.

## 완료 기준별 상태

| ID | 완료 조건 | 담당 | 상태 | 증거 위치 |
|---|---|---|---|---|
| C1 | 뉴스·정책 각 1개 이상 출처에서 수동 수집 성공 | A·B | **충족** — 정책 과기정통부 보도자료 50건, 뉴스 138건 | 이슈 #2(closed), #8, #21, #26 |
| C2 | 분석 완료 문서의 필수 근거값 누락 0건, 문서 ID 중복 0건 | B | **충족** — `validate_documents()` 통과, 실제 50건 확인 | `src/normalize.py`, `tests/test_normalize.py` |
| C3 | 별칭 정규화·복합어·반복 등장·키워드 없음·중복 문서 사례 처리 | A·B | 규칙 정의 완료, 사례 fixture는 A 쪽에 있음. 정책 자료 대조는 미착수 | `docs/data_dictionary.md` §4·§5, `docs/extraction_rules.md` / 이슈 #14 |
| C4 | 문서 빈도·출현 횟수·비율·동률 순위·기간 경계 계산 정확 | B | **충족** — B fixture 수작업 정답과 일치. A가 독립 손 계산으로 교차 확인(주간 16행·월간 15행 일치) | `tests/test_aggregate.py`, 이슈 #26 코멘트 |
| C5 | 실제 자료의 추출 품질 검토 및 한계 공개 | A·B | 뉴스는 A가 검토(`docs/news_quality_review.md`). 정책 자료는 미착수 | 이슈 #23, #24 |
| C6 | 대시보드에서 기간·유형 선택, 빈도·순위 및 근거 조회 | A | A가 정적 HTML로 구현(`dashboard/app.py`). B는 정책 자료로 아직 확인하지 않았다 | 이슈 #16, #17, #18 |
| C7 | 주간·월간 보고서를 각각 수동 생성 | B | **충족** — 예시 2개 생성, CSV와 수치 일치. A가 보고서↔화면↔CSV 100행 대조에서 불일치 0건 확인 | `reports/examples/`, `tests/test_generate_report.py`, 이슈 #20 |
| C8 | 동일 고정 입력·설정·사전으로 집계 재현 | A·B | **부분 충족** — 추출 단계는 아래 「재현성 점검」 절에서 A가 확인. 집계·보고서는 B 테스트로 결정성 확인. 종단 재실행 비교는 이슈 #22 | 이 문서 재현성 절, `tests/` 전반 |
| C9 | 다른 담당자가 README만으로 샘플 실행 완료 | A·B | **부분 충족** — 명령어 작성 완료. 서로의 환경에서 실행 확인은 이슈 #26 진행 중 | `README.md` §2·§3 |
| C10 | 전달 묶음 완비 및 공개 범위 확인 | B | 미착수 | 이슈 #27 |

## 검증 실행 기록

실행할 때마다 아래 표에 한 줄을 추가한다.

| 실행일 | 대상 기준 | 입력 버전 | 사전 버전 | 코드 커밋 | 실행 환경 | 담당 | 결과 | 미해결 |
|---|---|---|---|---|---|---|---|---|
| 2026-10-17 | C1·C2 | 과기정통부 보도자료 RSS | — | `984c5c6` | Python 3.13.15 / Windows | B | 50건 수집, 규격 검증 통과 | 뉴스 출처 미확정 |
| 2026-10-17 | C2·C4 | `tests/fixtures/*` | — | `c3317f7` | 같음 | B | 테스트 79건 통과 | 키워드 추출 없음 |
| 2026-10-17 | C7 | `tests/fixtures/report_*` | sample-2026-10-17 | `76839dc` | 같음 | B | 보고서 2건 생성, 수치 일치 | 예시가 fixture 기반 |
| 2026-10-17 | C1·C8 | `data/sample/*` | sample-2026-10-17 | `43e740a` | 같음 | B | 종단 실행 성공, CSV 3종 생성, 테스트 130건 통과 | 당시 추출·대시보드 미구현 |

## 재현성 점검 (이슈 #22, 완료 기준 C8)

**방법** 같은 고정 입력·사전·설정으로 `extract_keywords.py` 를 2회 실행하고 결과 CSV를 비교한다.
실행 시각 같은 메타데이터는 이 CSV에 들어 있지 않다.

| 항목 | 값 |
|---|---|
| 점검일 | 2026-10-17 |
| 실행자 | A (Windows 11, Python 3.12.10) |
| 입력 | `tests/fixtures/documents.csv`, 사전 `tests/fixtures/keywords_fixture.csv`, 본문 `tests/fixtures/texts/` |
| 명령 | `python src/extract_keywords.py --documents tests/fixtures/documents.csv --dictionary tests/fixtures/keywords_fixture.csv --text-dir tests/fixtures/texts --out <파일>` (2회) |
| 결과 | 두 출력이 바이트 단위로 같음. SHA-256 `e902adcc9b16776b6f19cc8b752904fbd8d195c32e04c0ab6d71db802a14be84` |
| 기대값 대조 | 정렬해 비교하면 `tests/fixtures/expected/document_keywords.csv` 와 일치 |
| 자동화 | `tests/test_fixture_consistency.py`, `tests/test_extract_keywords.py` 가 같은 비교를 매번 수행 |

**범위** 키워드 추출 단계만 점검했다. 집계 단계(`aggregate.py`, #13)는 B 쪽 테스트에서 같은 입력을 2회 넣으면 같은 결과가 나오는 것까지 확인했고(`tests/test_aggregate.py`, `tests/test_pipeline.py`), 수집부터 보고서까지의 종단 재실행 비교는 이슈 #22에 남아 있다.

## 막혀 있는 것

**정책 자료의 키워드 추출 결과 공유 (이슈 #12, #20, #24)**

`src/extract_keywords.py` 는 `main` 에 들어왔다(PR #39). 정책 문서에 추출을 돌려 `document_keywords.csv` 를 A에게 전달하면 교차 검토와 정책·통합 수치 대조가 이어진다. 재배포 제한 때문에 저장소에는 올리지 않고, 제목·본문이 없는 키워드 표와 메타데이터만 전달한다.

**테스트의 pytest 의존 (이슈 #26)**

B가 쓴 테스트 4개(`test_normalize`, `test_aggregate`, `test_generate_report`, `test_pipeline`)가 `import pytest` 를 한다. 공통 규칙은 "표준 라이브러리 `unittest`, 설치 없이 실행"이다(`docs/environment.md`). 쓰고 있는 pytest 기능과 바꾸는 범위는 이슈 #26에서 정한다.

**일간 집계 (이슈 #13 후속)**

대시보드의 일간 보기를 위해 `period_type=daily` 가 필요하다. A의 fixture에 일간 18행 손 계산 정답이 있다.

## 해결된 것

| 항목 | 결정·결과 | 근거 |
|---|---|---|
| 실행 환경·대시보드 도구 | Python 3.10 이상, 대시보드는 **정적 HTML**(표준 라이브러리). 외부 패키지는 수집 계층만 허용하고 버전 고정 | 이슈 #3 A 결정, `docs/environment.md` |
| `terms_ok_local` 분기 규칙 | 보고서와 대시보드가 `config/sources.csv` 의 `redistribution` 을 보고 제목을 숨긴다 | 이슈 #3 코멘트, `src/generate_report.py`, `dashboard/app.py` |
| 키워드 추출 미구현 | `src/extract_keywords.py` 가 `main` 에 들어왔고, 인자 없이 호출하면 `data/raw` 를 찾는다 | PR #39 |

## 기록 이력

| 날짜 | 작업 | 담당 |
|---|---|---|
| 2026-10-17 | 양식 작성. C1~C10 현재 상태 기록 | B |
| 2026-10-17 | 종단 실행(이슈 #21) 반영. C1·C2·C4·C7 충족, C8·C9 부분 충족으로 갱신 | B |
| 2026-10-17 | A의 재현성 점검 절(이슈 #22)을 합치고 A의 교차 검증 결과(이슈 #26)를 상태표에 반영. 막힌 것에서 실행 환경·`terms_ok_local`·키워드 추출을 해결로 옮김 | B |
