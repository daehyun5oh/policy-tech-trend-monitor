# 뉴스·정책 기반 디지털·ICT 기술키워드 모니터링 도구

뉴스·정책 자료에서 기술키워드를 추출·정규화하고, 기간별 빈도와 순위를 대시보드와 보고서로 보여 주는 수동 실행 시제품이다. 범위와 완료 기준은 [기획안.md](기획안.md)에 있다.

> **구현 상태** 뉴스·정책 수집, 정제·중복 제거, 키워드 추출, 집계(일간·주간·월간), 보고서 생성, 종단 실행(`pipeline.py`), CSV 검증, 대시보드가 모두 `main`에서 동작한다. 정책 쪽 수집은 외부 패키지가 필요하다(§1). 미국·중국 목록 페이지 수집(`html_list`)은 과거 자료를 모두 받을 수 없는 출처가 있다(`docs/source_policy.md`). 아직 남은 것: 비ICT 기사 제외 규칙과 맥락·확산 지표의 집계 구현(설계안 단계), 전달 묶음 최종 점검.

### 해석 한계 — 먼저 읽어주세요

**빈도는 수집한 자료 안의 언급량이다.** 기술의 혁신성·성숙도·시장성이나 미래 유망성을 판정하지 않는다. 이 도구를 "신흥기술 자동 탐지" 또는 "미래 기술 예측"으로 설명하지 않는다. 기간별 차이를 자동으로 "신규" 또는 "급증"으로 판정하지도 않는다.

## 1. 설치

Python 3.10 이상이 필요하다(3.12, 3.13에서 확인). 추출·검증·집계·보고서·대시보드·테스트와 뉴스 수집은 **외부 패키지 없이** 돈다. 정책 수집기(`collect_policy.py`)를 돌릴 때만 외부 패키지가 필요하다 ([docs/environment.md](docs/environment.md) 참고).

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

비밀값이 필요한 출처를 쓸 때는 `.env.example` 을 복사해 `.env` 를 만들고 값을 채운다. `.env` 는 Git에 올리지 않는다.

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

정책 쪽 종단 흐름(수집 → 정제 → 추출 → 집계 → 보고서)을 네트워크 없이 한 번에 보려면 고정 샘플을 쓴다.

```bash
python src/pipeline.py --sample --with-report
```

## 3. 실제 자료 실행

### 출처 등록

`config/sources.csv` 에 출처를 등록한다. **`access_check_status` 가 `terms_ok` 또는 `terms_ok_local` 인 출처만 수집한다.** 이용 조건 확인 기록은 [docs/source_policy.md](docs/source_policy.md)에 있다.

현재 1단계 수집 대상은 정책 6곳과 뉴스 4곳이다.

| 출처 | 국가 | 담당 | 재배포 조건 |
|---|---|---|---|
| 과기정통부 보도자료 | 한국 | B | `kogl_per_item` — 자료별 공공누리 유형 확인 |
| 백악관 OSTP / NSF / DOE Newsroom | 미국 | B | `text_only_no_images` — 본문 텍스트만 |
| 국무원 정책 영문판 / 중국과학원 영문판 | 중국 | B | `local_only_no_publish` — 로컬 분석만, 제목·본문 비공개 |
| 긱뉴스 / 전자신문 AI·SW / ZDNet Korea / 보안뉴스 IT·과학 | 한국 | A | `metadata_only` — 제목·URL·발행일과 집계값만 공개 |

**수집 기간** 2026-05-01 ~ 2026-10-31 (6개월). 뉴스와 정책은 같은 기간을 쓴다 ([docs/period_policy.md](docs/period_policy.md)).

과기정통부 과학기술정책·주간동향 피드와 과기부 영문 Press Room은 갱신이 멈춰 있어 1단계에서 제외했고(`access_ok_stale`), 정책브리핑은 제공자가 RSS를 중단해 제외했다(`blocked`).

### 수집·분석 순서

```bash
python src/collect_news.py rss --from 2026-10-01 --to 2026-10-31
python src/collect_policy.py --from 2026-10-01 --to 2026-10-31
python src/extract_keywords.py
python src/aggregate.py
python src/validate_csv.py --documents data/processed/documents.csv --keywords data/processed/document_keywords.csv --sources config/sources.csv
python src/generate_report.py --period-type weekly
```

처음에는 범위를 좁혀 확인하는 편이 안전하다.

```bash
python src/collect_policy.py --dry-run
python src/collect_policy.py --source-id policy-kr-001 --limit 10
```

- 뉴스 피드는 최근 15~50건만 주기 때문에, 실행할 때마다 받은 항목을 `data/raw/news_items.csv` 에 **누적**하고 `documents.csv` 는 누적본 전체로 다시 만든다. 며칠 모으면 일간·주간 추이가 쌓인다(Git에는 올리지 않음).
- 두 수집기는 `documents.csv` 의 다른 유형 행을 지우지 않고 합친다.
- 추출기는 본문 파일(`<document_id>.txt`)을 `data/raw`(정책 수집기)와 `data/processed/texts`(뉴스 수집기)에서 차례로 찾는다. 다른 폴더는 `--text-dir`을 여러 번 줘서 지정한다.
- 특정 출처만: `--sources news-002,news-004`(뉴스), `--source-id policy-kr-001`(정책). 누적 없이 이번 결과만: `--no-accumulate`.
- 한 출처가 실패해도 나머지는 수집하고, 실패한 출처는 종료 코드와 수집 기록(`data/processed/collect_log*.json`)의 사유(`fetch_error`, `parse_error`)로 알린다. **자동 재시도는 하지 않는다.**
- 검증 명령은 뉴스와 정책의 수집 기간이 같은지도 경고한다.
- **정기 수집(뉴스)**: 피드는 최근 기사만 주므로 `python src/run_daily.py` 하나로 수집 → 추출 → 집계 → 검증 → 대시보드 갱신을 돌리고, Windows 작업 스케줄러로 하루 두 번 자동 실행할 수 있다. 설정·확인·삭제 방법과 한계는 [docs/automation.md](docs/automation.md). 정책 수집은 외부 패키지가 필요해 이 실행기에 들어 있지 않다.
- **자동 수집 설정(Windows, 처음 한 번)**: 아래를 PowerShell에서 저장소 폴더 기준으로 실행하면 매일 09:00·21:00에 `run_daily.py`가 돈다. `main`을 체크아웃한 상태로 두고, 노트북이면 전원을 꽂아 둔다.

  ```powershell
  $repo = (Get-Location).Path
  $py   = (Get-Command pythonw).Source
  $act  = New-ScheduledTaskAction -Execute $py -Argument "src/run_daily.py" -WorkingDirectory $repo
  $trg  = @((New-ScheduledTaskTrigger -Daily -At 09:00), (New-ScheduledTaskTrigger -Daily -At 21:00))
  $set  = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
  Register-ScheduledTask -TaskName PolicyTechTrendMonitor-Collect -Action $act -Trigger $trg -Settings $set
  ```

  확인은 `Get-ScheduledTask -TaskName PolicyTechTrendMonitor-Collect | Get-ScheduledTaskInfo`, 기록은 `data/logs/`. 절전 중 PC를 깨우려면 전원 옵션의 "절전 모드 해제 타이머"가 켜져 있어야 한다. 자세한 설명·삭제·한계는 [docs/automation.md](docs/automation.md). 정책 수집은 외부 패키지가 필요해 이 실행기에 들어 있지 않다.
- 대시보드의 **일간 보기**는 집계 결과에 `daily` 기간이 있어야 나온다.

## 4. 결과 확인

| 파일 | 내용 |
|---|---|
| `data/processed/documents.csv` | 수집 문서 1건당 1행. `status`/`exclusion_reason` 으로 분석·제외·실패 구분 |
| `data/processed/document_keywords.csv` | 문서 × 표준 키워드 |
| `data/processed/keyword_stats.csv` | 기간·자료유형별 문서 빈도·출현 횟수·비율·순위 |
| `data/processed/collect_log.json` | 수집 성공·제외·실패 건수와 사유 |
| `data/processed/dashboard.html` | 대시보드 (집계 CSV 필요). 화면은 네 탭이다: **평소와 비교**(직전 기간 대비 키워드 비중의 변화), **뉴스 vs 정책**(같은 기간 두 유형의 비중 차이), **분야 안에서**(큰 주제 안의 키워드 비중), **순위·추이**. 키워드를 누르면 설명·분야, 출처 수·연속 기간, 근거 문서·함께 나온 키워드·출처·표기를 볼 수 있다. 정의와 한계는 `docs/insight_views.md` |
| `reports/` | 주간·월간 보고서 (예시는 `reports/examples/`) |

필드 정의는 [docs/data_dictionary.md](docs/data_dictionary.md).

## 5. 분석 기준

- 사전 기반 매칭, 동의어는 표준 키워드로 정규화한다. 규칙: [docs/extraction_rules.md](docs/extraction_rules.md), 사전: [docs/dictionary_v1.md](docs/dictionary_v1.md)
- 주 지표는 문서 빈도, 출현 횟수는 보조 지표다. 일간은 하루, 주간은 월~일, 월간은 달력 월, 한국 표준시 기준. 뉴스와 정책은 같은 기간으로 비교한다 ([docs/period_policy.md](docs/period_policy.md))
- 키워드 출현을 곧 기술의 중요도나 정책 효과로 해석하지 않는다. 정확도·탐지 성능 수치는 주장하지 않는다
- 보고서와 대시보드는 같은 집계 CSV를 읽는다. 화면에서 따로 다시 계산한 값을 보고서와 다르게 보여 주지 않는다

## 6. 문제 해결

| 증상 | 조치 |
|---|---|
| 한글이 깨져 보임 | `PYTHONIOENCODING=utf-8` 지정 |
| `수집 실패 (fetch_error)` | 네트워크와 [docs/source_policy.md](docs/source_policy.md)의 호출 제한을 확인한 뒤 직접 다시 실행. 자동 재시도하지 않는다 |
| `--text-dir가 지정되지 않음` 오류 | 요약 범위 문서가 있으면 `--text-dir data/processed/texts` 를 준다 |
| 검증 오류가 나옴 | 출력된 파일·행 번호의 값을 [데이터 사전](docs/data_dictionary.md)과 비교한다 |
| API 키 누락 | `.env` 가 있는지, 변수명이 `.env.example` 과 같은지 확인한다 |
| 발행일 없는 문서 | 수집일로 대체하지 않고 기간 집계에서 제외된다. `exclusion_reason=no_published_date` 로 기록된다 |
| 빈 결과 | 부분 기간·수집 실패와 구분해 표시된다. `total_documents` 가 0이면 비율·순위는 `해당 없음` |
| 스캔 PDF | OCR은 필수 범위 밖이다. `failed` / `pdf_scan_unsupported` 로 기록된다 |

## 7. 운영 주의

- 출처 이용 조건은 [docs/source_policy.md](docs/source_policy.md)에 기록한다. **확인 전에는 수집하지 않는다**
- **로그인·유료·접근제한을 우회하지 않는다.** 출처가 안내한 호출 제한을 지킨다
- 제공자가 기계적 수집 경로를 닫은 출처는 다른 경로로 대체 수집하지 않는다 (정책브리핑이 이에 해당해 제외했다)
- **열람 가능**과 **재배포 가능**은 다르다. 원문 전문과 피드 요약은 `data/raw/`, `data/processed/` 에 두고 Git에 올리지 않는다. 공개 저장소에는 메타데이터·집계값·샘플만 둔다
- 불필요한 개인정보를 수집하지 않는다. 과기정통부 목록에 노출되는 담당자 이름·연락처는 수집하지 않는다
- 비밀값은 `.env` 에만 둔다. 노출된 키는 폐기·재발급한다

## 8. 검증

- 단위·fixture 테스트: `python -m unittest discover -s tests` (정답은 `tests/fixtures/expected/`)
- 재현성 점검과 완료 기준 C1~C10의 상태: [docs/validation.md](docs/validation.md)
- 테스트는 네트워크에 접속하지 않고 고정 입력만 쓴다

| 파일 | 대상 | 완료기준 |
|---|---|---|
| `tests/test_collect_news.py` | 뉴스 수집·누적·제외 사유 | C1 |
| `tests/test_extract_keywords.py`, `tests/test_fixture_consistency.py` | 키워드 추출과 정답 fixture 일치, 재현성 | C3·C8 |
| `tests/test_normalize.py` | URL 정규화, 해시·식별자 결정성, 정제, 중복 제거, 필수값 | C2 |
| `tests/test_aggregate.py` | 빈도·비율·동률 순위·기간 경계를 수작업 정답과 대조 | C4 |
| `tests/test_generate_report.py` | 보고서 수치가 CSV와 일치, 제한 출처 제목 비공개 | C7 |
| `tests/test_pipeline.py` | 고정 샘플 종단 실행, 빈 결과·오류 사유 기록 | C1·C8 |
| `tests/test_dashboard.py` | 대시보드 화면 값이 집계 CSV와 일치 | C6 |
| `tests/test_validate_csv.py`, `tests/test_stdlib_only.py` | CSV 규격 검증, 의존성 경계 | C2 |

- 다른 담당자 환경에서의 실행 기록(#26)은 진행 중이다
