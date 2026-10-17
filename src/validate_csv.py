#!/usr/bin/env python3
"""공통 CSV 스키마 검증 (이슈 #15, 완료기준 C2).

검사 항목
  documents.csv        분석 완료 문서의 필수 근거값(제목·원문 URL·발행일·내부 식별자) 누락,
                       문서 ID 중복, 값 형식, 중복 제외 문서의 대표 문서 연결
  document_keywords.csv 문서 연결, (문서, 키워드) 중복, 출현 횟수, matched_terms(JSON 배열)
  keyword_stats.csv    기간 경계, 비율·순위 계산 일관성, 통합(all)과 뉴스·정책의 관계

사용 예:
  python src/validate_csv.py --dir data/processed
  python src/validate_csv.py --documents a.csv --keywords b.csv --stats c.csv

종료 코드: 0 오류 없음 / 1 오류 있음 / 2 사용 방법 오류
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import (PARTIAL_VALUES, DOCUMENT_COLUMNS, KEYWORD_COLUMNS, NOT_APPLICABLE, PERIOD_TYPES, SCOPES,  # noqa: E402
                    SOURCE_TYPES, STATS_COLUMNS, STATS_SOURCE_TYPES, STATUS_ANALYZED,
                    STATUS_EXCLUDED, STATUSES)

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SHARE_TOLERANCE = 0.01  # 기획안 C4: 비율은 반올림 오차 범위 안에서 비교


def parse_date(value):
    if not DATE_RE.fullmatch(value or ""):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def parse_aware_datetime(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def is_http_url(value):
    u = urlparse(value or "")
    return u.scheme in ("http", "https") and bool(u.netloc)


def to_int(value):
    return int(value) if re.fullmatch(r"\d+", value or "") else None


def month_end(d):
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


class Report:
    def __init__(self):
        self.errors = []   # (파일, 행, 메시지)
        self.summary = []

    def add(self, name, row, message):
        self.errors.append((name, row, message))


def read_rows(path, required, name, report):
    """(rows, ok). 필수 열이 없으면 오류를 기록하고 ok=False."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in required if c not in header]
        if missing:
            report.add(name, 1, "필수 열이 없음: " + ", ".join(missing))
            return [], False
        return list(reader), True


def validate_documents(path, report):
    name = "documents.csv"
    rows, ok = read_rows(path, DOCUMENT_COLUMNS, name, report)
    if not ok:
        return {}
    by_id, first_line = {}, {}
    for n, r in enumerate(rows, start=2):
        v = lambda k: (r.get(k) or "").strip()  # noqa: E731
        did, status = v("document_id"), v("status")
        if not did:
            report.add(name, n, "document_id가 비어 있음")
        elif did in first_line:
            report.add(name, n, "document_id 중복: %s (첫 등장 %d행)" % (did, first_line[did]))
        else:
            first_line[did] = n
            by_id[did] = r
        if v("source_type") not in SOURCE_TYPES:
            report.add(name, n, "source_type은 %s 중 하나여야 함 (값: %r)" % ("/".join(SOURCE_TYPES), v("source_type")))
        if status not in STATUSES:
            report.add(name, n, "status는 %s 중 하나여야 함 (값: %r)" % ("/".join(STATUSES), status))
        scope = v("analysis_scope")
        if scope and scope not in SCOPES:
            report.add(name, n, "analysis_scope는 %s 중 하나여야 함 (값: %r)" % ("/".join(SCOPES), scope))
        if v("published_date") and parse_date(v("published_date")) is None:
            report.add(name, n, "published_date는 유효한 YYYY-MM-DD여야 함 (값: %r)" % v("published_date"))
        if v("collected_at") and parse_aware_datetime(v("collected_at")) is None:
            report.add(name, n, "collected_at은 시간대가 포함된 ISO 8601이어야 함 (값: %r)" % v("collected_at"))
        if v("original_url") and not is_http_url(v("original_url")):
            report.add(name, n, "original_url은 http(s) 주소여야 함 (값: %r)" % v("original_url"))
        if status == STATUS_ANALYZED:
            for col in ("title", "original_url", "published_date", "analysis_scope"):
                if not v(col):
                    report.add(name, n, "분석 완료 문서의 필수값 누락: %s (%s)" % (col, did or "document_id 없음"))
            if v("exclusion_reason"):
                report.add(name, n, "분석 완료 문서에 exclusion_reason이 있음")
        if status == STATUS_EXCLUDED and not v("exclusion_reason"):
            report.add(name, n, "제외 문서에 exclusion_reason이 없음 (%s)" % did)
        if v("duplicate_of") and status != STATUS_EXCLUDED:
            report.add(name, n, "duplicate_of는 제외(excluded) 문서에만 있어야 함 (%s)" % did)
    for did, r in by_id.items():
        target = (r.get("duplicate_of") or "").strip()
        if not target:
            continue
        n = first_line[did]
        if target == did:
            report.add(name, n, "duplicate_of가 자기 자신임 (%s)" % did)
        elif target not in by_id:
            report.add(name, n, "duplicate_of의 대표 문서가 없음: %s -> %s" % (did, target))
        elif (by_id[target].get("status") or "").strip() != STATUS_ANALYZED:
            report.add(name, n, "대표 문서가 분석 완료 상태가 아님: %s -> %s" % (did, target))
    report.summary.append("%s %d행" % (name, len(rows)))
    return by_id


def validate_keywords(path, docs, report):
    name = "document_keywords.csv"
    rows, ok = read_rows(path, KEYWORD_COLUMNS, name, report)
    if not ok:
        return set()
    seen, names, versions = {}, {}, set()
    for n, r in enumerate(rows, start=2):
        v = lambda k: (r.get(k) or "").strip()  # noqa: E731
        did, kid = v("document_id"), v("keyword_id")
        if docs is not None:
            doc = docs.get(did)
            if doc is None:
                report.add(name, n, "documents.csv에 없는 document_id: %s" % did)
            elif (doc.get("status") or "").strip() != STATUS_ANALYZED:
                report.add(name, n, "분석 완료가 아닌 문서에 키워드가 연결됨: %s" % did)
        if not kid or not v("keyword"):
            report.add(name, n, "keyword_id와 keyword는 비어 있을 수 없음")
        if (did, kid) in seen:
            report.add(name, n, "(document_id, keyword_id) 중복: (%s, %s) (첫 등장 %d행)" % (did, kid, seen[(did, kid)]))
        seen.setdefault((did, kid), n)
        if names.setdefault(kid, v("keyword")) != v("keyword"):
            report.add(name, n, "keyword_id %s에 서로 다른 keyword가 있음" % kid)
        occ = to_int(v("occurrences"))
        if occ is None or occ < 1:
            report.add(name, n, "occurrences는 1 이상의 정수여야 함 (값: %r)" % v("occurrences"))
        try:
            terms = json.loads(v("matched_terms"))
        except ValueError:
            terms = None
        if not (isinstance(terms, list) and terms and all(isinstance(t, str) and t for t in terms)):
            report.add(name, n, "matched_terms는 비어 있지 않은 문자열의 JSON 배열이어야 함 (값: %r)" % v("matched_terms"))
        elif occ is not None and len(terms) > occ:
            report.add(name, n, "matched_terms 개수(%d)가 occurrences(%d)보다 많음" % (len(terms), occ))
        if not v("dictionary_version"):
            report.add(name, n, "dictionary_version이 비어 있음")
        versions.add(v("dictionary_version"))
    if len(versions - {""}) > 1:
        report.add(name, 1, "dictionary_version이 한 파일에 여러 개 섞여 있음: %s" % sorted(versions))
    report.summary.append("%s %d행" % (name, len(rows)))
    return set(names)


def validate_stats(path, keyword_ids, report):
    name = "keyword_stats.csv"
    rows, ok = read_rows(path, STATS_COLUMNS, name, report)
    if not ok:
        return
    keys, groups = {}, defaultdict(list)
    for n, r in enumerate(rows, start=2):
        v = lambda k: (r.get(k) or "").strip()  # noqa: E731
        pt, st = v("period_type"), v("source_type")
        start, end = parse_date(v("period_start")), parse_date(v("period_end"))
        if pt not in PERIOD_TYPES:
            report.add(name, n, "period_type은 %s 중 하나여야 함 (값: %r)" % ("/".join(PERIOD_TYPES), pt))
        if "is_partial_period" in r and v("is_partial_period") not in PARTIAL_VALUES:
            report.add(name, n, "is_partial_period는 %s 중 하나여야 함 (값: %r)" % ("/".join(PARTIAL_VALUES), v("is_partial_period")))
        if st not in STATS_SOURCE_TYPES:
            report.add(name, n, "source_type은 %s 중 하나여야 함 (값: %r)" % ("/".join(STATS_SOURCE_TYPES), st))
        if start is None or end is None:
            report.add(name, n, "period_start/period_end는 유효한 YYYY-MM-DD여야 함")
        elif pt == "daily" and start != end:
            report.add(name, n, "일간은 시작일과 종료일이 같아야 함 (%s ~ %s)" % (start, end))
        elif pt == "weekly" and not (start.weekday() == 0 and end == start + dt.timedelta(days=6)):
            report.add(name, n, "주간은 월요일~일요일이어야 함 (%s ~ %s)" % (start, end))
        elif pt == "monthly" and not (start.day == 1 and end == month_end(start)):
            report.add(name, n, "월간은 1일~말일이어야 함 (%s ~ %s)" % (start, end))
        kid = v("keyword_id")
        if not kid or not v("keyword"):
            report.add(name, n, "keyword_id와 keyword는 비어 있을 수 없음")
        elif keyword_ids is not None and kid not in keyword_ids:
            report.add(name, n, "document_keywords.csv에 없는 keyword_id: %s" % kid)
        key = (pt, v("period_start"), v("period_end"), st, kid)
        if key in keys:
            report.add(name, n, "같은 기간·유형·키워드 행이 중복됨 (첫 등장 %d행)" % keys[key])
        keys.setdefault(key, n)
        dc, oc, total = to_int(v("document_count")), to_int(v("occurrence_count")), to_int(v("total_documents"))
        if None in (dc, oc, total):
            report.add(name, n, "document_count, occurrence_count, total_documents는 0 이상의 정수여야 함")
            continue
        if oc < dc:
            report.add(name, n, "occurrence_count(%d)가 document_count(%d)보다 작음" % (oc, dc))
        if dc > total:
            report.add(name, n, "document_count(%d)가 total_documents(%d)보다 큼" % (dc, total))
        share, rank = v("document_share"), v("rank")
        if total == 0:
            if share != NOT_APPLICABLE or rank != NOT_APPLICABLE:
                report.add(name, n, "total_documents가 0이면 비율·순위는 '%s'이어야 함" % NOT_APPLICABLE)
        else:
            try:
                if abs(float(share) - dc / total * 100) > SHARE_TOLERANCE:
                    report.add(name, n, "document_share(%s)가 %d/%d*100과 다름" % (share, dc, total))
            except ValueError:
                report.add(name, n, "document_share는 숫자여야 함 (값: %r)" % share)
            if to_int(rank) is None or int(rank) < 1:
                report.add(name, n, "rank는 1 이상의 정수여야 함 (값: %r)" % rank)
        groups[(pt, v("period_start"), v("period_end"), st)].append((n, kid, dc, oc, total, rank))

    for gkey, members in groups.items():
        totals = {m[4] for m in members}
        if len(totals) > 1:
            report.add(name, members[0][0], "같은 기간·유형 안에서 total_documents가 다름: %s %s" % (gkey, sorted(totals)))
        for n, kid, dc, _, total, rank in members:
            if total == 0 or to_int(rank) is None:
                continue
            expected = 1 + sum(1 for m in members if m[2] > dc)  # 동률은 공동 순위
            if int(rank) != expected:
                report.add(name, n, "rank(%s)가 문서 빈도 순위(공동 순위 %d)와 다름: %s" % (rank, expected, kid))

    by_period = defaultdict(dict)
    for (pt, s, e, st, kid), n in keys.items():
        by_period[(pt, s, e)][st] = True
    for (pt, s, e), kinds_present in by_period.items():
        if {"all", "news", "policy"} <= set(kinds_present):
            t = {st: next(iter({m[4] for m in groups[(pt, s, e, st)]}), 0) for st in ("all", "news", "policy")}
            if t["all"] != t["news"] + t["policy"]:
                report.add(name, 1, "통합 total_documents(%d) != 뉴스(%d) + 정책(%d): %s %s~%s" % (t["all"], t["news"], t["policy"], pt, s, e))
        if "all" in kinds_present:
            dcs = defaultdict(dict)
            for st in ("all", "news", "policy"):
                for _, kid, dc, *_ in groups.get((pt, s, e, st), []):
                    dcs[kid][st] = dc
            for kid, d in dcs.items():
                a, nw, po = d.get("all"), d.get("news", 0), d.get("policy", 0)
                if a is not None and not (max(nw, po) <= a <= nw + po):
                    report.add(name, 1, "통합 문서 빈도(%d)가 뉴스(%d)·정책(%d)로 설명되지 않음: %s %s~%s %s" % (a, nw, po, pt, s, e, kid))
    report.summary.append("%s %d행" % (name, len(rows)))


def period_warnings(documents=None, sources=None):
    """뉴스와 정책이 같은 기간을 다루는지 확인한다 (기획안 §3, §9: 두 유형을 같은 기준으로 비교).
    오류가 아니라 경고 목록을 돌려준다.

    - 출처 목록: 수집 가능(terms_ok 계열) 출처의 collect_from/collect_to 가 뉴스와 정책에서 같아야 한다.
    - 문서 목록: 분석 완료 문서의 발행일 범위가 뉴스와 정책에서 겹쳐야 하고, 범위가 다르면 공통 범위를 알려 준다.
    """
    out = []
    if sources:
        windows = defaultdict(set)
        with open(sources, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("access_check_status") or "").startswith("terms_ok") and r.get("collect_from") and r.get("collect_to"):
                    windows[r["source_type"]].add((r["collect_from"], r["collect_to"]))
        if len(windows) == len(SOURCE_TYPES) and len({w for ws in windows.values() for w in ws}) > 1:
            detail = "; ".join("%s %s" % (t, ", ".join("%s~%s" % w for w in sorted(ws))) for t, ws in sorted(windows.items()))
            out.append("수집 기간이 출처마다 다름 (%s). 뉴스와 정책은 같은 기간으로 맞춰야 비교할 수 있음 (docs/period_policy.md)" % detail)
    if documents:
        ranges = defaultdict(list)
        with open(documents, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                day = parse_date((r.get("published_date") or "").strip())
                if (r.get("status") or "").strip() == STATUS_ANALYZED and day is not None:
                    ranges[(r.get("source_type") or "").strip()].append(day)
        if len(ranges) == len(SOURCE_TYPES):
            span = {t: (min(d), max(d)) for t, d in ranges.items()}
            lo, hi = max(s[0] for s in span.values()), min(s[1] for s in span.values())
            text = ", ".join("%s %s~%s" % (t, s[0], s[1]) for t, s in sorted(span.items()))
            if lo > hi:
                out.append("뉴스와 정책의 분석 문서 발행일 범위가 겹치지 않음 (%s)" % text)
            elif any(s != (lo, hi) for s in span.values()):
                out.append("뉴스와 정책의 분석 문서 발행일 범위가 다름 (%s). 비교는 공통 범위 %s~%s에서만 해석할 것" % (text, lo, hi))
    return out


def run(documents=None, keywords=None, stats=None):
    report = Report()
    docs = validate_documents(documents, report) if documents else None
    keyword_ids = validate_keywords(keywords, docs, report) if keywords else None
    if stats:
        validate_stats(stats, keyword_ids, report)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="공통 CSV가 기획안 §6 규격을 지키는지 검사한다.")
    p.add_argument("--dir", help="documents.csv, document_keywords.csv, keyword_stats.csv가 있는 폴더")
    p.add_argument("--documents")
    p.add_argument("--keywords")
    p.add_argument("--stats")
    p.add_argument("--max-errors", type=int, default=50, help="화면에 표시할 오류 수 (기본 50)")
    p.add_argument("--sources", help="출처 목록(config/sources.csv). 주면 뉴스·정책 수집 기간이 같은지 경고함")
    p.add_argument("--strict-period", action="store_true", help="기간 경고가 있으면 종료 코드 1로 끝냄")
    args = p.parse_args(argv)
    documents, keywords, stats = args.documents, args.keywords, args.stats
    if args.dir:
        d = Path(args.dir)
        documents = documents or str(d / "documents.csv")
        keywords = keywords or str(d / "document_keywords.csv")
        stats = stats or str(d / "keyword_stats.csv")
    if not (documents or keywords or stats):
        p.print_usage(sys.stderr)
        print("검사할 파일을 --dir 또는 --documents/--keywords/--stats로 지정하세요.", file=sys.stderr)
        return 2
    try:
        report = run(documents, keywords, stats)
    except OSError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2
    print("검사 대상: " + ", ".join(report.summary))
    for name, row, message in report.errors[:args.max_errors]:
        print("오류 %s %d행: %s" % (name, row, message))
    if len(report.errors) > args.max_errors:
        print("... 외 %d건 (--max-errors로 더 볼 수 있음)" % (len(report.errors) - args.max_errors))
    print("검증 결과: 오류 %d건" % len(report.errors))
    warnings = []
    if documents or args.sources:
        try:
            warnings = period_warnings(documents if documents and Path(documents).exists() else None, args.sources)
        except OSError as e:
            print("오류: %s" % e, file=sys.stderr)
            return 2
    for w in warnings:
        print("경고 기간: %s" % w)
    if report.errors or (warnings and args.strict_period):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
