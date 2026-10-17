# 보고서 예시

이 폴더의 보고서는 **검증용 고정 입력(fixture)으로 생성한 예시**입니다. 실제 수집 자료가 아닙니다.

## 왜 fixture로 만들었나

실제로 수집한 정책 문서 50건에는 아직 키워드 추출 결과가 없습니다. 키워드 추출은 이슈 #11(담당 A)이고, `document_keywords.csv` 가 없으면 상위 키워드 표가 비어 보고서 구조를 보여주지 못합니다.

그래서 키워드와 **재배포 제한 출처**가 모두 들어 있는 fixture로 예시를 만들었습니다. 보고서가 어떤 모습인지, 제한 출처를 어떻게 다루는지 확인할 수 있습니다.

## 입력

| 파일 | 내용 |
|---|---|
| `tests/fixtures/report_documents.csv` | 문서 5건 (분석 4건 + 발행일 없는 1건) |
| `tests/fixtures/report_document_keywords.csv` | 문서-키워드 5건 |
| `tests/fixtures/report_sources.csv` | 출처 3곳. `시험중국` 은 `local_only_no_publish` |

수치는 `tests/test_generate_report.py` 의 수작업 정답과 대조해 검증했습니다 (완료기준 C7).

## 눈여겨볼 점

**재배포 제한 출처의 제목이 빠져 있습니다.** `시험중국` 출처의 문서는 근거 목록에 이렇게 나옵니다.

```
- 2026-10-07 · 시험중국 · 제목 비공개 · <https://cn.example/view/9>
```

`config/sources.csv` 의 `redistribution` 이 `local_only_no_publish` 인 출처에 적용되는 규칙입니다. 중국 국무원 영문판과 중국과학원 영문판이 여기 해당합니다. 근거는 `docs/source_policy.md` §3, §6 에 있습니다.

## 다시 만드는 방법

```
.venv\Scripts\python.exe src/aggregate.py --documents tests/fixtures/report_documents.csv --keywords tests/fixtures/report_document_keywords.csv --out <임시경로>/keyword_stats.csv --as-of 2026-10-20
```

```
.venv\Scripts\python.exe src/generate_report.py --period-type weekly --documents tests/fixtures/report_documents.csv --keywords tests/fixtures/report_document_keywords.csv --stats <임시경로>/keyword_stats.csv --sources tests/fixtures/report_sources.csv --out-dir reports/examples
```

`--period-type monthly` 로 한 번 더 돌리면 월간 예시가 나옵니다.

실제 자료로 만든 보고서는 `reports/weekly/`, `reports/monthly/` 에 생성되며 Git 추적에서 제외됩니다.
