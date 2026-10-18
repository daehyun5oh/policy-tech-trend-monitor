# 시연 리허설 (이슈 #28)

기획안 §12의 시연 순서를 그대로 따라갈 수 있게 만든 대본이다. **아래 명령은 모두 실제로 돌려 확인했다**(2026-10-18, B 환경: Windows 11, Python 3.13.15). 각 단계의 "보여야 하는 것"은 그때 나온 출력이다.

## 시연 전에

| 항목 | 값 |
|---|---|
| 전체 소요 | 약 5분 (네트워크 수집 단계 포함) |
| 네트워크 | 1~5단계는 **필요 없다.** 6단계만 쓴다 |
| 준비물 | 이 저장소, Python 3.10 이상, 브라우저 |

**시작 전에 정리한다.** 이전 실행 결과가 남아 있으면 숫자가 달라진다.

```bash
rm -rf data/processed data/raw reports/weekly reports/monthly
```

Windows PowerShell 에서는 `Remove-Item -Recurse -Force data/processed, data/raw, reports/weekly, reports/monthly` 를 쓴다. 이 폴더들은 Git에 올라가지 않으므로 지워도 저장소에 영향이 없다.

> **머지 상태 주의** 이 대본은 B의 PR 10개(#41~#47, #52~#54)가 모두 `main` 에 들어온 상태를 전제한다. 아직이면 `src/collect_html.py`(해외 수집), `period_type=daily`(일간 보기), `src/pipeline.py`(종단 실행)가 없다. 로컬에서 모두 합쳐 확인했을 때 **테스트 283개가 통과**했다.

---

## 1단계 — 환경 준비 (30초)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python --version
```

**보여야 하는 것**

```
Python 3.13.15
```

**말할 것** 추출·집계·보고서·대시보드·테스트는 외부 패키지 없이 돈다. 설치가 필요한 것은 수집 계층뿐이다(`requests`, `feedparser`, `beautifulsoup4`, `lxml`).

**막히면** Windows에서 `python` 이 아무 반응 없이 끝나면 Microsoft Store 별칭 때문이다. 앱 실행 별칭에서 `python.exe` 를 끄거나 `py` 를 쓴다. 한글이 깨지면 `$env:PYTHONIOENCODING="utf-8"`.

---

## 2단계 — 테스트 (3초)

```bash
python -m unittest discover -s tests
```

**보여야 하는 것**

```
Ran 283 tests in 1.6s

OK
```

**말할 것** 설치 없이 표준 라이브러리만으로 돈다. 수집 계층을 검증하는 테스트는 외부 패키지가 없으면 사유와 함께 건너뛴다. 정답 fixture는 손으로 계산한 값이며 구현 출력을 복사하지 않았다.

---

## 3단계 — 고정 샘플 종단 실행 (1초, 네트워크 없음)

```bash
python src/pipeline.py --sample --with-report
```

**보여야 하는 것**

```
종단 실행 (sample)

[1/5] collect(sample): ok
[2/5] normalize: ok
[3/5] extract(sample): ok
[4/5] aggregate: ok
[5/5] report: ok

  documents.csv: 6행
  document_keywords.csv: 6행
  keyword_stats.csv: 27행
```

**말할 것**

- 입력 7행이 6행이 된다. 주소만 다르고 같은 문서인 1건이 중복 제거된 것이다
- 발행일이 없는 1건은 **수집일로 대체하지 않고** 제외하며 사유를 남긴다
- 단계마다 성공·빈 결과·건너뜀·실패를 사유와 함께 `pipeline_log_*.json` 에 적는다
- 이 샘플은 합성 데이터다(`data/sample/README.md`)

---

## 4단계 — 대시보드 (1초)

```bash
python dashboard/app.py --stats data/processed/keyword_stats.csv --keywords data/processed/document_keywords.csv --documents data/processed/documents.csv --out data/processed/dashboard.html --open
```

**보여야 하는 것**

```
대시보드 생성: data\processed\dashboard.html (기간 16개, 화면 48개)
```

**화면에서 눌러 볼 것 (순서대로)**

1. **기간 단위** 일간 → 주간 → 월간 전환
2. **자료 유형** 통합 → 뉴스 → 정책 전환
3. **키워드 하나 누르기** → 근거 문서 목록이 열린다
4. **자료가 없는 기간** 고르기 → "이 기간에는 분석한 정책 문서가 없습니다. 비율과 순위는 '해당 없음'"
5. **출처별 분석 범위** 표시 확인 → 제목만 / 제목+요약 / 제목+본문 건수

**말할 것** 화면과 보고서는 같은 집계 CSV를 읽는다. 화면에서 따로 계산하지 않는다. 실제 자료 156개 화면·값 1,400개를 CSV와 대조해 불일치 0건을 확인했다(`docs/validation.md` 「대시보드 화면 대조」).

---

## 5단계 — 보고서와 CSV 대조 (1초)

3단계에서 `--with-report` 로 이미 만들어졌다. 따로 만들려면:

```bash
python src/generate_report.py --period-type weekly
```

**열어서 보여줄 것** `reports/weekly/report_weekly_2026-10-05_2026-10-11.md`

**대조해 보일 것** 보고서의 표와 `keyword_stats.csv` 의 같은 기간·유형 행이 일치한다.

| 순위 | 키워드 | 문서 | 비율 | 출현 |
|---|---|---|---|---|
| 1 | 인공지능 | 3건 | 60.00% | 6회 |
| 2 | 반도체 | 2건 | 40.00% | 3회 |
| 3 | 양자 | 1건 | 20.00% | 2회 |

리허설에서 이 3행을 CSV와 글자 단위로 맞춰 **3/3 일치**를 확인했다.

**말할 것**

- 보고서 끝에 해석 한계 4줄이 항상 붙는다. "급증", "유망", "신흥기술 탐지" 같은 단정 표현을 쓰지 않으며 테스트로 막아 두었다
- 재배포가 제한된 출처(중국 2곳)는 근거 문서에 **제목 대신 `제목 비공개`** 와 주소·발행일만 싣는다. 대시보드도 같은 규칙을 따른다

---

## 6단계 — 실제 자료 수동 수집 (20초, 네트워크 필요)

시연에서는 **건수를 줄여** 돌린다. 전체 수집은 몇 분 걸린다.

```bash
python src/collect_policy.py --source-id policy-kr-001 --limit 3 --delay 1
```

```
  피드 50건 중 3건 처리
분석 대상 3건 / 새로 추가 3건 / documents.csv 총 9건
원문 저장 3건 -> data/raw/
규격 검증 통과 (완료기준 C2)
```

```bash
python src/collect_html.py --source-id policy-us-001 --limit 2 --delay 1
```

```
  분류: {'Remarks': 1, 'Releases': 1}
분석 대상 2건 / 새로 추가 2건 / 총 11건
원문 저장 2건 -> data/raw/
규격 검증 통과 (완료기준 C2)
```

**말할 것**

- 출처마다 **요청 1회, 재시도 없음**, 출처 사이에 간격을 둔다
- `access_check_status` 가 `terms_ok` 또는 `terms_ok_local` 인 출처만 수집한다. 확인 전에는 수집하지 않는다
- 미국 출처는 본문 텍스트만 받고 이미지는 받지 않는다
- 두 수집기는 서로의 행을 지우지 않는다. 뉴스 150행 + 정책 152행으로 확인했다

**먼저 보여주면 좋은 것** (요청을 보내지 않는다)

```bash
python src/collect_policy.py --dry-run
```

수집 대상과 건너뛴 출처가 사유와 함께 나온다.

---

## 마무리 — 완료 기준 체크

`docs/validation.md` 를 열어 C1~C10 상태표를 보여 준다.

| 충족 | C1 수집 · C2 필수값·중복 · C4 집계 정확 · C5 추출 품질 · C6 대시보드 · C7 보고서 |
|---|---|
| 부분 | C3(사전 v2 대기) · C8(종단 재실행 비교 남음) · C9(서로의 환경 실행 기록) |
| 미착수 | C10(전달 묶음 점검, 이슈 #27) |

**한계도 함께 말한다** (기획안 §2)

- 빈도는 수집한 자료 안의 **언급량**이다. 기술의 혁신성·성숙도·유망성을 판정하지 않는다
- 출처에 따라 분석 텍스트 길이가 크게 다르다(과기정통부 중간값 98자 ↔ OSTP 8,056자). 출처 간 문서 빈도를 그대로 견주면 안 된다
- NSF·DOE는 목록에서 과거 글을 받을 수 없어 주기적 실행으로 쌓아야 한다
- 사전 표기 일치 방식이라 철자 변형·동음이의어에 약하다. 사례는 `docs/policy_quality_review.md`, `docs/news_quality_review.md`

---

## 리허설 기록

| 항목 | 값 |
|---|---|
| 실행일 | 2026-10-18 |
| 실행자 | B |
| 환경 | Windows 11, Python 3.13.15, 가상환경 |
| 코드 | B의 PR 10개를 로컬에서 모두 합친 상태 |
| 결과 | 1~6단계 **전부 성공.** 테스트 283개 통과, 보고서↔CSV 3/3 일치, 실제 수집 5건 규격 검증 통과 |
| 리허설에서 찾아 고친 것 | 일간 집계(PR #46)와 종단 실행(PR #44)을 함께 합치면 `test_stats_generated` 가 깨지는 것을 발견해 고쳤다. 두 PR을 따로 보면 드러나지 않는다 |
| 남은 것 | A 환경에서의 실행 기록(C9, 이슈 #26) |
