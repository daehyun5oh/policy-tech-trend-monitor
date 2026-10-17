# 검증 기록

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

**범위** 키워드 추출 단계만 점검했다. 집계 단계(`aggregate.py`, #13)는 B 구현 후 같은 방법으로 점검해 이 표에 추가한다.
