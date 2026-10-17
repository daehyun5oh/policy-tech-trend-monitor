# CLAUDE.md — 뉴스·정책 기반 디지털·ICT 기술키워드 모니터링 도구

두 담당자(A=hyeon0, B=daehyun5oh)가 각자의 Claude로 개발한다. 이 파일은 둘이 **같은 기준**으로 일하게 하는 안내다. 작업 전에 읽고, 규칙을 바꿀 때는 이 파일도 함께 고친다.

## 먼저 읽을 것

| 문서 | 내용 |
|---|---|
| `기획안.md` | 확정 범위, 지표 정의, 완료 기준 C1~C10, 8주 로드맵 |
| `README.md` | 설치·실행 명령 (빠른 시작은 실제 구현과 일치해야 함) |
| `docs/data_dictionary.md` | 공통 CSV 3종의 필드·허용값 (B 담당, 규격의 기준) |
| `docs/extraction_rules.md` | 키워드 매칭·정규화 규칙 |
| `docs/source_policy.md`, `config/sources.csv` | 출처별 이용 조건과 수집 허용 범위 |
| `docs/period_policy.md` | 뉴스·정책 기간 통일과 공통 범위 해석 |
| `docs/topic_filter_rules.md`, `docs/context_indicators.md` | 설계안(미구현, 합의 전). 구현 전에 상대와 합의 |

## 역할

| 영역 | 주담당 |
|---|---|
| 뉴스 수집·분석, 대시보드, 보고서↔화면↔CSV 수치 대조 | A |
| 정책 수집·정제·중복 제거, 공통 CSV·집계(`aggregate.py`), 보고서 생성 | B |
| 추출·집계 규칙, 키워드 사전, README, 최종 시연 | A·B 공동 |

상대 담당 영역의 규격을 바꿔야 하면 코드를 바로 고치지 말고 PR·이슈 코멘트로 먼저 합의한다.

## 실행과 검증

```bash
python -m unittest discover -s tests     # 설치 없이 실행 (표준 라이브러리 unittest)
python src/validate_csv.py --documents data/processed/documents.csv --keywords data/processed/document_keywords.csv --sources config/sources.csv
```

- **의존성 경계**: 외부 패키지는 수집 계층(`src/collect_policy.py`)에서만 쓰고 `requirements.txt`에 버전을 고정한다. 추출·집계·검증·대시보드·테스트는 표준 라이브러리만 쓴다(`docs/environment.md`, `tests/test_stdlib_only.py`). 경계를 넓힐 때는 상대와 합의한다.
- Windows 콘솔에서 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 지정한다. `python`이 실행되지 않으면 Microsoft Store 별칭 때문일 수 있다(환경 문서 참조).
- `data/raw/`, `data/processed/`는 Git에 올리지 않는다.
- 새 기능에는 테스트를 함께 둔다. 변경한 뒤에는 위 테스트가 모두 통과해야 한다.

## 반드시 지킬 규칙

1. **정답은 손으로 계산한다.** `tests/fixtures/expected/`의 기대값은 구현 코드의 출력을 복사해서 만들지 않는다. 구현을 맞추려고 기대값을 고치지 않는다.
2. **규격은 `docs/data_dictionary.md`를 따른다.** `src/schema.py`와 값이 어긋나면 문서를 기준으로 맞추고, 바꿀 때는 양쪽 수집기·화면·보고서·검증기·정답 fixture에 함께 반영한다.
3. **출처 이용 조건을 먼저 확인한다.** 확인 전에는 수집하지 않는다. 로그인·접근 제한을 우회하지 않고, 출처마다 요청 1회, 재시도 없음, 출처 사이에 간격을 둔다. 제공자가 닫은 경로를 다른 방법으로 대체 수집하지 않는다.
4. **재배포 범위를 지킨다.** `config/sources.csv`의 `redistribution`에 따라 공개 산출물에 싣는 범위가 다르다. `local_only_no_publish`는 제목·본문을 싣지 않고 URL·발행일만 쓴다. 원문 전문과 피드 요약은 저장소에 올리지 않는다.
5. **외부에는 사용자 허락 없이 아무것도 보내지 않는다.** 매체·기관에 문의 메일을 보내거나 이슈·PR 코멘트·메시지를 올리는 일은 사용자가 시킬 때만 한다. 초안만 요청받았으면 초안만 보여 준다.
6. **해석의 한계를 지킨다(기획안 §2).** 빈도는 언급량이다. 기술의 혁신성·성숙도·시장성·유망성이나 영향력을 판정하지 않는다. "신흥기술 자동 탐지", "미래 기술 예측", "급증" 같은 자동 판정 표현을 쓰지 않는다. 정확도·탐지 성능 수치를 주장하지 않는다.
7. **기간은 뉴스·정책이 같다.** 수집 기간은 정책과 같은 값으로 두고, 통합·비교는 두 유형의 자료가 모두 있는 공통 범위에서만 해석한다(`docs/period_policy.md`).
8. **지표는 한 곳에서 계산한다.** 대시보드와 보고서는 같은 집계 CSV를 읽는다. 화면에서 따로 다시 계산한 값을 보고서와 다르게 보여 주지 않는다.
9. **날짜가 없는 문서는 수집일로 대체하지 않는다.** 제외하고 건수와 사유를 남긴다.
10. **실제 결과를 있는 그대로 보고한다.** 실패한 테스트, 건너뛴 단계, 확인하지 못한 것을 숨기지 않는다.

## Git 작업 방식

- `main`에 직접 커밋하지 않는다. 이슈 하나 단위로 짧은 브랜치(`feat/…`, `docs/…`, `fix/…`)를 만들고 **작은 PR**로 올린다.
- PR은 상대가 리뷰한 뒤 `main`에 합친다. 합친 뒤에는 상대와 자기 브랜치가 `main`을 바로 받는다(오래 떨어져 있지 않는다).
- 작업을 시작하기 전에 `git fetch` 후 `main`의 최신 내용을 받는다.
- 커밋 메시지는 한국어로 `feat:`, `fix:`, `docs:`, `test:`, `config:`, `merge:` 접두를 쓴다.
- 이슈를 닫을 때는 완료 기준을 하나씩 대조한 근거 코멘트를 남긴다. 코드가 아직 `main`에 없으면 "머지 전"이라고 적는다.
- Claude가 만든 커밋·PR에는 시스템이 지정한 공동 작성자 표시를 붙인다.

## 흔한 함정

- `analysis_scope` 값은 `title` / `title_summary` / `title_body`다(데이터 사전 기준).
- 부분 기간은 수집 종료일이 기간 안에 있거나 기간이 그 뒤까지 이어질 때다. 키워드 없는 분석 문서도 비율의 분모에 포함한다.
- 통합(`all`) 문서 빈도는 뉴스·정책을 더하지 않고 문서 ID의 합집합으로 센다.
- 뉴스 피드는 최근 기사만 주므로 `collect_news.py rss`는 `data/raw/news_items.csv`에 누적한다. `documents.csv`의 뉴스가 아닌 행은 지우지 않는다.
- 키워드가 없다는 이유로 문서를 제외하지 않는다. 비ICT 제외는 별도 규칙이다(`docs/topic_filter_rules.md`, 설계 단계).
