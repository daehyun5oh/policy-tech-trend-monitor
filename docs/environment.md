# 실행 환경·대시보드 도구 결정 (이슈 #3)

| 항목 | 결정 | 근거 |
|---|---|---|
| 언어 | Python 3.10 이상. 검증: A 3.12.10, B 3.13.15 | 기획안 §8 기본안. 두 담당자가 같은 버전대를 쓰기 쉽다 |
| 의존성 | **수집 계층에서만 외부 패키지를 허용**하고 버전을 고정한다(`requirements.txt`). 추출·집계·검증·대시보드·테스트는 **표준 라이브러리만** 쓴다 | 정책 수집기가 RSS 파싱과 HTML 크롤링(미국·중국 5곳, #37)에 `feedparser`, `requests`, `beautifulsoup4`, `lxml`을 쓴다. 그 밖의 단계는 설치 없이 돌게 두어 "README만으로 실행"(C9)과 재현성(C8) 부담을 줄인다 |
| 테스트 | `python -m unittest discover -s tests` | 설치 불필요. pytest도 같은 테스트를 실행할 수 있다 |
| 가상환경 | 수집기를 돌릴 때 사용을 권장 (`python -m venv .venv`) | 외부 패키지 버전이 시스템과 섞이지 않는다(C8) |
| 대시보드 | `dashboard/app.py` 가 CSV를 읽어 **서버 없이 열리는 HTML 파일 하나**를 만든다 | 후보: Streamlit, Dash, 정적 HTML |
| 설정 | `config/` 의 CSV, 비밀값은 `.env` (`.env.example` 참조) | 비밀값은 저장소에 두지 않는다(기획안 §11) |

## 대시보드 도구 비교

| 후보 | 장점 | 단점 | 판단 |
|---|---|---|---|
| Streamlit | 화면 구성이 빠르다 | 설치 필요, 서버 실행, 버전에 따라 화면이 달라진다 | 제외 |
| Dash/Plotly | 상호작용 풍부 | 설치 필요, 의존성이 많다 | 제외 |
| **정적 HTML + 내장 JS** | 설치·서버·CDN 불필요, 파일 하나를 열면 끝, 수치가 CSV와 같음을 확인하기 쉽다 | 그래프를 직접 그려야 한다 | **선정** |

## 의존성 경계

| 영역 | 외부 패키지 | 근거 |
|---|---|---|
| `src/collect_policy.py` (B) | 허용 (`requirements.txt`) | RSS·HTML 파싱을 직접 구현하면 오류 위험이 큼 |
| `src/collect_news.py` (A) | 쓰지 않음 | 표준 라이브러리로 충분. 바꿀 이유가 없다 |
| `src/extract_keywords.py`, `src/validate_csv.py`, `src/schema.py`, `dashboard/app.py` | **쓰지 않음** | 이 단계들은 설치 없이 실행되어야 한다 |
| `src/collect_html.py` (B) | 허용 (`requests`, `beautifulsoup4`, `lxml`) | 출처마다 HTML 구조가 달라 파서가 필요 |
| `src/normalize.py`, `src/aggregate.py`, `src/generate_report.py`, `src/pipeline.py` (B) | **쓰지 않음** | B의 방침: pandas 없이 표준 라이브러리 `csv`로 처리. 실제로 표준 라이브러리만 쓴다 |

`tests/test_stdlib_only.py`가 "쓰지 않음" 영역이 표준 라이브러리만 가져오는지 검사한다(B 모듈은 파일이 `main`에 들어온 뒤부터 자동으로 검사). 이 경계를 넓히려면(예: 집계에 외부 패키지) 이 문서와 테스트를 함께 바꾸고 상대와 합의한다.

## 테스트 규칙

- 모든 테스트는 **`unittest.TestCase`** 클래스로 쓴다. `python -m unittest discover -s tests`가 설치 없이 전부 모아 실행해야 한다.
- pytest 함수형 테스트(`def test_...`만 있고 클래스가 없는 파일)는 `unittest discover`가 **모으지 못한다.** pytest가 설치된 환경에서는 오류 없이 조용히 빠지고, 설치되지 않은 환경에서는 `import pytest` 오류로 실패한다. 새 테스트는 처음부터 `unittest`로 쓰고, 기존 pytest 테스트는 `unittest`로 바꾼다(기계적 변환: `tmp_path` → `tempfile.TemporaryDirectory`, `monkeypatch` → `unittest.mock.patch`, `parametrize` → `subTest`, `pytest.raises` → `assertRaises`).
- **수집 계층(`collect_policy.py`, `collect_html.py`)을 검증하는 테스트**는 수집 의존성(`requests`, `beautifulsoup4` 등)을 가져와도 된다. 단 의존성이 없는 환경에서 `unittest`가 실패하지 않도록 **가져오기에 실패하면 건너뛰게**(`unittest.skip`, 사유에 `pip install -r requirements.txt` 안내) 쓴다. 건너뛴 사실은 실행 결과(`skipped=N`)에 보인다.

## 확인 상태

- A 환경(Windows 11, Python 3.12.10): 설치 없이 테스트 통과, 수집 → 추출 → 검증 → 대시보드 실행 확인.
- B 환경(Windows, Python 3.13.15 설치 확인, B 기록): 우리 코드(`main`)의 실행은 **미확인.** 이슈 #26에서 확인하고 이 문서에 결과를 적는다.
- 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8` 환경변수를 지정한다(Windows 콘솔).
- **Windows 주의 (B 확인)**: `python`을 입력해도 아무것도 실행되지 않으면 Microsoft Store 별칭이 앞서 잡힌 것이다. 설정 → 앱 → 앱 실행 별칭에서 `python.exe`/`python3.exe`를 끄거나, `py` 런처를 쓰거나, 가상환경의 `python.exe`를 직접 호출한다.
