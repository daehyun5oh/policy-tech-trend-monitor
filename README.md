# 뉴스·정책 기반 디지털·ICT 기술키워드 모니터링 도구

뉴스·정책 자료에서 기술키워드를 추출·정규화하고, 기간별 빈도와 순위를 대시보드로 보여 주는 수동 실행 시제품이다. 범위와 완료 기준은 [기획안.md](기획안.md)에 있다.

> **구현 상태** 수집(뉴스) → 키워드 추출 → CSV 검증 → 대시보드는 동작한다. 집계(`aggregate.py`, #13), 정책 수집기, 보고서 생성(#19)은 B 담당이며 아직 없다. 그래서 아래 빠른 시작은 집계 결과를 **정답 fixture**로 대신한다.

## 1. 설치

Python 3.10 이상이 필요하다(3.12, 3.13에서 확인). 추출·검증·대시보드·테스트와 뉴스 수집은 **외부 패키지 없이** 돈다. 정책 수집기(`collect_policy.py`)를 돌릴 때만 외부 패키지가 필요하다 ([docs/environment.md](docs/environment.md) 참고).

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt  # 정책 수집기용. 빠른 시작에는 필요 없다
```

Windows에서 `python`이 실행되지 않으면 Microsoft Store 별칭 때문일 수 있다. 앱 실행 별칭에서 `python.exe`를 끄거나 `py` 런처를 쓴다.

```bash
python --version
```

Windows 콘솔에서 한글이 깨지면 먼저 `PYTHONIOENCODING=utf-8`(PowerShell은 `$env:PYTHONIOENCODING="utf-8"`)을 지정한다.

## 2. 빠른 시작 (샘플, 네트워크 불필요)

저장소 루트에서 순서대로 실행한다.

```bash
python -m unittest discover -s tests
python src/collect_news.py csv data/sample/geeknews_2026-09_input.csv --from 2026-09-01 --to 2026-09-30
python src/extract_keywords.py
python src/validate_csv.py --documents data/processed/documents.csv --keywords data/processed/document_keywords.csv
python dashboard/app.py --dir tests/fixtures --stats tests/fixtures/expected/keyword_stats.csv --keywords tests/fixtures/expected/document_keywords.csv --out data/processed/dashboard.html --open
```

| 단계 | 기대 결과 |
|---|---|
| 테스트 | `OK` |
| 수집 | `수집 완료: 전체 1417건 / 분석 1383 / 제외 34 ...` |
| 추출 | `사전 버전 v1 / 분석 대상 1383건 ...` |
| 검증 | `검증 결과: 오류 0건` |
| 대시보드 | `data/processed/dashboard.html` 이 브라우저에서 열린다 (fixture 기준 화면) |

## 3. 실제 자료 실행

`config/sources.csv` 에서 상태가 `terms_ok` 인 뉴스 RSS 출처(긱뉴스, 전자신문 AI·SW, ZDNet Korea, 보안뉴스 IT·과학)의 공개 피드를 **출처마다 1회만** 요청한다(재시도 없음). 피드는 최근 15~50건만 주기 때문에, 실행할 때마다 받은 항목을 `data/raw/news_items.csv` 에 **누적**하고 `documents.csv` 는 누적본 전체로 다시 만든다. 며칠 모으면 일간·주간 추이가 쌓인다(Git에는 올리지 않음).

```bash
python src/collect_news.py rss --from 2026-10-01 --to 2026-10-31
python src/extract_keywords.py
python src/validate_csv.py --documents data/processed/documents.csv --keywords data/processed/document_keywords.csv --sources config/sources.csv
```

- 추출기는 본문 파일(`<document_id>.txt`)을 `data/raw`(정책 수집기)와 `data/processed/texts`(뉴스 수집기)에서 차례로 찾는다. 다른 폴더는 `--text-dir`을 여러 번 줘서 지정한다.
- 특정 출처만: `--sources news-002,news-004` (수집기), 누적 없이 이번 결과만: `--no-accumulate`.
- 한 출처가 실패해도 나머지는 수집하고, 실패한 출처는 종료 코드 1과 `data/processed/collect_log.json` 의 사유(`fetch_error`, `parse_error`)로 알린다.
- 마지막 명령은 뉴스와 정책의 수집 기간이 같은지도 경고한다. 기준은 [docs/period_policy.md](docs/period_policy.md).
- 정책 자료 수집과 집계 단계는 B 구현 후 추가한다. 대시보드의 **일간 보기**는 집계 결과에 `daily` 기간이 있어야 나온다.

## 4. 결과 확인

| 파일 | 내용 |
|---|---|
| `data/processed/documents.csv` | 수집 문서 1건당 1행. `status`/`exclusion_reason` 으로 분석·제외·실패 구분 |
| `data/processed/document_keywords.csv` | 문서 × 표준 키워드 |
| `data/processed/collect_log.json` | 수집 성공·제외·실패 건수와 사유 |
| `data/processed/dashboard.html` | 대시보드 (집계 CSV `keyword_stats.csv` 필요) |

필드 정의는 [docs/data_dictionary.md](docs/data_dictionary.md).

## 5. 분석 기준

- 사전 기반 매칭, 동의어는 표준 키워드로 정규화한다. 규칙: [docs/extraction_rules.md](docs/extraction_rules.md), 사전: [docs/dictionary_v1.md](docs/dictionary_v1.md)
- 주 지표는 문서 빈도, 출현 횟수는 보조 지표다. 일간은 하루, 주간은 월~일, 월간은 달력 월, 한국 표준시 기준. 뉴스와 정책은 같은 기간으로 비교한다 ([docs/period_policy.md](docs/period_policy.md))
- 키워드 출현을 곧 기술의 중요도나 정책 효과로 해석하지 않는다. 정확도·탐지 성능 수치는 주장하지 않는다

## 6. 문제 해결

| 증상 | 조치 |
|---|---|
| 한글이 깨져 보임 | `PYTHONIOENCODING=utf-8` 지정 |
| `수집 실패 (fetch_error)` | 네트워크 확인 후 직접 다시 실행. 자동 재시도하지 않는다 |
| `--text-dir가 지정되지 않음` 오류 | 요약 범위 문서가 있으면 `--text-dir data/processed/texts` 를 준다 |
| 검증 오류가 나옴 | 출력된 파일·행 번호의 값을 [데이터 사전](docs/data_dictionary.md)과 비교한다 |

## 7. 운영 주의

- 출처 이용 조건은 [docs/source_policy.md](docs/source_policy.md)에 기록한다. 확인 전에는 수집하지 않는다
- 로그인·접근 제한을 우회하지 않고, 출처가 안내한 호출 제한을 지킨다
- 원문 전문과 피드 요약은 `data/raw/`, `data/processed/` 에 두고 Git에 올리지 않는다. 공개 저장소에는 메타데이터·집계값·샘플만 둔다
- 비밀값은 `.env` 에 두고 `.env.example` 만 추적한다

## 8. 검증

- 단위·fixture 테스트: `python -m unittest discover -s tests` (정답은 `tests/fixtures/expected/`)
- 재현성 점검 기록: [docs/validation.md](docs/validation.md)
- 다른 담당자 환경에서의 실행 기록(#26)은 아직 없다
