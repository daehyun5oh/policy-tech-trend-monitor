# 정기 수집 (자동 실행)

뉴스 피드는 최근 기사만 주기 때문에(ZDNet Korea 약 15시간, 전자신문 AI·SW 약 이틀, 긱뉴스 약 하루, 보안뉴스 약 4주) 한 번 받아서는 기간이 늘지 않는다. 하루에 두 번 자동으로 받아 `data/raw/news_items.csv`에 쌓으면 일간·주간 비교가 의미를 갖게 된다. 기획안은 "정기 자동 실행"을 후속 과제로 두었는데, 대시보드의 비교 화면(`docs/insight_views.md`)이 자료 기간에 달려 있어 앞당겼다.

## 무엇이 실행되나

`python src/run_daily.py` 하나가 아래를 순서대로 돌린다(모두 기존 명령이다).

| 단계 | 명령 | 하는 일 |
|---|---|---|
| collect | `collect_news.py rss --from … --to …` | 뉴스 4곳의 공개 피드를 **출처마다 1회** 요청해 누적 저장소에 쌓고 `documents.csv`를 다시 만든다 |
| extract | `extract_keywords.py` | 키워드 추출 |
| aggregate | `aggregate.py` | 일간·주간·월간 집계 |
| validate | `validate_csv.py --dir … --sources …` | CSV 규격 검사, 뉴스·정책 기간 경고 |
| dashboard | `dashboard/app.py --dir data/processed` | `data/processed/dashboard.html` 갱신 |

- 수집 기간은 `config/sources.csv`의 뉴스 출처 `collect_from`/`collect_to`를 읽는다.
- **재시도하지 않는다.** 한 단계가 실패하면 사유를 기록하고 나머지를 계속한다(이미 쌓인 자료로 대시보드는 갱신된다).
- 실행 기록은 `data/logs/run_daily_<날짜>_<시각>.json`(최근 60개만 보관, Git 제외). 단계별 종료 코드·소요 시간·출력 끝부분과 실행한 코드의 브랜치·커밋이 들어 있다.
- 동시에 두 번 돌지 않도록 `data/processed/.run_daily.lock`을 쓴다(2시간이 지난 잠금은 무시).
- 정책 수집(`collect_policy.py`)은 외부 패키지가 필요해 **넣지 않았다.** 정책 자료는 B의 환경에서 같은 방식으로 돌린다.
- 종료 코드: 0 모두 성공 / 1 일부 실패 / 3 이미 실행 중이라 건너뜀.

```bash
python src/run_daily.py --dry-run     # 실행할 단계만 보기
python src/run_daily.py --no-collect  # 수집 없이 쌓인 자료로 분석·대시보드만 갱신
```

## Windows 작업 스케줄러 등록 (A의 PC)

작업 이름 `PolicyTechTrendMonitor-Collect`, **매일 09:00과 21:00**.

| 설정 | 값 |
|---|---|
| 실행 | `pythonw.exe src/run_daily.py` (창 없음), 작업 폴더는 이 저장소 |
| 계정 | 현재 사용자, **로그온해 있을 때만** 실행(비밀번호를 저장하지 않는다) |
| 놓친 실행 | 시작 시각에 PC가 꺼져 있었으면 켜진 뒤 가능한 빨리 실행(`StartWhenAvailable`) |
| 중복 | 이미 실행 중이면 새 실행을 무시 |
| 제한 시간 | 30분 |

확인·실행·삭제:

```powershell
Get-ScheduledTask -TaskName PolicyTechTrendMonitor-Collect | Get-ScheduledTaskInfo   # 마지막·다음 실행 시각, 결과
Start-ScheduledTask -TaskName PolicyTechTrendMonitor-Collect                          # 지금 한 번 실행
Unregister-ScheduledTask -TaskName PolicyTechTrendMonitor-Collect -Confirm:$false     # 등록 삭제
```

## 한계와 주의

- **PC가 꺼져 있거나 로그아웃 상태면 실행되지 않는다.** 시각을 놓치면 켜진 뒤 한 번 돌지만, 그 사이에 피드에서 밀려난 기사(특히 ZDNet Korea는 약 15시간치)는 받을 수 없다.
- 예약 작업은 이 저장소 폴더의 **지금 체크아웃된 브랜치의 코드**를 실행한다. 개발하느라 다른 브랜치에 있으면 그 브랜치의 코드가 돈다(실행 기록에 브랜치·커밋과 미커밋 여부가 남는다). 안정적으로 쓰려면 `main`을 체크아웃해 둔다.
- 저장소(`data/raw/news_items.csv`)가 계속 커진다. 기사 한 건이 한 줄이라 몇 달 쌓아도 수 MB 수준이다.
- 출처 이용 조건은 그대로다: 출처마다 요청 1회, 하루 2회, 재시도 없음. 이용 조건이 바뀌면 `config/sources.csv`의 상태를 바꾸면 그 출처는 자동으로 수집에서 빠진다.
