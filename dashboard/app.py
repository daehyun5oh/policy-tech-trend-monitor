#!/usr/bin/env python3
"""대시보드 생성 (이슈 #16, #17).

keyword_stats.csv, documents.csv, document_keywords.csv를 읽어 서버 없이 열리는
HTML 파일 하나(dashboard.html)를 만든다. 외부 글꼴·CDN을 쓰지 않는다.
화면에는 문서 빈도·출현 횟수·비율·순위, 기간별 추이, 키워드별 근거 문서가 나온다.

사용 예:
  python dashboard/app.py --dir data/processed --open
  python dashboard/app.py --dir tests/fixtures --stats tests/fixtures/expected/keyword_stats.csv \\
      --keywords tests/fixtures/expected/document_keywords.csv --out dashboard.html

부분 기간 판정: keyword_stats.csv에 is_partial_period 열이 있으면 그 값을 쓴다. 없으면(제안) documents.csv의 collected_at 중 가장 늦은 날짜(수집 종료일)가
기간 안에 있거나 period_end가 그 뒤인 기간을 '부분 기간'으로 표시한다.

종료 코드: 0 정상 / 1 입력 CSV가 규격에 맞지 않음 / 2 파일을 읽을 수 없음
"""
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import webbrowser
from collections import defaultdict
from math import sqrt
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import validate_csv as vc  # noqa: E402
from schema import SOURCE_TYPES, STATUS_ANALYZED, STATUS_EXCLUDED, STATUS_FAILED  # noqa: E402

TEMPLATE = HERE / "template.html"
SOURCES_FILE = HERE.parent / "config" / "sources.csv"
META_FILE = HERE.parent / "config" / "keyword_meta.csv"
CO_LIMIT = 12  # 키워드마다 화면에 실을 함께 나온 키워드 수
# redistribution 값이 이 집합에 있는 출처는 제목·본문을 공개 산출물에 싣지 않는다 (docs/source_policy.md, 이슈 #2).
HIDE_TITLE = {"local_only_no_publish"}
KST = dt.timezone(dt.timedelta(hours=9))
TYPES = [("all", "통합"), ("news", "뉴스"), ("policy", "정책")]
DEFAULT_TITLE = "기술키워드 동향 모니터"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def week_of(d):
    start = d - dt.timedelta(days=d.weekday())
    return start, start + dt.timedelta(days=6)


def month_of(d):
    start = d.replace(day=1)
    return start, vc.month_end(start)


def cell(row, key):
    return (row.get(key) or "").strip()


def rank_key(row):
    rank = cell(row, "rank")
    return (int(rank) if rank.isdigit() else 10 ** 9, cell(row, "keyword"))


def load_hidden_sources(path):
    """sources.csv 에서 제목을 감춰야 하는 출처 이름 집합을 읽는다. 파일이 없으면 빈 집합."""
    try:
        rows = read_csv(path)
    except OSError:
        return frozenset()
    return frozenset(cell(r, "source_name") for r in rows if cell(r, "redistribution") in HIDE_TITLE)


def load_meta(path):
    """keyword_meta.csv 에서 {keyword_id: {"c": 분야, "d": 설명}} 을 읽는다. 파일이 없으면 빈 dict."""
    try:
        rows = read_csv(path)
    except OSError:
        return {}
    return {cell(r, "keyword_id"): {"c": cell(r, "category"), "d": cell(r, "description")}
            for r in rows if cell(r, "keyword_id")}


def load_countries(path):
    """sources.csv 에서 {출처 이름: 국가 코드(KR·US·CN …)}를 읽는다. 파일이 없으면 빈 dict."""
    try:
        rows = read_csv(path)
    except OSError:
        return {}
    return {cell(r, "source_name"): cell(r, "country") for r in rows if cell(r, "source_name") and cell(r, "country")}


def stats_rows_for(members, kids_by_doc, occurrences, names):
    """문서 목록에서 `keyword_stats.csv` 한 묶음과 같은 모양의 행(순위순)을 만든다. 정의는 `aggregate.py`와 같다.

    비율 = 문서 수 ÷ 문서 목록 길이 × 100(소수 둘째 자리). 순위는 문서 수가 같으면 같은 순위이고 다음 순위는 건너뛴다.
    같은 순위 안에서는 키워드 이름순이다.
    """
    total = len(members)
    dc, oc = defaultdict(int), defaultdict(int)
    for doc in members:
        did = cell(doc, "document_id")
        for kid in kids_by_doc.get(did, ()):
            dc[kid] += 1
            oc[kid] += occurrences.get((did, kid), 0)
    ordered = sorted(dc, key=lambda k: (-dc[k], names.get(k, k)))
    rows, prev_rank, prev_dc = [], 0, None
    for i, kid in enumerate(ordered, start=1):
        rank = prev_rank if dc[kid] == prev_dc else i
        prev_rank, prev_dc = rank, dc[kid]
        rows.append({"keyword_id": kid, "keyword": names.get(kid, kid), "document_count": str(dc[kid]),
                     "occurrence_count": str(oc[kid]), "total_documents": str(total),
                     "document_share": "%.2f" % round(dc[kid] / total * 100, 2), "rank": str(rank)})
    return rows


def pct(num, den):
    return "%.2f" % (num / den * 100)


def compute_context(members, kids_by_doc, names, terms_by_doc):
    """한 기간·유형의 분석 문서(members: documents.csv 행 목록)에서 키워드별 맥락 값을 계산한다.

    정의는 docs/context_indicators.md §3 과 같다. 집계(aggregate.py)가 같은 값을 만들면 그 값으로 바꾼다.
    반환: {keyword_id: {"co": [[연관 id, 연관 이름, 함께 나온 문서 수, 비율, 순위]], "src": [[출처, 문서 수, 출처 분석 문서 수, 비율]],
                      "terms": [[표기, 문서 수]], "sc": 출처 수, "tc": 유형 수}}
    """
    by_kid = defaultdict(set)
    for r in members:
        did = cell(r, "document_id")
        for kid in kids_by_doc.get(did, ()):
            by_kid[kid].add(did)
    row_of = {cell(r, "document_id"): r for r in members}
    totals = defaultdict(int)
    for r in members:
        totals[cell(r, "source_name")] += 1
    out = {}
    for kid, docs in by_kid.items():
        co = {rid: len(docs & rdocs) for rid, rdocs in by_kid.items() if rid != kid and docs & rdocs}
        co_rows = []
        for rid, n in sorted(co.items(), key=lambda kv: (-kv[1], names.get(kv[0], kv[0]))):
            co_rows.append([rid, names.get(rid, rid), n, pct(n, len(docs)), 1 + sum(1 for m in co.values() if m > n)])
        per_src = defaultdict(int)
        per_term = defaultdict(set)
        for did in docs:
            per_src[cell(row_of[did], "source_name")] += 1
            for term in terms_by_doc.get((did, kid), ()):
                per_term[term].add(did)
        out[kid] = {
            "co": co_rows[:CO_LIMIT],
            "src": [[s, n, totals[s], pct(n, totals[s])] for s, n in sorted(per_src.items(), key=lambda kv: (-kv[1], kv[0]))],
            "terms": [[t, len(d)] for t, d in sorted(per_term.items(), key=lambda kv: (-len(kv[1]), kv[0]))],
            "sc": len(per_src),
            "tc": len({cell(row_of[d], "source_type") for d in docs}),
        }
    return out


# ---- 인사이트 화면(docs/insight_views.md) 계산 ----------------------------------------
BASELINE_K = {"daily": 7, "weekly": 4, "monthly": 3, "range": 1}  # 기준 기간 개수(이전의 분석 문서가 있는 기간)
MIN_CUR_DOCS = 5        # 이번 기간 문서가 이보다 적으면 비교하지 않는다
MIN_BASE_DOCS = 20      # 기준 기간 문서 합이 이보다 적으면 비교하지 않는다
MIN_COUNT = 2           # 비중이 커진 키워드로 올리려면 이번 기간 문서가 이 이상
MIN_TYPE_DOCS = 5       # 뉴스·정책 비교는 두 유형 모두 이 이상일 때만
MIN_CAT_CUR = 5         # 분야 안 비교: 이번 기간 분야 문서
MIN_CAT_BASE = 15       # 분야 안 비교: 기준 기간 분야 문서 합
BACKGROUND_SHARE = 0.4  # 전체 기간 문서의 이 비율 이상에 나오면 「상시」
BACKGROUND_MIN_DOCS = 20
MIX_WARN_PP = 15.0      # 출처별 문서 비중이 이만큼(%p) 달라지면 출처 구성 경고
TOP_N = 10
COUNTRY_LABELS = {"KR": "한국", "US": "미국", "CN": "중국"}
COUNTRY_TOP = 15        # 국가별 보기에서 나라마다 싣는 키워드 수
DOC_LIST_MAX = 150      # 정책 문서 목록에 싣는 최대 문서 수

SCOPE_ORDER = ("title", "title_summary", "title_body")


def scope_by_source(members):
    """출처별로 분석 범위(제목만, 제목+요약, 제목+본문)가 몇 건씩인지 센다.
    반환: [[출처, 제목만, 제목+요약, 제목+본문]] (출처 이름순)."""
    counts = defaultdict(lambda: dict.fromkeys(SCOPE_ORDER, 0))
    for r in members:
        scope = cell(r, "analysis_scope")
        if scope in SCOPE_ORDER:
            counts[cell(r, "source_name")][scope] += 1
    return [[name] + [c[s] for s in SCOPE_ORDER] for name, c in sorted(counts.items())]


def compare_counts(n1, N1, n0, N0):
    """두 비율(n1/N1, n0/N0)의 차이와 표준화 점수(두 비율 차이 검정 통계량)를 돌려준다.

    p = (n1+n0)/(N1+N0) 일 때 z = (p1-p0) / sqrt(p(1-p)(1/N1+1/N0)). p가 0 또는 1이면 z는 None.
    비중·차이는 %로, 소수 둘째 자리까지. 우연한 변동에 견준 차이일 뿐 유의성이나 중요도의 주장이 아니다."""
    p1, p0 = n1 / N1, n0 / N0
    pool = (n1 + n0) / (N1 + N0)
    se = sqrt(pool * (1 - pool) * (1 / N1 + 1 / N0)) if 0 < pool < 1 else 0
    z = (p1 - p0) / se if se else None
    return {"p1": round(p1 * 100, 2), "p0": round(p0 * 100, 2), "d": round((p1 - p0) * 100, 2),
            "z": None if z is None else round(z, 2)}


def category_counts(members, kids_by_doc, kid_cat):
    """분야 안 비교용: {분야: {"N": 그 분야 키워드를 하나라도 가진 문서 수, "k": {keyword_id: 문서 수}}}."""
    out = {}
    for r in members:
        kids = kids_by_doc.get(cell(r, "document_id"), ())
        for cat in {kid_cat[k] for k in kids if k in kid_cat}:
            entry = out.setdefault(cat, {"N": 0, "k": defaultdict(int)})
            entry["N"] += 1
            for k in kids:
                if kid_cat.get(k) == cat:
                    entry["k"][k] += 1
    return {c: {"N": e["N"], "k": dict(e["k"])} for c, e in out.items()}


def _prior_nonempty(views, pt, tid, starts, idx, k):
    out = []
    for j in range(idx - 1, -1, -1):
        if views["%s|%s|%s" % (pt, starts[j], tid)]["analyzed"] > 0:
            out.append(starts[j])
            if len(out) == k:
                break
    return list(reversed(out))


def _source_mix(views, pt, tid, starts):
    mix = defaultdict(int)
    for s in starts:
        for row in views["%s|%s|%s" % (pt, s, tid)].get("scope", []):
            mix[row[0]] += sum(row[1:])
    return mix


def build_changes(views, periods, names):
    """기간마다 기준 기간과 비교한 키워드 비중 변화(`chg`)와 분야 안 비중(`catv`)을 채운다 (docs/insight_views.md §3, §5, §7)."""
    for pt, plist in periods.items():
        starts = sorted(p["start"] for p in plist)
        for tid, _ in TYPES:
            for idx, start in enumerate(starts):
                v = views["%s|%s|%s" % (pt, start, tid)]
                if v["analyzed"] == 0:
                    continue
                base = _prior_nonempty(views, pt, tid, starts, idx, BASELINE_K[pt])
                bviews = [views["%s|%s|%s" % (pt, s, tid)] for s in base]
                N1, N0 = v["analyzed"], sum(b["analyzed"] for b in bviews)
                info = {"N1": N1, "N0": N0, "k": len(base), "base": [base[0], base[-1]] if base else []}
                if N1 < MIN_CUR_DOCS:
                    v["chg"] = dict(info, ok=False, why="cur")
                elif N0 < MIN_BASE_DOCS:
                    v["chg"] = dict(info, ok=False, why="base")
                else:
                    cur = {s["id"]: s["dc"] for s in v["stats"]}
                    n0s = defaultdict(int)
                    label = {s["id"]: s["kw"] for s in v["stats"]}
                    for b in bviews:
                        for s in b["stats"]:
                            n0s[s["id"]] += s["dc"]
                            label.setdefault(s["id"], s["kw"])
                    rows = []
                    for kid in set(cur) | set(n0s):
                        n1, n0 = cur.get(kid, 0), n0s.get(kid, 0)
                        row = {"id": kid, "kw": label[kid], "n1": n1, "n0": n0}
                        row.update(compare_counts(n1, N1, n0, N0))
                        rows.append(row)
                    key = lambda r: (r["z"] is None, -(r["z"] or 0), r["kw"])  # noqa: E731
                    up = sorted((r for r in rows if r["n1"] >= MIN_COUNT and r["d"] > 0 and r["z"] is not None), key=key)[:TOP_N]
                    down = sorted((r for r in rows if r["d"] < 0 and r["z"] is not None and r["n0"] / N0 * N1 >= MIN_COUNT),
                                  key=lambda r: (r["z"], r["kw"]))[:TOP_N]
                    cur_mix, base_mix = _source_mix(views, pt, tid, [start]), _source_mix(views, pt, tid, base)
                    warn = []
                    for src in sorted(set(cur_mix) | set(base_mix)):
                        a = cur_mix.get(src, 0) / (sum(cur_mix.values()) or 1) * 100
                        b = base_mix.get(src, 0) / (sum(base_mix.values()) or 1) * 100
                        if abs(a - b) >= MIX_WARN_PP:
                            warn.append({"src": src, "cur": round(a, 1), "base": round(b, 1)})
                    v["chg"] = dict(info, ok=True, up=up, down=down, mix=warn)
                # 분야 안 비중 (기준 기간과의 비교는 분야 문서가 충분할 때만)
                catv = {}
                for cat, cur in v.get("cat", {}).items():
                    Nc0 = sum(b.get("cat", {}).get(cat, {}).get("N", 0) for b in bviews)
                    can = cur["N"] >= MIN_CAT_CUR and Nc0 >= MIN_CAT_BASE
                    rows = []
                    for kid in sorted(cur["k"]):
                        n1 = cur["k"][kid]
                        row = {"id": kid, "kw": names.get(kid, kid), "n1": n1}
                        if can:
                            n0 = sum(b.get("cat", {}).get(cat, {}).get("k", {}).get(kid, 0) for b in bviews)
                            row.update(n0=n0, **compare_counts(n1, cur["N"], n0, Nc0))
                        else:
                            row.update(p1=round(n1 / cur["N"] * 100, 2))
                        rows.append(row)
                    rows.sort(key=lambda r: (-r["n1"], r["kw"]))
                    catv[cat] = {"N": cur["N"], "N0": Nc0, "cmp": can, "rows": rows}
                v["catv"] = catv


def build_gaps(views, periods):
    """같은 기간의 뉴스·정책 문서 비중을 견준 `gap`을 통합 화면(`all`)에 붙인다 (docs/insight_views.md §4)."""
    for pt, plist in periods.items():
        for p in plist:
            nv, pv = views["%s|%s|news" % (pt, p["start"])], views["%s|%s|policy" % (pt, p["start"])]
            Nn, Np = nv["analyzed"], pv["analyzed"]
            gap = {"Nn": Nn, "Np": Np, "ok": Nn >= MIN_TYPE_DOCS and Np >= MIN_TYPE_DOCS}
            if gap["ok"]:
                cn, cp = {s["id"]: s["dc"] for s in nv["stats"]}, {s["id"]: s["dc"] for s in pv["stats"]}
                label = {s["id"]: s["kw"] for s in nv["stats"] + pv["stats"]}
                rows = []
                for kid in set(cn) | set(cp):
                    a, b = cn.get(kid, 0), cp.get(kid, 0)
                    c = compare_counts(a, Nn, b, Np)
                    rows.append({"id": kid, "kw": label[kid], "nn": a, "np": b, "pn": c["p1"], "pp": c["p0"], "d": c["d"], "z": c["z"],
                                 "tag": "only_news" if b == 0 and a >= MIN_COUNT else ("only_policy" if a == 0 and b >= MIN_COUNT else "")})
                gap["news"] = sorted((r for r in rows if r["d"] > 0 and r["nn"] >= MIN_COUNT), key=lambda r: (-r["d"], r["kw"]))[:TOP_N]
                gap["policy"] = sorted((r for r in rows if r["d"] < 0 and r["np"] >= MIN_COUNT), key=lambda r: (r["d"], r["kw"]))[:TOP_N]
            views["%s|%s|all" % (pt, p["start"])]["gap"] = gap


def background_keywords(analyzed, kids_by_doc):
    """자료 유형마다 전체 기간 문서의 BACKGROUND_SHARE 이상에 나오는 키워드(상시 키워드)의 keyword_id 목록."""
    out = {}
    for tid, _ in TYPES:
        docs = [r for _, r in analyzed if tid == "all" or cell(r, "source_type") == tid]
        if len(docs) < BACKGROUND_MIN_DOCS:
            out[tid] = []
            continue
        counts = defaultdict(int)
        for r in docs:
            for kid in kids_by_doc.get(cell(r, "document_id"), ()):
                counts[kid] += 1
        out[tid] = sorted(k for k, n in counts.items() if n / len(docs) >= BACKGROUND_SHARE)
    return out


def apply_streaks(views, periods):
    """연속 기간 수(streak)를 채운다. 분석 문서가 없는 기간은 건너뛰고(끊지 않음),
    문서는 있었는데 그 키워드가 없으면 끊는다 (docs/context_indicators.md §3-4)."""
    for pt, plist in periods.items():
        for tid, _ in TYPES:
            run = {}
            for p in sorted(plist, key=lambda x: x["start"]):
                v = views.get("%s|%s|%s" % (pt, p["start"], tid))
                if not v or v["analyzed"] == 0:
                    continue
                nxt = {kid: run.get(kid, 0) + 1 for kid in v["ctx"]}
                for kid, c in v["ctx"].items():
                    c["st"] = nxt[kid]
                run = nxt


def date_range(first, last):
    """first~last(포함) 사이의 모든 날짜를 YYYY-MM-DD 문자열로."""
    out, d = [], dt.date.fromisoformat(first)
    end = dt.date.fromisoformat(last)
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def build_model(documents, keyword_rows, stats_rows, title=DEFAULT_TITLE, generated_at=None, hidden_sources=frozenset(), meta=None,
                countries=None, range_period=None):
    """CSV 행 목록으로 화면에 필요한 모든 값을 미리 계산한 dict를 만든다.

    `range_period=(시작일, 종료일)`을 주면 그 조회 기간 전체를 하나의 기간(`range`)으로 더한다(없으면 분석한 문서의 첫날~마지막 날).
    `range` 기간의 순위·건수는 집계 CSV에 없어 같은 정의(`stats_rows_for`)로 대시보드가 문서 목록에서 센다.
    `countries`({출처 이름: 국가 코드})를 주면 정책 화면에 국가별 키워드 집계와 문서 목록을 싣는다.
    """
    analyzed, excluded = [], []
    failed = {t: 0 for t in SOURCE_TYPES}
    undated = {t: 0 for t in SOURCE_TYPES}
    collected = []
    for r in documents:
        status, stype = cell(r, "status"), cell(r, "source_type")
        day = vc.parse_date(cell(r, "published_date"))
        if vc.parse_aware_datetime(cell(r, "collected_at")):
            collected.append(cell(r, "collected_at")[:10])
        if status == STATUS_FAILED and stype in failed:
            failed[stype] += 1
        elif status == STATUS_EXCLUDED and day is None and stype in undated:
            undated[stype] += 1
        if day is None:
            continue
        if status == STATUS_ANALYZED:
            analyzed.append((day, r))
        elif status == STATUS_EXCLUDED:
            excluded.append((day, r))
    analyzed.sort(key=lambda x: (-x[0].toordinal(), cell(x[1], "document_id")))

    spans = {"daily": set(), "weekly": set(), "monthly": set(), "range": set()}
    for r in stats_rows:
        if cell(r, "period_type") in spans:
            spans[cell(r, "period_type")].add((cell(r, "period_start"), cell(r, "period_end")))
    for day, _ in analyzed:
        spans["weekly"].add(tuple(x.isoformat() for x in week_of(day)))
        spans["monthly"].add(tuple(x.isoformat() for x in month_of(day)))
    latest = max(collected) if collected else ""
    if analyzed:
        first_day, last_day = range_period or (min(d for d, _ in analyzed).isoformat(), max(d for d, _ in analyzed).isoformat())
        spans["range"] = {(first_day, last_day)}
    # 일간은 집계 CSV에 daily 행이 있을 때만 쓴다(없으면 빈 결과로 오해하게 되므로 숨긴다).
    # 문서가 없는 날도 '분석한 문서가 없음'으로 보이도록 첫 날부터 마지막 수집일까지 빈틈없이 만든다.
    if spans["daily"]:
        days = {s for s, _ in spans["daily"]} | {d.isoformat() for d, _ in analyzed}
        last = max(days | ({latest} if latest else set()))
        spans["daily"] = {(x, x) for x in date_range(min(days), last)}
    else:
        spans["daily"] = set()
    # 집계 CSV에 is_partial_period 열이 있으면 그 값을 쓰고, 없으면 수집 종료일로 판정한다.
    flagged = {(cell(r, "period_type"), cell(r, "period_start")): cell(r, "is_partial_period") == "yes"
               for r in stats_rows if cell(r, "is_partial_period")}

    def is_partial(pt, s, e):
        if pt == "range":
            return False  # 조회 기간 전체는 사용자가 정한 기간이라 부분 기간으로 보지 않는다
        if (pt, s) in flagged:
            return flagged[(pt, s)]
        # 수집 종료일이 기간 안에 있거나 기간이 그 뒤까지 이어지면 진행 중인 기간이다 (data_dictionary.md §6).
        return bool(latest) and e >= latest

    periods = {pt: [{"start": s, "end": e, "partial": is_partial(pt, s, e)} for s, e in sorted(v)]
               for pt, v in spans.items()}

    by_group = defaultdict(list)
    for r in stats_rows:
        by_group[(cell(r, "period_type"), cell(r, "period_start"), cell(r, "source_type"))].append(r)
    kids_by_doc = defaultdict(set)
    names, terms_by_doc, occurrences = {}, {}, {}
    for r in keyword_rows:
        kids_by_doc[cell(r, "document_id")].add(cell(r, "keyword_id"))
        names[cell(r, "keyword_id")] = cell(r, "keyword")
        occurrences[(cell(r, "document_id"), cell(r, "keyword_id"))] = vc.to_int(cell(r, "occurrences")) or 0
        try:
            terms = json.loads(cell(r, "matched_terms") or "[]")
        except ValueError:
            terms = []
        terms_by_doc[(cell(r, "document_id"), cell(r, "keyword_id"))] = [t for t in terms if isinstance(t, str)]

    for rs_, re_ in spans["range"]:
        for tid, _ in TYPES:
            group = [m[1] for m in analyzed if rs_ <= m[0].isoformat() <= re_ and (tid == "all" or cell(m[1], "source_type") == tid)]
            by_group[("range", rs_, tid)] = stats_rows_for(group, kids_by_doc, occurrences, names)

    countries = countries or {}
    kid_cat = {k: m["c"] for k, m in (meta or {}).items() if m.get("c")}
    doc_list, doc_index = [], {}

    def index_of(row):
        did = cell(row, "document_id")
        if did not in doc_index:
            doc_index[did] = len(doc_list)
            doc_list.append({"t": cell(row, "title"), "d": cell(row, "published_date"),
                             "s": cell(row, "source_name"), "g": countries.get(cell(row, "source_name"), ""),
                             "k": sorted(names.get(k, k) for k in kids_by_doc.get(did, ())), "u": cell(row, "original_url") if vc.is_http_url(cell(row, "original_url")) else "",
                             "y": cell(row, "source_type")})
            if cell(row, "source_name") in hidden_sources:
                doc_list[-1]["t"] = ""  # 제목은 모델(HTML)에 싣지 않는다
                doc_list[-1]["h"] = 1
        return doc_index[did]

    views = {}
    for pt, plist in periods.items():
        for p in plist:
            start, end = p["start"], p["end"]
            for tid, _ in TYPES:
                def inside(item):
                    return start <= item[0].isoformat() <= end and (tid == "all" or cell(item[1], "source_type") == tid)
                members = [m for m in analyzed if inside(m)]
                rows = sorted(by_group.get((pt, start, tid), []), key=rank_key)
                stats = [{"id": cell(r, "keyword_id"), "kw": cell(r, "keyword"),
                          "dc": vc.to_int(cell(r, "document_count")) or 0, "oc": vc.to_int(cell(r, "occurrence_count")) or 0,
                          "share": cell(r, "document_share"), "rank": cell(r, "rank")} for r in rows]
                wanted = {s["id"] for s in stats}
                evidence = defaultdict(list)
                for _, doc in members:
                    for kid in sorted(kids_by_doc.get(cell(doc, "document_id"), frozenset()) & wanted):
                        evidence[kid].append(index_of(doc))
                extra = {}
                if tid == "policy":
                    # 국가별 키워드 집계와 문서 목록(정책 화면 전용). 국가는 sources.csv 의 country 로 정한다.
                    by_country = defaultdict(list)
                    for _, doc in members:
                        by_country[countries.get(cell(doc, "source_name"), "")].append(doc)
                    ctry = {}
                    for code in sorted(by_country, key=lambda c: (list(COUNTRY_LABELS).index(c) if c in COUNTRY_LABELS else len(COUNTRY_LABELS), c)):
                        crows = stats_rows_for(by_country[code], kids_by_doc, occurrences, names)
                        ctry[code or "?"] = {"label": COUNTRY_LABELS.get(code, code or "미상"), "N": len(by_country[code]),
                                             "stats": [{"id": r["keyword_id"], "kw": r["keyword"], "dc": int(r["document_count"]),
                                                        "share": r["document_share"], "rank": r["rank"]} for r in crows[:COUNTRY_TOP]]}
                    extra = {"ctry": ctry, "dl": [index_of(doc) for _, doc in members[:DOC_LIST_MAX]]}
                views["%s|%s|%s" % (pt, start, tid)] = dict(extra, **{
                    "analyzed": len(members),
                    "excluded": sum(1 for m in excluded if inside(m)),
                    "csvTotal": (vc.to_int(cell(rows[0], "total_documents")) if rows else None),
                    "stats": stats,
                    "ev": dict(evidence),
                    "ctx": compute_context([m[1] for m in members], kids_by_doc, names, terms_by_doc),
                    "scope": scope_by_source([m[1] for m in members]),
                    "cat": category_counts([m[1] for m in members], kids_by_doc, kid_cat),
                })
    apply_streaks(views, periods)
    build_changes(views, periods, names)
    build_gaps(views, periods)
    coverage = {}
    for tid in SOURCE_TYPES:
        days = [d.isoformat() for d, r in analyzed if cell(r, "source_type") == tid]
        if days:
            coverage[tid] = [min(days), max(days)]
    common = None
    if len(coverage) == len(SOURCE_TYPES):
        lo, hi = max(c[0] for c in coverage.values()), min(c[1] for c in coverage.values())
        common = [lo, hi] if lo <= hi else None
    return {
        "title": title,
        "generatedAt": generated_at or dt.datetime.now(KST).isoformat(timespec="seconds"),
        "latestCollected": latest,
        "types": [{"id": i, "label": l} for i, l in TYPES],
        "periods": periods,
        "views": views,
        "docs": doc_list,
        "overall": {"failed": failed, "excludedUndated": undated},
        "coverage": coverage,
        "commonRange": common,
        "meta": meta or {},
        "background": background_keywords(analyzed, kids_by_doc),
        "categories": sorted(set(kid_cat.values())),
        "countryLabels": COUNTRY_LABELS,
        "sources": {cell(r, "source_name"): {"y": cell(r, "source_type"), "g": countries.get(cell(r, "source_name"), "")}
                    for _, r in analyzed},
    }


def embed_json(model):
    """<script> 안에 넣어도 태그가 끝나거나 열리지 않도록 <, >, &를 유니코드 이스케이프한다."""
    text = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    for ch, esc in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(ch, esc)
    return text


def render_html(model, template_text=None):
    template_text = template_text if template_text is not None else TEMPLATE.read_text(encoding="utf-8")
    values = {"TITLE": html.escape(model["title"]), "DATA": embed_json(model)}
    # 한 번에 치환해, 데이터 안에 __DATA__ 같은 문자열이 있어도 다시 치환되지 않게 한다.
    return re.sub(r"__(TITLE|DATA)__", lambda m: values[m.group(1)], template_text)


def main(argv=None):
    p = argparse.ArgumentParser(description="CSV로 대시보드 HTML 파일을 만든다.")
    p.add_argument("--dir", default="data/processed", help="CSV 3종이 있는 폴더 (기본 data/processed)")
    p.add_argument("--documents")
    p.add_argument("--keywords")
    p.add_argument("--stats")
    p.add_argument("--out", help="출력 HTML (기본 <dir>/dashboard.html)")
    p.add_argument("--title", default=DEFAULT_TITLE)
    p.add_argument("--sources", default=str(SOURCES_FILE), help="출처 목록(redistribution 으로 제목 공개 여부 결정). 기본 config/sources.csv")
    p.add_argument("--meta", default=str(META_FILE), help="키워드 설명·분야 (기본 config/keyword_meta.csv). 없으면 설명 없이 표시")
    p.add_argument("--no-validate", action="store_true", help="입력 CSV 규격 검사를 건너뜀")
    p.add_argument("--open", action="store_true", help="만든 뒤 기본 브라우저로 연다")
    p.add_argument("--range-from", help="조회 기간 시작일 YYYY-MM-DD (기본: 분석한 문서의 첫 날)")
    p.add_argument("--range-to", help="조회 기간 종료일 YYYY-MM-DD (기본: 분석한 문서의 마지막 날)")
    args = p.parse_args(argv)
    base = Path(args.dir)
    documents = args.documents or str(base / "documents.csv")
    keywords = args.keywords or str(base / "document_keywords.csv")
    stats = args.stats or str(base / "keyword_stats.csv")
    out = Path(args.out) if args.out else base / "dashboard.html"
    try:
        if not args.no_validate:
            report = vc.run(documents, keywords, stats)
            if report.errors:
                for name, row, message in report.errors[:20]:
                    print("오류 %s %d행: %s" % (name, row, message), file=sys.stderr)
                print("입력 CSV에 오류가 %d건 있어 대시보드를 만들지 않았습니다. (--no-validate로 건너뛸 수 있음)" % len(report.errors), file=sys.stderr)
                return 1
        model = build_model(read_csv(documents), read_csv(keywords), read_csv(stats), title=args.title,
                            hidden_sources=load_hidden_sources(args.sources), meta=load_meta(args.meta),
                            countries=load_countries(args.sources),
                            range_period=(args.range_from, args.range_to) if args.range_from and args.range_to else None)
    except OSError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(model), encoding="utf-8")
    print("대시보드 생성: %s (기간 %d개, 화면 %d개)" % (out, sum(len(v) for v in model["periods"].values()), len(model["views"])))
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
