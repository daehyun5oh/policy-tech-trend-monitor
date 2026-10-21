# 고정 샘플

API 키나 네트워크 없이 파이프라인을 끝까지 돌려보기 위한 입력입니다 (기획안 §8, 완료기준 C9).

## 중요 — 합성 데이터입니다

`policy_sample_input.csv`, `document_keywords_sample.csv`, `sources_sample.csv` 는 **사람이 만든 가짜 자료**입니다. 실제 기관이 발표한 내용이 아니고, 주소도 `sample.example` 로 실재하지 않습니다. 재배포 조건 문제가 없도록 일부러 합성했습니다.

| 파일 | 내용 | 비고 |
|---|---|---|
| `policy_sample_input.csv` | 문서 7행 | 정상 5건 + 발행일 없는 1건 + 중복 1건 |
| `document_keywords_sample.csv` | 문서-키워드 6행 | **키워드 추출(이슈 #11, 담당 A)의 대역** |
| `sources_sample.csv` | 출처 3곳 | `샘플해외기관` 은 `local_only_no_publish` |

## 샘플에 일부러 섞어 둔 경우

파이프라인이 규칙대로 처리하는지 확인하려고 아래를 넣었습니다.

| 경우 | 기대 동작 |
|---|---|
| 발행일이 빈 행 | 수집일로 대체하지 않고 `no_published_date` 로 제외 |
| 같은 주소에 `utm_source` 만 붙은 행 | URL 정규화 후 같은 문서로 판정, 한 행만 남음 |
| 본문에 HWP 메타데이터 블록 | 정제 단계에서 제거 |
| 본문에 전화번호 | 기획안 §11에 따라 삭제 |
| `local_only_no_publish` 출처 | 보고서에 제목을 싣지 않음 |

## 키워드 파일이 `url` 기준인 이유

`document_id` 는 원문 식별자나 정규화 URL을 해시해 만듭니다(`docs/data_dictionary.md` §5). 샘플 파일에 해시를 적어 두면 규칙이 바뀔 때마다 고쳐야 하므로, 샘플은 `url` 로 적고 파이프라인이 `document_id` 로 바꿔 넣습니다.

A의 키워드 추출기는 `document_id` 를 바로 내보내면 됩니다. 대역이 필요한 동안만 쓰는 방식입니다.

## 실행

```
.venv\Scripts\python.exe src/pipeline.py --sample
```
