#!/usr/bin/env python3
"""공개용 데모 대시보드를 만든다 (GitHub Pages용, `docs/index.html`).

**실제 수집 자료를 쓰지 않는다.** 제목·출처·주소를 모두 이 스크립트가 지어낸다(주소는 `sample.example`).
그래서 재배포 조건이나 저작권 문제가 없다. 키워드 사전(`config/keywords.csv`)과 대시보드 코드는 실제와 같은 것을 쓰므로
화면의 모양과 동작은 실제 도구와 같지만, **숫자는 의미가 없다**(일부러 넣은 추세가 있다).

사용 예:
  python src/build_demo.py                  # docs/index.html 을 만든다
  python src/build_demo.py --out /tmp/d.html --workdir /tmp/demo

같은 입력이면 같은 결과가 나온다(난수 씨앗 고정). 표준 라이브러리만 쓴다.
"""
import argparse
import csv
import datetime as dt
import hashlib
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import extract_keywords as ek  # noqa: E402

SEED = 20261022
FIRST, LAST = dt.date(2026, 9, 1), dt.date(2026, 10, 21)
COLLECTED = "2026-10-21T09:00:00+09:00"
NOTICE = "샘플 데이터로 만든 데모입니다. 제목·출처·숫자는 모두 지어낸 것이며 실제 기사나 정책 문서가 아닙니다. 실제 자료는 각자 PC에서 `python monitor.py`로 수집해 봅니다."

ACTIONS_KO = ["투자 확대 계획 발표", "실증 사업 착수", "가이드라인 마련", "국제 협력 논의", "시범 서비스 시작", "제도 개선 방안 검토", "공청회 개최", "연구개발 지원 공고"]
ACTIONS_EN = ["announces new initiative", "launches pilot program", "releases guidance", "expands international cooperation", "opens funding call", "publishes strategy"]
GENERIC_KO = ["올해의 과학기술인상 시상식 개최", "청소년 과학 캠프 참가자 모집", "연구기관 방문 및 간담회", "국립과학관 특별 전시 개막"]
GENERIC_EN = ["Annual awards ceremony held", "Delegation visits research institute", "Regulations on local administration revised", "Youth science camp opens"]

# 출처: (이름, 유형, 국가, 언어, 재배포 조건, 하루 문서 수 범위)
SOURCES = [
    ("샘플뉴스 A", "news", "KR", "ko", "metadata_only", (3, 6)),
    ("샘플뉴스 B", "news", "KR", "ko", "metadata_only", (2, 5)),
    ("샘플뉴스 C", "news", "KR", "ko", "metadata_only", (1, 4)),
    ("샘플 한국 부처", "policy", "KR", "ko", "kogl_per_item", (0, 2)),
    ("샘플 미국 기관", "policy", "US", "en", "text_only_no_images", (0, 2)),
    ("샘플 중국 기관", "policy", "CN", "en", "local_only_no_publish", (0, 1)),
]
# 키워드 이름 → 유형·국가별 기본 가중치. 뒤의 TRENDS 가 특정 기간에 가중치를 키운다(데모에 변화가 보이도록).
BASE = {
    "news": {"인공지능": 10, "AI 에이전트": 3, "생성형 AI": 4, "클라우드": 3, "사이버보안": 3, "로봇": 2, "반도체": 2, "GPU": 2, "오픈소스": 2, "개인정보": 1.5, "데이터센터": 1.5, "대규모 언어 모델": 1.5},
    "KR": {"인공지능": 6, "반도체": 3, "양자기술": 1, "사이버보안": 2, "디지털 전환": 2, "5G": 1, "개인정보": 1.5, "소버린 AI": 1.5, "데이터센터": 1},
    "US": {"인공지능": 6, "양자기술": 2, "AI 거버넌스": 2, "반도체": 2, "사이버보안": 1.5, "클라우드": 1, "슈퍼컴퓨팅": 1},
    "CN": {"반도체": 4, "인공지능": 3, "양자기술": 1.5, "사이버보안": 1, "5G": 1},
}
# (키워드, 대상, 시작일, 끝일, 배수)
TRENDS = [("AI 에이전트", "news", dt.date(2026, 10, 8), LAST, 4.0), ("양자기술", "KR", dt.date(2026, 10, 5), dt.date(2026, 10, 12), 6.0),
          ("AI 거버넌스", "US", dt.date(2026, 10, 12), LAST, 3.0), ("사이버보안", "news", dt.date(2026, 9, 20), dt.date(2026, 9, 27), 3.0)]


def keyword_terms(dictionary_path):
    """키워드 이름 → (한국어 표기, 영어 표기 또는 None). 사전에 있는 표기만 쓴다."""
    ko, en = {}, {}
    with open(dictionary_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name, term = r["keyword"], r["term"]
            if term == name:
                ko[name] = term
            if term.isascii() and len(term) >= 4 and r["case_sensitive"] == "0" and name not in en:
                en[name] = term
    return {n: (ko[n], en.get(n)) for n in ko}


def weights(kind, day, kinds):
    w = dict(BASE[kind])
    for kw, target, a, b, mult in TRENDS:
        if target == kind and a <= day <= b and kw in w:
            w[kw] *= mult
    return {k: v for k, v in w.items() if k in kinds}


def make_documents(dictionary_path, rng=None):
    rng = rng or random.Random(SEED)
    terms = keyword_terms(dictionary_path)
    rows, n = [], 0
    day = FIRST
    while day <= LAST:
        for name, typ, country, lang, _, (lo, hi) in SOURCES:
            for _ in range(rng.randint(lo, hi)):
                kind = "news" if typ == "news" else country
                pool = weights(kind, day, terms if lang == "ko" else {k for k, (_, e) in terms.items() if e})
                n += 1
                if rng.random() < (0.12 if typ == "news" else 0.35) or not pool:
                    title = rng.choice(GENERIC_KO if lang == "ko" else GENERIC_EN)
                else:
                    picks = rng.choices(list(pool), weights=list(pool.values()), k=2 if rng.random() < 0.3 else 1)
                    picks = list(dict.fromkeys(picks))
                    if lang == "ko":
                        title = "%s 분야 %s" % (" · ".join(terms[p][0] for p in picks), rng.choice(ACTIONS_KO))
                    else:
                        title = "%s %s" % (" and ".join(terms[p][1] for p in picks), rng.choice(ACTIONS_EN))
                url = "https://sample.example/%s/%d" % (name.replace(" ", "-"), n)
                rows.append({"document_id": "demo-%04d" % n, "source_type": typ, "source_name": name, "source_document_id": str(n),
                             "title": title, "original_url": url, "published_date": day.isoformat(), "collected_at": COLLECTED,
                             "analysis_scope": "title", "content_hash": hashlib.sha1(url.encode()).hexdigest()[:12],
                             "status": "analyzed", "exclusion_reason": "", "duplicate_of": "", "text_chars": "0"})
        day += dt.timedelta(days=1)
    return rows


def write_sources(path):
    fields = ["source_id", "source_type", "source_name", "organization", "country", "language", "access_method", "list_url",
              "access_check_status", "access_check_date", "redistribution", "collect_from", "collect_to", "owner", "note"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, (name, typ, country, lang, redis, _) in enumerate(SOURCES, start=1):
            w.writerow({"source_id": "demo-%d" % i, "source_type": typ, "source_name": name, "organization": name, "country": country,
                        "language": lang, "access_method": "sample", "list_url": "https://sample.example/", "access_check_status": "terms_ok",
                        "access_check_date": "2026-10-22", "redistribution": redis, "collect_from": FIRST.isoformat(),
                        "collect_to": LAST.isoformat(), "owner": "demo", "note": "합성 데모"})


def run(args):
    print("   $ python " + " ".join(args))
    return subprocess.run([sys.executable] + args, cwd=ROOT).returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="합성 샘플로 공개용 데모 대시보드를 만든다.")
    ap.add_argument("--out", default=str(ROOT / "docs" / "index.html"), help="출력 HTML (기본 docs/index.html)")
    ap.add_argument("--workdir", default=str(ROOT / "data" / "demo"), help="중간 CSV를 둘 폴더(기본 data/demo, Git 제외)")
    args = ap.parse_args(argv)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    docs = make_documents(ROOT / "config" / "keywords.csv")
    fields = ek.DICTIONARY_COLUMNS and list(docs[0])
    with open(work / "documents.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(docs)
    write_sources(work / "sources.csv")
    print("합성 문서 %d건 (뉴스 %d · 정책 %d)" % (len(docs), sum(d["source_type"] == "news" for d in docs), sum(d["source_type"] == "policy" for d in docs)))
    steps = [
        ["src/extract_keywords.py", "--documents", str(work / "documents.csv"), "--out", str(work / "document_keywords.csv")],
        ["src/aggregate.py", "--documents", str(work / "documents.csv"), "--keywords", str(work / "document_keywords.csv"),
         "--out", str(work / "keyword_stats.csv"), "--as-of", LAST.isoformat()],
        ["dashboard/app.py", "--dir", str(work), "--out", args.out, "--sources", str(work / "sources.csv"),
         "--title", "기술키워드 동향 모니터 (샘플 데모)", "--notice", NOTICE,
         "--range-from", FIRST.isoformat(), "--range-to", LAST.isoformat()],
    ]
    for s in steps:
        if run(s) != 0:
            print("실패: " + s[0], file=sys.stderr)
            return 1
    print("데모 대시보드: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
