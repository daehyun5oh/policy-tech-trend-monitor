"""조회 기간 전체(range) 보기와 정책 국가별 집계의 계산 검사. 기대값은 손으로 계산했다."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "src"))
import app  # noqa: E402


def doc(i, typ, day, source, title="제목"):
    return {"document_id": i, "source_type": typ, "source_name": source, "title": title, "published_date": day,
            "collected_at": "2026-10-15T09:00:00+09:00", "original_url": "https://example.org/" + i, "status": "analyzed",
            "analysis_scope": "title", "exclusion_reason": ""}


def kw(doc_id, kid, name, occ):
    return {"document_id": doc_id, "keyword_id": kid, "keyword": name, "matched_terms": "[]", "occurrences": str(occ),
            "dictionary_version": "v1"}


DOCS = [doc("d1", "policy", "2026-10-12", "한국부처", "가"), doc("d2", "policy", "2026-10-13", "미국기관", "나"),
        doc("d3", "policy", "2026-10-14", "미국기관", "다"), doc("d4", "news", "2026-10-14", "뉴스사", "라"),
        doc("d5", "policy", "2026-10-14", "중국기관", "비공개 제목")]
KWS = [kw("d1", "K1", "인공지능", 2), kw("d1", "K2", "반도체", 1), kw("d2", "K1", "인공지능", 1),
       kw("d3", "K1", "인공지능", 3), kw("d3", "K2", "반도체", 1), kw("d4", "K1", "인공지능", 1)]
COUNTRIES = {"한국부처": "KR", "미국기관": "US", "중국기관": "CN", "뉴스사": "KR"}


def model(**kw_):
    return app.build_model(DOCS, KWS, [], hidden_sources=frozenset({"중국기관"}), countries=COUNTRIES, **kw_)


class RangeViewTest(unittest.TestCase):
    def test_range_defaults_to_the_first_to_last_analyzed_day_and_is_not_partial(self):
        m = model()
        self.assertEqual(m["periods"]["range"], [{"start": "2026-10-12", "end": "2026-10-14", "partial": False}])

    def test_range_can_be_given_explicitly(self):
        m = model(range_period=("2026-10-10", "2026-10-20"))
        self.assertEqual((m["periods"]["range"][0]["start"], m["periods"]["range"][0]["end"]), ("2026-10-10", "2026-10-20"))

    def test_counts_shares_and_ranks_for_all_types_hand_calculated(self):
        # 통합 5건: 인공지능 d1·d2·d3·d4 = 4건(출현 2+1+3+1=7, 4/5=80.00%), 반도체 d1·d3 = 2건(출현 2, 40.00%)
        v = model()["views"]["range|2026-10-12|all"]
        self.assertEqual(v["analyzed"], 5)
        self.assertEqual([(s["kw"], s["dc"], s["oc"], s["share"], s["rank"]) for s in v["stats"]],
                         [("인공지능", 4, 7, "80.00", "1"), ("반도체", 2, 2, "40.00", "2")])
        self.assertEqual(v["csvTotal"], 5)

    def test_news_and_policy_views_are_separate(self):
        m = model()
        self.assertEqual(m["views"]["range|2026-10-12|news"]["analyzed"], 1)
        pol = m["views"]["range|2026-10-12|policy"]
        # 정책 4건(d1, d2, d3, d5): 인공지능 d1·d2·d3 = 3건 75.00%, 반도체 d1·d3 = 2건 50.00%
        self.assertEqual((pol["analyzed"], [(s["kw"], s["dc"], s["share"]) for s in pol["stats"]]),
                         (4, [("인공지능", 3, "75.00"), ("반도체", 2, "50.00")]))

    def test_tied_keywords_share_a_rank_and_the_next_one_skips(self):
        names = {"A": "가", "B": "나", "C": "다"}
        members = [doc("x1", "policy", "2026-10-12", "s"), doc("x2", "policy", "2026-10-12", "s")]
        kids = {"x1": {"A", "B", "C"}, "x2": {"A", "B"}}
        rows = app.stats_rows_for(members, kids, {}, names)
        # 가·나는 2건으로 공동 1위, 다는 1건이라 3위(2위를 건너뜀)
        self.assertEqual([(r["keyword"], r["rank"]) for r in rows], [("가", "1"), ("나", "1"), ("다", "3")])

    def test_range_equals_the_aggregate_for_a_period_that_covers_everything(self):
        # 집계(aggregate.py)가 만든 월간 값과 같은 정의인지: 같은 문서로 월간 행을 손으로 적어 둔다
        stats = [{"period_type": "monthly", "period_start": "2026-10-01", "period_end": "2026-10-31", "source_type": "all",
                  "keyword_id": "K1", "keyword": "인공지능", "document_count": "4", "occurrence_count": "7",
                  "total_documents": "5", "document_share": "80.00", "rank": "1", "is_partial_period": "no"},
                 {"period_type": "monthly", "period_start": "2026-10-01", "period_end": "2026-10-31", "source_type": "all",
                  "keyword_id": "K2", "keyword": "반도체", "document_count": "2", "occurrence_count": "2",
                  "total_documents": "5", "document_share": "40.00", "rank": "2", "is_partial_period": "no"}]
        m = app.build_model(DOCS, KWS, stats, hidden_sources=frozenset(), countries=COUNTRIES)
        monthly = m["views"]["monthly|2026-10-01|all"]["stats"]
        ranged = m["views"]["range|2026-10-12|all"]["stats"]
        self.assertEqual(monthly, ranged)


class CountryAndDocListTest(unittest.TestCase):
    def test_policy_keywords_are_counted_per_country_hand_calculated(self):
        c = model()["views"]["range|2026-10-12|policy"]["ctry"]
        self.assertEqual(list(c), ["KR", "US", "CN"])                                  # 한국·미국·중국 순
        self.assertEqual((c["KR"]["label"], c["KR"]["N"]), ("한국", 1))
        # 한국 1건(d1): 인공지능·반도체 모두 1건 100.00%, 같은 순위라 이름순(반도체, 인공지능)
        self.assertEqual([(s["kw"], s["dc"], s["share"], s["rank"]) for s in c["KR"]["stats"]],
                         [("반도체", 1, "100.00", "1"), ("인공지능", 1, "100.00", "1")])
        # 미국 2건(d2, d3): 인공지능 2건 100.00%, 반도체 1건 50.00%
        self.assertEqual([(s["kw"], s["dc"], s["share"], s["rank"]) for s in c["US"]["stats"]],
                         [("인공지능", 2, "100.00", "1"), ("반도체", 1, "50.00", "2")])
        # 중국 1건(d5)은 키워드가 없다
        self.assertEqual((c["CN"]["N"], c["CN"]["stats"]), (1, []))

    def test_country_data_is_only_on_policy_views(self):
        m = model()
        self.assertNotIn("ctry", m["views"]["range|2026-10-12|news"])
        self.assertNotIn("ctry", m["views"]["range|2026-10-12|all"])

    def test_document_list_is_newest_first_with_keywords_and_hides_restricted_titles(self):
        m = model()
        v = m["views"]["range|2026-10-12|policy"]
        # 날짜 최신순, 같은 날은 문서 ID순: d3(10/14), d5(10/14), d2(10/13), d1(10/12)
        docs = [m["docs"][i] for i in v["dl"]]
        self.assertEqual([d["t"] for d in docs], ["다", "", "나", "가"])
        self.assertEqual(docs[0]["k"], ["반도체", "인공지능"])                       # d3 의 키워드
        self.assertEqual((docs[0]["g"], docs[1]["g"]), ("US", "CN"))
        self.assertEqual(docs[1].get("h"), 1)                                          # 중국 출처 제목은 모델에 싣지 않음

    def test_without_countries_everything_still_builds(self):
        m = app.build_model(DOCS, KWS, [], hidden_sources=frozenset())
        self.assertEqual(list(m["views"]["range|2026-10-12|policy"]["ctry"]), ["?"])


if __name__ == "__main__":
    unittest.main()
