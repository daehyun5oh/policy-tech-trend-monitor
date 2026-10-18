"""목록 페이지 수집기 검증 (이슈 #37)

네트워크에 접속하지 않는다. `tests/fixtures/html/` 의 합성 HTML만 쓴다.
그 파일들은 실제 페이지가 아니라 구조만 흉내낸 것이다(같은 폴더 README 참고).

`unittest` 로 쓴다(`docs/environment.md`). 다만 수집기가 `beautifulsoup4`,
`lxml`, `requests` 를 쓰기 때문에 그 패키지가 없으면 전체를 건너뛴다.
의존성 경계상 수집 계층만 외부 패키지를 쓰므로, 수집기를 검증하는 이
파일도 같은 경계 안에 있다.
"""

from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "html"
sys.path.insert(0, str(ROOT / "src"))

try:
    import collect_html as ch
    MISSING = ""
except ImportError as exc:  # 수집용 외부 패키지 미설치
    ch = None
    MISSING = f"수집 계층 의존성 없음: {exc}. pip install -r requirements.txt"

WINDOW = ("2026-05-01", "2026-10-31")


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@unittest.skipIf(ch is None, MISSING)
class ParserTest(unittest.TestCase):
    """출처별 목록 파서가 제목·주소·발행일·분류를 꺼내는지."""

    def test_ostp_items(self):
        items = ch.parse_ostp(read("ostp_list.html"),
                              "https://www.whitehouse.gov/ostp/news/")
        self.assertEqual(len(items), 3, "li.wp-block-post 3건을 모두 찾아야 한다")
        first = items[0]
        self.assertEqual(first["title"], "Sample Release on Quantum Testbeds")
        self.assertEqual(first["url"],
                         "https://www.whitehouse.gov/releases/2026/10/"
                         "sample-release-on-quantum-testbeds/")
        self.assertEqual(first["category"], "Releases")
        self.assertEqual(ch.nz.parse_published_date(first["published_raw"]),
                         "2026-10-09")

    def test_ostp_ignores_navigation_links(self):
        """상단 메뉴에도 /releases/ 링크가 있어 항목으로 세면 안 된다."""
        items = ch.parse_ostp(read("ostp_list.html"), "https://www.whitehouse.gov/ostp/news/")
        self.assertNotIn("Releases", [i["title"] for i in items])

    def test_ostp_remarks_path_and_category(self):
        """연설 후기는 /remarks/ 경로이고 날짜가 time 태그 없이 글자로 온다."""
        items = ch.parse_ostp(read("ostp_list.html"), "https://www.whitehouse.gov/ostp/news/")
        remarks = [i for i in items if i["category"] == "Remarks"]
        self.assertEqual(len(remarks), 1)
        self.assertIn("/remarks/", remarks[0]["url"])
        self.assertEqual(ch.nz.parse_published_date(remarks[0]["published_raw"]),
                         "2026-10-07")

    def test_nsf_items_and_podcast_category(self):
        items = ch.parse_nsf(read("nsf_list.html"), "https://www.nsf.gov/news")
        self.assertEqual(len(items), 3)
        kinds = [i["category"] for i in items]
        self.assertEqual(kinds.count("Podcast"), 1, "팟캐스트를 분류로 구분해야 한다")
        self.assertEqual(kinds.count("NSF News"), 2)

    def test_nsf_date_without_time_tag(self):
        items = ch.parse_nsf(read("nsf_list.html"), "https://www.nsf.gov/news")
        last = items[-1]
        self.assertEqual(ch.nz.parse_published_date(last["published_raw"]),
                         "2026-09-30")

    def test_doe_items_and_type_from_class(self):
        items = ch.parse_doe(read("doe_list.html"), "https://www.energy.gov/newsroom")
        self.assertEqual(len(items), 3)
        self.assertEqual([i["category"] for i in items],
                         ["press-release", "fact-sheet", "article"])

    def test_doe_title_is_not_the_topic_link(self):
        """감싼 상자의 첫 링크는 주제 필터다. 제목으로 쓰면 안 된다."""
        items = ch.parse_doe(read("doe_list.html"), "https://www.energy.gov/newsroom")
        self.assertEqual(items[0]["title"], "Sample Transmission Announcement")
        self.assertNotEqual(items[0]["title"], "Grid")

    def test_doe_missing_date_is_empty_not_guessed(self):
        """날짜 칸이 비면 빈 값으로 둔다. 수집일로 채우지 않는다."""
        items = ch.parse_doe(read("doe_list.html"), "https://www.energy.gov/newsroom")
        self.assertEqual(items[-1]["published_raw"], "")

    def test_govcn_items_and_protocol_relative_url(self):
        items = ch.parse_gov_cn(read("govcn_list.html"),
                                "https://english.www.gov.cn/policies/latestreleases/")
        self.assertEqual(len(items), 2, "메뉴의 li 를 항목으로 세면 안 된다")
        self.assertTrue(items[0]["url"].startswith("https://english.www.gov.cn/policies/"),
                        f"프로토콜 상대 주소를 https 로 바꿔야 한다: {items[0]['url']}")
        self.assertEqual(ch.nz.parse_published_date(items[0]["published_raw"]),
                         "2026-10-09")

    def test_cas_title_excludes_summary(self):
        """링크가 제목과 요약을 함께 감싸고 있어 제목만 뽑아야 한다."""
        items = ch.parse_cas(read("cas_list.html"),
                             "https://english.cas.cn/newsroom/news-updates/")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Sample Prize Awarded to a Researcher")
        for item in items:
            self.assertNotIn("made-up summary", item["title"])

    def test_cas_title_falls_back_to_heading(self):
        """title 속성이 없으면 h4 를 쓴다."""
        items = ch.parse_cas(read("cas_list.html"),
                             "https://english.cas.cn/newsroom/news-updates/")
        self.assertEqual(items[1]["title"], "Sample Research Platform Launched")

    def test_cas_abbreviated_month_date(self):
        """`Oct 04, 2026` 형식을 읽어야 한다."""
        items = ch.parse_cas(read("cas_list.html"),
                             "https://english.cas.cn/newsroom/news-updates/")
        self.assertEqual(ch.nz.parse_published_date(items[0]["published_raw"]),
                         "2026-10-04")
        self.assertEqual(ch.nz.parse_published_date(items[1]["published_raw"]),
                         "2026-09-30")

    def test_every_registered_source_has_a_parser(self):
        for source_id in ("policy-us-001", "policy-us-002", "policy-us-003",
                          "policy-cn-001", "policy-cn-002"):
            self.assertIn(source_id, ch.PARSERS)


@unittest.skipIf(ch is None, MISSING)
class BodyTest(unittest.TestCase):
    """상세 페이지에서 본문만 고르는지."""

    def test_ostp_body(self):
        text = ch.nz.clean_text(ch.pick_body_html(read("ostp_detail.html"),
                                                  "policy-us-001"))
        self.assertIn("quantum testbeds", text)
        self.assertNotIn("Sample footer", text)

    def test_govcn_body_drops_top_menu(self):
        """출처별 선택자가 없으면 상단 메뉴가 본문 앞에 붙어 들어온다."""
        text = ch.nz.clean_text(ch.pick_body_html(read("govcn_detail.html"),
                                                  "policy-cn-001"))
        self.assertIn("BEIJING", text)
        self.assertNotIn("INSTITUTIONS", text)
        self.assertNotIn("Copyright", text)

    def test_cas_body_drops_menu_and_related_items(self):
        text = ch.nz.clean_text(ch.pick_body_html(read("cas_detail.html"),
                                                  "policy-cn-002"))
        self.assertIn("research platform", text)
        self.assertNotIn("About CAS", text)
        self.assertNotIn("Sample Related Item", text)

    def test_unknown_source_falls_back_to_general_rule(self):
        """출처를 모르면 일반 규칙으로 내려간다. 비어서는 안 된다."""
        text = ch.nz.clean_text(ch.pick_body_html(read("ostp_detail.html"), ""))
        self.assertIn("quantum testbeds", text)

    def test_images_are_not_collected(self):
        html = ("<html><body><main><p>" + "본문 " * 80 +
                "</p><img src='photo.jpg' alt='사진'></main></body></html>")
        picked = ch.pick_body_html(html, "")
        self.assertNotIn("photo.jpg", picked,
                         "재배포 조건이 text_only_no_images 다")


@unittest.skipIf(ch is None, MISSING)
class PagingTest(unittest.TestCase):
    """목록 페이징과 멈추는 조건."""

    def test_next_link_from_ostp(self):
        soup = ch.BeautifulSoup(read("ostp_list.html"), "lxml")
        nxt = ch.find_next_link(soup, "https://www.whitehouse.gov/ostp/news/")
        self.assertEqual(nxt, "https://www.whitehouse.gov/ostp/news/?query-54-page=2")

    def test_next_link_handles_protocol_relative(self):
        soup = ch.BeautifulSoup(read("govcn_list.html"), "lxml")
        nxt = ch.find_next_link(soup, "https://english.www.gov.cn/policies/latestreleases/")
        self.assertEqual(
            nxt, "https://english.www.gov.cn/policies/latestreleases/page_2.html")

    def test_next_link_relative_path(self):
        soup = ch.BeautifulSoup(read("cas_list.html"), "lxml")
        nxt = ch.find_next_link(soup, "https://english.cas.cn/newsroom/news-updates/")
        self.assertEqual(nxt,
                         "https://english.cas.cn/newsroom/news-updates/index_1.shtml")

    def test_no_next_link_when_absent(self):
        soup = ch.BeautifulSoup(read("nsf_list.html"), "lxml")
        self.assertIsNone(ch.find_next_link(soup, "https://www.nsf.gov/news"))

    def test_paged_sources_are_the_three_confirmed_ones(self):
        """NSF·DOE 는 목록 HTML로 과거 쪽을 넘길 수 없다(2026-10-17 확인)."""
        self.assertEqual(ch.PAGED_SOURCES,
                         {"policy-us-001", "policy-cn-001", "policy-cn-002"})


@unittest.skipIf(ch is None, MISSING)
class CollectTest(unittest.TestCase):
    """요청을 가로채 수집 흐름 전체를 확인한다. 네트워크를 쓰지 않는다."""

    def setUp(self):
        self.real_fetch = ch.fetch
        self.requested: list[str] = []

    def tearDown(self):
        ch.fetch = self.real_fetch

    def fake_fetch(self, pages: dict, default: str = ""):
        def _fetch(url, delay, timeout=25):
            self.requested.append(url)
            for key, value in pages.items():
                if key in url:
                    return (value, None) if value is not None else (None, "Timeout: 시험")
            return (default, None) if default else (None, "HTTPError: 404")
        return _fetch

    def source(self, source_id: str, name: str, list_url: str,
               redistribution: str = "text_only_no_images") -> dict:
        return {"source_id": source_id, "source_name": name, "list_url": list_url,
                "redistribution": redistribution, "collect_from": WINDOW[0],
                "collect_to": WINDOW[1]}

    def test_rows_follow_documents_csv_schema(self):
        ch.fetch = self.fake_fetch({"/ostp/news/": read("ostp_list.html")},
                                   default=read("ostp_detail.html"))
        docs, log = ch.collect_html(
            self.source("policy-us-001", "백악관 OSTP 뉴스",
                        "https://www.whitehouse.gov/ostp/news/"),
            WINDOW, delay=0, limit=None)
        self.assertEqual(len(docs), 3)
        for row in docs:
            self.assertEqual(sorted(k for k in row if not k.startswith("_")),
                             sorted(ch.nz.DOCUMENT_FIELDS))
            self.assertEqual(row["source_type"], "policy")
        self.assertEqual(ch.nz.validate_documents(docs), [],
                         "rss 경로와 같은 규격이어야 한다")

    def test_out_of_window_item_is_excluded_with_reason(self):
        ch.fetch = self.fake_fetch({"/ostp/news/": read("ostp_list.html")},
                                   default=read("ostp_detail.html"))
        docs, log = ch.collect_html(
            self.source("policy-us-001", "백악관 OSTP 뉴스",
                        "https://www.whitehouse.gov/ostp/news/"),
            WINDOW, delay=0, limit=None)
        excluded = [d for d in docs if d["exclusion_reason"] == "out_of_period"]
        self.assertEqual(len(excluded), 1, "2026-04-02 항목은 기간 밖이다")
        self.assertEqual(log["excluded"].get("out_of_period"), 1)

    def test_body_is_not_requested_for_out_of_window_item(self):
        """기간 밖 문서의 상세 페이지는 받지 않는다. 요청을 아낀다."""
        ch.fetch = self.fake_fetch({"/ostp/news/": read("ostp_list.html")},
                                   default=read("ostp_detail.html"))
        ch.collect_html(self.source("policy-us-001", "백악관 OSTP 뉴스",
                                    "https://www.whitehouse.gov/ostp/news/"),
                        WINDOW, delay=0, limit=None)
        self.assertNotIn("sample-fact-sheet-outside-window",
                         " ".join(self.requested))

    def test_body_failure_is_recorded_without_retry(self):
        pages = {"/ostp/news/": read("ostp_list.html"),
                 "sample-release-on-quantum-testbeds": None}
        ch.fetch = self.fake_fetch(pages, default=read("ostp_detail.html"))
        docs, log = ch.collect_html(
            self.source("policy-us-001", "백악관 OSTP 뉴스",
                        "https://www.whitehouse.gov/ostp/news/"),
            WINDOW, delay=0, limit=None)
        failed = [d for d in docs if d["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["exclusion_reason"], "fetch_error")
        self.assertEqual(len(log["body_failures"]), 1)
        asked = [u for u in self.requested
                 if "sample-release-on-quantum-testbeds" in u]
        self.assertEqual(len(asked), 1, "실패해도 다시 요청하지 않는다")

    def test_list_failure_is_recorded_and_stops(self):
        ch.fetch = self.fake_fetch({"/ostp/news/": None})
        docs, log = ch.collect_html(
            self.source("policy-us-001", "백악관 OSTP 뉴스",
                        "https://www.whitehouse.gov/ostp/news/"),
            WINDOW, delay=0, limit=None)
        self.assertEqual(docs, [])
        self.assertIn("Timeout", log["error"])
        self.assertEqual(len(self.requested), 1, "재시도하지 않는다")

    def test_paging_stops_when_page_is_older_than_window(self):
        # 2쪽은 주소도 날짜도 달라야 한다. 주소가 같으면 "앞 쪽과 같은 항목"
        # 으로 먼저 걸러지므로 기간 조건을 확인할 수 없다
        old = read("govcn_list.html").replace("2026/10/09", "2026/01/09") \
                                     .replace("2026/09/24", "2026/01/02") \
                                     .replace("SAMPLE0001", "SAMPLE0101") \
                                     .replace("SAMPLE0002", "SAMPLE0102")
        ch.fetch = self.fake_fetch({"page_2.html": old,
                                    "latestreleases/": read("govcn_list.html")},
                                   default="<html><body>본문</body></html>")
        docs, log = ch.collect_html(
            self.source("policy-cn-001", "국무원 정책 영문판",
                        "https://english.www.gov.cn/policies/latestreleases/",
                        redistribution="local_only_no_publish"),
            WINDOW, delay=0, limit=None, fetch_bodies=False, max_pages=3)
        self.assertEqual(len(log["pages"]), 2, "3쪽까지 가지 않고 멈춰야 한다")
        self.assertIn("오래됐다", log["pages"][-1]["stop"])

    def test_unpaged_source_reads_one_page_only(self):
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        docs, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=None, fetch_bodies=False, max_pages=5)
        self.assertFalse(log["paging_supported"])
        self.assertEqual(len(log["pages"]), 1)
        self.assertIn("주기적 실행", log["paging_note"])

    def test_categories_are_logged_for_later_selection(self):
        """주제 선별은 기술용어 사전이 한다. 수집 단계는 분류만 남긴다."""
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        docs, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=None, fetch_bodies=False)
        self.assertEqual(log["categories"], {"NSF News": 2, "Podcast": 1})
        self.assertEqual(len(docs), 3, "팟캐스트를 수집 단계에서 버리지 않는다")

    def test_exclude_category_filters_and_records(self):
        """선별은 선택지다. 걸러낸 분류와 건수를 실행 기록에 남긴다."""
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        docs, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=None, fetch_bodies=False,
            exclude_categories={"podcast"})
        self.assertEqual(len(docs), 2)
        self.assertEqual(log["skipped_categories"], {"Podcast": 1})
        self.assertNotIn("Podcast", log["categories"])

    def test_exclude_category_is_case_insensitive(self):
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        _, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=None, fetch_bodies=False,
            exclude_categories={"PODCAST"})
        self.assertEqual(log["skipped_categories"], {"Podcast": 1})

    def test_empty_exclude_option_filters_nothing(self):
        """`--exclude-category` 를 주지 않으면 아무것도 걸러내지 않는다."""
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        docs, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=None, fetch_bodies=False,
            exclude_categories={""})
        self.assertEqual(len(docs), 3)
        self.assertEqual(log["skipped_categories"], {})

    def test_same_item_twice_gives_same_document_id(self):
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        src = self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news")
        first, _ = ch.collect_html(src, WINDOW, delay=0, limit=None,
                                   fetch_bodies=False)
        second, _ = ch.collect_html(src, WINDOW, delay=0, limit=None,
                                    fetch_bodies=False)
        self.assertEqual([d["document_id"] for d in first],
                         [d["document_id"] for d in second])

    def test_limit_caps_processed_items(self):
        ch.fetch = self.fake_fetch({"/news": read("nsf_list.html")})
        docs, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=2, fetch_bodies=False)
        self.assertEqual(len(docs), 2)
        self.assertEqual(log["items_seen"], 2)

    def test_missing_parser_is_reported_not_raised(self):
        docs, log = ch.collect_html(
            self.source("policy-kr-005", "KISTEP 발간물", "https://example.test/"),
            WINDOW, delay=0, limit=None)
        self.assertEqual(docs, [])
        self.assertIn("파서가 없다", log["error"])
        self.assertEqual(self.requested, [], "파서가 없으면 요청하지 않는다")

    def test_broken_list_markup_is_recorded(self):
        """사이트가 개편돼 항목을 못 찾으면 사유를 남긴다."""
        ch.fetch = self.fake_fetch({"/news": "<html><body><p>개편</p></body></html>"})
        docs, log = ch.collect_html(
            self.source("policy-us-002", "NSF News", "https://www.nsf.gov/news"),
            WINDOW, delay=0, limit=None, fetch_bodies=False)
        self.assertEqual(docs, [])
        self.assertIn("구조가 바뀌었을 수 있다", log["pages"][0]["stop"])


@unittest.skipIf(ch is None, MISSING)
class SourceSelectionTest(unittest.TestCase):
    """이용 조건을 확인한 출처만 수집 대상에 넣는지."""

    def test_only_checked_sources_are_collected(self):
        rows = ch.load_sources()
        self.assertTrue(rows, "html_list 정책 출처가 있어야 한다")
        for row in rows:
            self.assertIn(row["access_check_status"], ch.COLLECTABLE_STATUS)
            self.assertEqual(row["access_method"], "html_list")

    def test_kistep_is_not_collected_yet(self):
        """KISTEP 은 이용 조건을 확인했지만(이슈 #31) 수집 경로가 PDF 라 제외된다.

        `access_method` 가 `pdf_list` 여서 이 수집기의 대상이 아니다.
        PDF 수집기를 만들면 그때 대상에 들어온다.
        """
        ids = {r["source_id"] for r in ch.load_sources()}
        self.assertNotIn("policy-kr-005", ids)
        rows = {r["source_id"]: r for r in csv.DictReader(
            (ROOT / "config" / "sources.csv").open(encoding="utf-8"))}
        self.assertEqual(rows["policy-kr-005"]["access_method"], "pdf_list")
        self.assertEqual(rows["policy-kr-005"]["access_check_status"], "terms_ok_local")


if __name__ == "__main__":
    unittest.main()
