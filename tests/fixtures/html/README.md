# 목록 페이지 테스트 입력 (이슈 #37)

`src/collect_html.py` 의 출처별 파서를 네트워크 없이 검증하기 위한 HTML이다.

**실제 페이지를 저장한 것이 아니다.** 모두 **합성 파일**이며, 실제 사이트에서 확인한
**구조(태그·class 이름·날짜 표기 방식)만** 따라 썼다. 제목·본문 문장은 지어낸 것이다.

이렇게 한 이유는 두 가지다.

- 중국 출처는 서면 허가 없는 재배포를 금지하고 있어(`config/sources.csv` 의
  `local_only_no_publish`) 원문을 저장소에 올릴 수 없다
- 미국 출처는 연방저작물이라 올릴 수 있지만, 한쪽만 실제 자료를 쓰면
  테스트 기준이 들쭉날쭉해진다

구조를 확인한 날짜는 2026-10-17 이다. 사이트가 개편되면 이 파일이 아니라
실제 페이지를 다시 확인해 파서와 이 입력을 함께 고쳐야 한다. 이 파일만 고치면
테스트는 통과하지만 실제 수집은 실패한다.

| 파일 | 흉내낸 구조 |
|---|---|
| `ostp_list.html` | 백악관 OSTP 뉴스 목록 (`li.wp-block-post`, `time[datetime]`), 다음 쪽 링크 포함 |
| `nsf_list.html` | NSF News 목록 (`div.latest-news-teaser`), 팟캐스트 1건 섞임 |
| `doe_list.html` | DOE Newsroom 목록 (`div.collection-item-wrapper`, `October 14, 2026` 표기) |
| `govcn_list.html` | 국무원 정책 목록 (`li > h3 a` + `h4`, `2026/10/09` 표기, 프로토콜 상대 주소) |
| `cas_list.html` | 중국과학원 목록 (`ul.up-list li`, `Oct 04, 2026` 표기, 요약이 링크 안에 들어 있음) |
| `ostp_detail.html` | 상세 페이지 (`div.entry-content`) |
| `govcn_detail.html` | 상세 페이지 (`div.Artical_Content`), 본문 앞에 상단 메뉴 있음 |
| `cas_detail.html` | 상세 페이지 (`div.detail-contents`), 본문 앞에 상단 메뉴와 꼬리에 관련 기사 목록 있음 |
