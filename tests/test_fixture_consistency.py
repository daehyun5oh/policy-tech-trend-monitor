"""fixture 기대값의 내부 일관성 검사 (이슈 #14).

tests/fixtures/expected/keyword_stats.csv는 사람이 직접 계산해 적은 값이다.
이 테스트는 documents.csv와 expected/document_keywords.csv에서 같은 값을 다시 계산해,
손으로 적은 숫자에 산수 실수가 없는지 확인한다. (B의 aggregate.py 구현과는 무관한 별도 계산이다.)
"""
import csv
import datetime as dt
import unittest
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def week(d):
    start = d - dt.timedelta(days=d.weekday())
    return start, start + dt.timedelta(days=6)


def month(d):
    start = d.replace(day=1)
    return start, (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


class FixtureConsistencyTest(unittest.TestCase):
    def test_expected_stats_match_independent_recomputation(self):
        docs = {r["document_id"]: r for r in read(FIX / "documents.csv") if r["status"] == "analyzed"}
        keywords = read(FIX / "expected" / "document_keywords.csv")
        periods = set()
        for d in docs.values():
            day = dt.date.fromisoformat(d["published_date"])
            periods.add(("weekly",) + week(day))
            periods.add(("monthly",) + month(day))

        recomputed = {}
        for ptype, start, end in periods:
            for stype in ("news", "policy", "all"):
                ids = {i for i, d in docs.items()
                       if start <= dt.date.fromisoformat(d["published_date"]) <= end
                       and (stype == "all" or d["source_type"] == stype)}
                agg = {}
                for r in keywords:
                    if r["document_id"] in ids:
                        entry = agg.setdefault((r["keyword_id"], r["keyword"]), [set(), 0])
                        entry[0].add(r["document_id"])
                        entry[1] += int(r["occurrences"])
                counts = [len(v[0]) for v in agg.values()]
                for (kid, name), (dset, occ) in agg.items():
                    dc = len(dset)
                    rank = 1 + sum(1 for c in counts if c > dc)  # 동률은 공동 순위
                    recomputed[(ptype, start.isoformat(), end.isoformat(), stype, kid)] = (
                        name, str(dc), str(occ), str(len(ids)), "%.2f" % (dc / len(ids) * 100), str(rank))

        expected = {}
        for r in read(FIX / "expected" / "keyword_stats.csv"):
            key = (r["period_type"], r["period_start"], r["period_end"], r["source_type"], r["keyword_id"])
            self.assertNotIn(key, expected, "기대값 파일에 같은 행이 두 번 있음: %s" % (key,))
            expected[key] = (r["keyword"], r["document_count"], r["occurrence_count"],
                             r["total_documents"], r["document_share"], r["rank"])
        self.assertEqual(recomputed, expected)


class SyntheticMarkTest(unittest.TestCase):
    def test_fixture_folder_declares_synthetic_data(self):
        text = (FIX / "README.md").read_text(encoding="utf-8")
        self.assertIn("가상 데이터", text)
        for row in read(FIX / "documents.csv"):
            self.assertTrue(row["original_url"].startswith("https://example.test/"), row["document_id"])


if __name__ == "__main__":
    unittest.main()
