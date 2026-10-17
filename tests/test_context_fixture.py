"""맥락·확산 지표 정답 fixture의 내부 일관성 검사 (docs/context_indicators.md §3, §4).

tests/fixtures/context/expected/ 의 파일은 문서별 키워드를 보고 손으로 계산해 적은 값이다.
이 테스트는 documents.csv와 document_keywords.csv에서 같은 값을 독립적으로 다시 계산해
손으로 적은 숫자에 실수가 없는지 확인한다. 아직 aggregate.py가 이 지표를 만들지 않으므로
구현이 생기면 같은 expected 파일과 정확히 일치해야 한다.
"""
import csv
import datetime as dt
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import validate_csv as vc  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "context"
TYPES = ("news", "policy", "all")


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def week(day):
    start = day - dt.timedelta(days=day.weekday())
    return start.isoformat(), (start + dt.timedelta(days=6)).isoformat()


def pct(num, den):
    return "%.2f" % (num / den * 100)


class Recomputed:
    def __init__(self):
        self.docs = {r["document_id"]: r for r in read(FIX / "documents.csv") if r["status"] == "analyzed"}
        self.kw = defaultdict(dict)  # 문서 -> {keyword_id: (keyword, 표기 목록)}
        for r in read(FIX / "document_keywords.csv"):
            if r["document_id"] in self.docs:
                self.kw[r["document_id"]][r["keyword_id"]] = (r["keyword"], json.loads(r["matched_terms"]))
        self.period_of = {i: week(dt.date.fromisoformat(d["published_date"])) for i, d in self.docs.items()}
        self.periods = sorted(set(self.period_of.values()))

    def members(self, period, stype):
        return [i for i, d in self.docs.items()
                if self.period_of[i] == period and (stype == "all" or d["source_type"] == stype)]

    def keyword_docs(self, ids):
        out = defaultdict(set)
        for i in ids:
            for kid in self.kw[i]:
                out[kid].add(i)
        return out

    def names(self):
        return {kid: v[0] for per in self.kw.values() for kid, v in per.items()}


def keyed(rows, keys, values):
    out = {}
    for r in rows:
        key = tuple(r[k] for k in keys)
        assert key not in out, "기대값 파일에 같은 행이 두 번 있음: %s" % (key,)
        out[key] = tuple(r[v] for v in values)
    return out


BASE = ("period_type", "period_start", "period_end", "source_type")


class ContextFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Recomputed()
        cls.names = cls.r.names()

    def test_fixture_csvs_pass_the_validator(self):
        report = vc.run(str(FIX / "documents.csv"), str(FIX / "document_keywords.csv"))
        self.assertEqual(report.errors, [])

    def test_context_matches(self):
        got = {}
        for p in self.r.periods:
            for t in TYPES:
                kd = self.r.keyword_docs(self.r.members(p, t))
                for kid, docs in kd.items():
                    co = {rid: len(docs & rdocs) for rid, rdocs in kd.items() if rid != kid and docs & rdocs}
                    for rid, n in co.items():
                        rank = 1 + sum(1 for m in co.values() if m > n)
                        got[("weekly", p[0], p[1], t, kid, rid)] = (
                            self.names[kid], self.names[rid], str(n), pct(n, len(docs)), str(rank))
        want = keyed(read(FIX / "expected" / "keyword_context.csv"), BASE + ("keyword_id", "related_keyword_id"),
                     ("keyword", "related_keyword", "co_document_count", "co_share", "rank"))
        self.assertEqual(got, want)

    def test_sources_match(self):
        got = {}
        for p in self.r.periods:
            for t in TYPES:
                ids = self.r.members(p, t)
                totals = defaultdict(int)
                for i in ids:
                    totals[self.r.docs[i]["source_name"]] += 1
                for kid, docs in self.r.keyword_docs(ids).items():
                    per = defaultdict(int)
                    for i in docs:
                        per[self.r.docs[i]["source_name"]] += 1
                    for src, n in per.items():
                        got[("weekly", p[0], p[1], t, kid, src)] = (
                            self.names[kid], str(n), str(totals[src]), pct(n, totals[src]))
        want = keyed(read(FIX / "expected" / "keyword_sources.csv"), BASE + ("keyword_id", "source_name"),
                     ("keyword", "document_count", "source_total_documents", "source_share"))
        self.assertEqual(got, want)

    def test_terms_match(self):
        got = {}
        for p in self.r.periods:
            for t in TYPES:
                ids = self.r.members(p, t)
                per = defaultdict(set)
                for i in ids:
                    for kid, (_, terms) in self.r.kw[i].items():
                        for term in terms:
                            per[(kid, term)].add(i)
                for (kid, term), docs in per.items():
                    got[("weekly", p[0], p[1], t, kid, term)] = (self.names[kid], str(len(docs)))
        want = keyed(read(FIX / "expected" / "keyword_terms.csv"), BASE + ("keyword_id", "term"),
                     ("keyword", "document_count"))
        self.assertEqual(got, want)

    def test_stats_extra_columns_match(self):
        got = {}
        for t in TYPES:
            streak = defaultdict(int)  # 키워드 -> 직전까지의 연속 기간 수
            for p in self.r.periods:
                ids = self.r.members(p, t)
                if not ids:
                    continue  # 분석 문서가 없는 기간은 건너뛴다(끊지 않는다)
                kd = self.r.keyword_docs(ids)
                for kid in list(streak):
                    if kid not in kd:
                        streak[kid] = 0  # 문서는 있었는데 이 키워드가 없으면 끊긴다
                for kid, docs in kd.items():
                    streak[kid] += 1
                    sources = {self.r.docs[i]["source_name"] for i in docs}
                    types = {self.r.docs[i]["source_type"] for i in docs}
                    got[("weekly", p[0], p[1], t, kid)] = (
                        self.names[kid], str(len(docs)), str(len(sources)), str(len(types)), str(streak[kid]))
        want = keyed(read(FIX / "expected" / "keyword_stats_extra.csv"), BASE + ("keyword_id",),
                     ("keyword", "document_count", "source_count", "type_count", "streak"))
        self.assertEqual(got, want)

    def test_the_gap_week_is_skipped_not_counted_as_absence(self):
        # 10-12~10-18 주에는 분석 문서가 하나도 없다. 10-19 주의 인공지능은 연속 4번째다.
        self.assertNotIn(("2026-10-12", "2026-10-18"), self.r.periods)
        rows = read(FIX / "expected" / "keyword_stats_extra.csv")
        last = [r for r in rows if r["period_start"] == "2026-10-19" and r["source_type"] == "news" and r["keyword_id"] == "K001"]
        self.assertEqual([r["streak"] for r in last], ["4"])


if __name__ == "__main__":
    unittest.main()
