"""목록 페이지 수집기 (이슈 #37, 담당 B)

`access_method` 가 `html_list` 인 출처를 수집한다. 미국 3곳과 중국 2곳이며,
사이트마다 HTML 구조가 달라 출처별 파서를 둔다.

정제·식별자·중복 제거는 `src/normalize.py` 를 쓴다 (이슈 #9). 이 파일은
"목록에서 제목·주소·발행일을 어떻게 꺼내는가" 만 담당한다.

주제 선별은 하지 않는다. 기획안 §4 가 기술용어 사전 기반 추출로 걸러내도록
정했기 때문이다. 대신 각 항목의 분류(`category`)를 실행 기록에 남겨 나중에
선별할 수 있게 한다.

허용된 출처만 수집한다. `access_check_status` 가 `terms_ok` 또는
`terms_ok_local` 인 행만 쓴다 (기획안 §11).

사용법
    python src/collect_html.py --dry-run
    python src/collect_html.py --source-id policy-us-002 --limit 3
    python src/collect_html.py --no-bodies
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

import normalize as nz  # noqa: E402

SOURCES_CSV = ROOT / "config" / "sources.csv"
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
DOCUMENTS_CSV = PROCESSED_DIR / "documents.csv"

KST = timezone(timedelta(hours=9))
COLLECTABLE_STATUS = {"terms_ok", "terms_ok_local"}
USER_AGENT = "policy-tech-trend-monitor/0.1 (research prototype)"


# ──────────────────────────── 공통 도우미 ────────────────────────────

def text_of(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def first_date(text: str) -> str:
    """문자열에서 날짜로 보이는 첫 조각을 꺼낸다. normalize 가 형식을 맞춘다."""
    patterns = (
        r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}",
        r"[A-Z][a-z]{2,8} \d{1,2},? 20\d{2}",
        r"\d{1,2} [A-Z][a-z]{2,8} 20\d{2}",
    )
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0)
    return ""


def item(title: str, url: str, published_raw: str, category: str = "") -> dict:
    return {"title": title.strip(), "url": url,
            "published_raw": published_raw.strip(), "category": category.strip()}


# ──────────────────────────── 출처별 파서 ────────────────────────────
# 선택자는 2026-10-17 에 실제 페이지를 받아 확인했다. 사이트 개편 시
# 이 함수만 고치면 된다.

def parse_ostp(html: str, base_url: str) -> list[dict]:
    """백악관 OSTP 뉴스. WordPress 블록 구조."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for post in soup.select("li.wp-block-post"):
        link = post.select_one("h2.wp-block-post-title a[href]")
        if not link:
            continue
        # 분류 줄은 "Releases October 9, 2026" 처럼 날짜가 붙어 온다
        meta = text_of(post.select_one(".wp-block-whitehouse-post-template__meta"))
        time_el = post.select_one("time[datetime]")
        # time 태그가 없는 항목도 있어, 그때는 분류 줄에 적힌 날짜를 읽는다.
        # 수집일로 대체하지 않는다 (기획안 §3)
        published = (time_el.get("datetime") if time_el else "") or first_date(meta)
        category = re.sub(r"\s*[A-Z][a-z]+ \d{1,2},? 20\d{2}.*$", "", meta).strip()
        out.append(item(text_of(link), urljoin(base_url, link["href"]),
                        published, category))
    return out


def parse_nsf(html: str, base_url: str) -> list[dict]:
    """NSF News. 팟캐스트도 같은 목록에 섞여 있어 분류를 기록한다."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for teaser in soup.select("div.latest-news-teaser"):
        link = teaser.select_one(".latest-news-teaser__headline h3 a[href]")
        if not link:
            continue
        time_el = teaser.select_one(".latest-news-teaser__date time[datetime]")
        published = (time_el.get("datetime") if time_el else "") or \
            first_date(text_of(teaser.select_one(".latest-news-teaser__date")))
        category = text_of(teaser.select_one(".latest-news-teaser__type"))
        out.append(item(text_of(link), urljoin(base_url, link["href"]),
                        published, category))
    return out


DOE_TYPE_RE = re.compile(r"collection-item--type-([a-z-]+)")


def parse_doe(html: str, base_url: str) -> list[dict]:
    """DOE Newsroom. 보도자료·기사·팩트시트가 섞여 있다."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for wrapper in soup.select("div.collection-item-wrapper"):
        link = wrapper.select_one(".collection-item__title a[href]")
        if not link:
            continue
        published = first_date(text_of(wrapper.select_one(".collection-item__date")))
        classes = " ".join(wrapper.get("class") or [])
        m = DOE_TYPE_RE.search(classes)
        category = m.group(1) if m else ""
        out.append(item(text_of(link), urljoin(base_url, link["href"]),
                        published, category))
    return out


def parse_gov_cn(html: str, base_url: str) -> list[dict]:
    """중국 국무원 정책 영문판. li 안에 h3(제목·링크)과 h4(날짜)."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.find_all("li"):
        link = li.select_one("h3 a[href]")
        if not link:
            continue
        published = first_date(text_of(li.select_one("h4")))
        if not published:
            continue
        href = link["href"]
        if href.startswith("//"):
            href = "https:" + href
        out.append(item(text_of(link), urljoin(base_url, href), published, "policy"))
    return out


def parse_cas(html: str, base_url: str) -> list[dict]:
    """중국과학원 뉴스 영문판.

    li 하나가 링크 하나로 감싸여 있고, 그 안에 날짜(`.up-times`), 제목(`h4`),
    요약(`.up-des`)이 들어 있다. `a` 전체의 글자를 제목으로 쓰면 요약까지
    따라오므로 `title` 속성이나 `h4` 를 쓴다.
    """
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("ul.up-list li"):
        link = li.find("a", href=True)
        if not link or ".shtml" not in link["href"]:
            continue
        title = (link.get("title") or text_of(li.select_one("h4"))).strip()
        published = first_date(text_of(li.select_one(".up-times")))
        out.append(item(title, urljoin(base_url, link["href"]), published, "news"))
    return out


PARSERS = {
    "policy-us-001": parse_ostp,
    "policy-us-002": parse_nsf,
    "policy-us-003": parse_doe,
    "policy-cn-001": parse_gov_cn,
    "policy-cn-002": parse_cas,
}


# ──────────────────────────── 페이징 ────────────────────────────
# 2026-10-17 확인: 다섯 곳 중 세 곳만 HTML로 다음 쪽을 넘길 수 있다.
#
#   OSTP     `?query-54-page=2`        가능
#   국무원   `page_2.html`             가능
#   CAS      `index_1.shtml`           가능
#   NSF      목록에 최신 9건만, `?page=N` 은 같은 결과       불가
#   DOE      뉴스룸에 최신 18건만, 검색 목록은 자료를 JS로 불러온다  불가
#
# NSF·DOE 는 한 번에 과거를 거슬러 받을 수 없다. 주기적으로 실행해 쌓는
# 방식으로 기간을 채운다. 실행 기록에 `paging_supported: false` 로 남긴다.

PAGED_SOURCES = {"policy-us-001", "policy-cn-001", "policy-cn-002"}

NEXT_TEXT_RE = re.compile(r"^(next|next page|next →|next>|>|›|»|다음)$", re.I)


def find_next_link(soup: BeautifulSoup, base_url: str) -> str | None:
    """목록 페이지가 스스로 제시하는 '다음 쪽' 주소를 따라간다.

    쪽 번호 규칙을 코드에 적어두면 사이트가 번호 체계를 바꿀 때 조용히
    틀린 주소를 받는다. 페이지가 알려주는 링크를 쓰는 편이 안전하다.
    """
    rel_next = soup.find("a", rel="next", href=True)
    candidates = [rel_next] if rel_next else []
    candidates += [a for a in soup.find_all("a", href=True)
                   if NEXT_TEXT_RE.match(text_of(a))]
    for a in candidates:
        href = a["href"]
        if href.startswith("//"):
            href = "https:" + href
        resolved = urljoin(base_url, href)
        if resolved.rstrip("/") != base_url.rstrip("/"):
            return resolved
    return None


# ──────────────────────────── 본문 ────────────────────────────

BODY_SELECTORS = ("article", "main", "div[role=main]", "#content", "body")

# 출처마다 본문만 담은 영역이 따로 있다. 2026-10-17 에 상세 페이지를 받아
# 확인했다. 이것을 쓰지 않으면 중국 두 곳은 본문 앞에 상단 메뉴가 그대로
# 섞여 들어온다 (`App HOME NEWS INSTITUTIONS ...`).
SOURCE_BODY_SELECTORS = {
    "policy-us-001": ("div.entry-content",),
    "policy-cn-001": ("div.Artical_Content",),
    "policy-cn-002": ("div.detail-contents",),
}


def pick_body_html(html: str, source_id: str = "") -> str:
    """상세 페이지에서 본문으로 보이는 영역의 HTML을 고른다.

    텍스트로 바꾸는 일은 `normalize.clean_text()` 가 한다. 여기서는 어느
    영역이 본문인지만 정한다.

    이미지는 가져오지 않는다. 미국 출처의 재배포 조건이
    `text_only_no_images` 이기 때문이다 (`docs/source_policy.md` §3).
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                     "form", "noscript", "img", "figure", "picture", "svg"]):
        tag.decompose()

    # 출처별 선택자가 맞으면 그것을 쓴다. 사이트가 바뀌어 못 찾으면 아래
    # 일반 규칙으로 내려가고, 그 사실은 호출하는 쪽이 글자 수로 알 수 있다.
    for selector in SOURCE_BODY_SELECTORS.get(source_id, ()):
        node = soup.select_one(selector)
        if node and len(node.get_text(" ", strip=True)) > 100:
            return str(node)

    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node and len(node.get_text(" ", strip=True)) > 200:
            return str(node)
    return str(soup)


# ──────────────────────────── 수집 ────────────────────────────

def fetch(url: str, delay: float, timeout: int = 25) -> tuple[str | None, str | None]:
    """한 번만 요청한다. 실패 시 재시도하지 않는다 (기획안 §11)."""
    time.sleep(delay)
    try:
        res = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        res.raise_for_status()
        if not res.encoding or res.encoding.lower() == "iso-8859-1":
            res.encoding = res.apparent_encoding or "utf-8"
        return res.text, None
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: {exc}"


def collect_list_pages(source_id: str, list_url: str, window: tuple[str, str],
                       delay: float, limit: int | None, max_pages: int,
                       log: dict) -> list[dict]:
    """목록을 쪽 단위로 훑어 항목을 모은다. 멈추는 이유를 기록에 남긴다.

    멈추는 조건은 네 가지다. 다음 쪽 링크가 없거나, 그 쪽의 모든 글이
    수집 시작일보다 오래됐거나, `--limit` 을 채웠거나, `--max-pages` 에
    닿았을 때다.
    """
    parser = PARSERS[source_id]
    paged = source_id in PAGED_SOURCES
    log["paging_supported"] = paged
    if not paged:
        log["paging_note"] = ("이 출처는 목록 HTML로 과거 쪽을 넘길 수 없다. "
                              "최신 묶음만 받고, 주기적 실행으로 기간을 채운다")

    items: list[dict] = []
    seen: set[str] = set()
    url = list_url
    pages = max_pages if paged else 1

    for page_no in range(1, pages + 1):
        page_log = {"page": page_no, "url": url, "items": 0, "new": 0,
                    "error": None, "stop": None}
        log["pages"].append(page_log)

        html, error = fetch(url, delay)
        if html is None:
            page_log["error"] = error
            page_log["stop"] = "요청 실패. 재시도하지 않았다"
            if page_no == 1:
                log["error"] = error
            break
        log["fetched"] = True

        try:
            found = parser(html, url)
        except Exception as exc:  # 파서는 사이트 개편에 약하다. 터뜨리지 않고 기록한다
            page_log["error"] = f"목록 파싱 실패: {type(exc).__name__}: {exc}"
            page_log["stop"] = "파싱 실패"
            if page_no == 1:
                log["error"] = page_log["error"]
            break

        page_log["items"] = len(found)
        fresh = [it for it in found if it["url"] not in seen]
        for it in fresh:
            seen.add(it["url"])
        items.extend(fresh)
        page_log["new"] = len(fresh)

        if not found:
            page_log["stop"] = "항목 없음. 사이트 구조가 바뀌었을 수 있다"
            break
        if not fresh:
            page_log["stop"] = "앞 쪽과 같은 항목. 페이징이 듣지 않는다"
            break

        dates = [d for d in (nz.parse_published_date(it["published_raw"])
                             for it in found) if d]
        if dates and max(dates) < window[0]:
            page_log["stop"] = f"이 쪽의 최신 글({max(dates)})이 수집 시작일보다 오래됐다"
            break
        if limit and len(items) >= limit:
            page_log["stop"] = f"--limit {limit} 을 채웠다"
            break
        if page_no == pages:
            page_log["stop"] = (f"--max-pages {max_pages} 에 닿았다"
                                if paged else "페이징을 지원하지 않는 출처다")
            break

        nxt = find_next_link(BeautifulSoup(html, "lxml"), url)
        if not nxt:
            page_log["stop"] = "다음 쪽 링크가 없다"
            break
        url = nxt

    return items


def collect_html(source: dict, window: tuple[str, str], delay: float,
                 limit: int | None, fetch_bodies: bool = True,
                 max_pages: int = 1,
                 exclude_categories: set[str] | None = None) -> tuple[list[dict], dict]:
    """목록 페이지 한 곳을 수집한다. (문서 목록, 실행 기록) 을 돌려준다.

    `exclude_categories` 는 목록에 적힌 분류로 항목을 걸러낸다(예: NSF 의
    `Podcast`). 기본값은 비어 있어 **아무것도 걸러내지 않는다**. 주제로
    거르는 규칙은 아직 합의 전이고(`docs/topic_filter_rules.md`), 기획안 §4 는
    기술용어 사전 기반 추출이 관련성을 판단하도록 정했기 때문이다.
    """
    source_id = source["source_id"]
    source_name = source["source_name"]
    list_url = source["list_url"]
    exclude = {c.strip().lower() for c in (exclude_categories or set()) if c.strip()}

    log = {
        "source_id": source_id, "source_name": source_name, "list_url": list_url,
        "access_method": "html_list", "redistribution": source.get("redistribution", ""),
        "window": list(window), "fetched": False, "error": None,
        "pages": [], "items_found": 0, "items_seen": 0, "documents": 0,
        "categories": {}, "skipped_categories": {}, "excluded": {},
        "body_failures": [],
    }

    if source_id not in PARSERS:
        log["error"] = f"파서가 없다: {source_id}. PARSERS 에 추가해야 한다"
        return [], log

    items = collect_list_pages(source_id, list_url, window, delay, limit,
                               max_pages, log)
    log["items_found"] = len(items)
    if not items:
        return [], log

    if exclude:
        kept = []
        for it in items:
            key = it["category"].lower()
            if key and key in exclude:
                name = it["category"]
                log["skipped_categories"][name] = \
                    log["skipped_categories"].get(name, 0) + 1
            else:
                kept.append(it)
        items = kept

    items = items[:limit] if limit else items
    log["items_seen"] = len(items)
    for it in items:
        key = it["category"] or "(없음)"
        log["categories"][key] = log["categories"].get(key, 0) + 1

    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    documents = []

    for it in items:
        published = nz.parse_published_date(it["published_raw"])
        in_range = bool(published) and nz.in_window(published, *window)

        body = ""
        if fetch_bodies and in_range:
            detail, body_error = fetch(it["url"], delay)
            if detail is None:
                log["body_failures"].append({"url": it["url"], "error": body_error})
                documents.append(nz.failed_document(
                    source_type="policy", source_name=source_name,
                    original_url=it["url"], collected_at=collected_at,
                    reason="fetch_error", title=it["title"]))
                continue
            body = pick_body_html(detail, source_id)

        documents.append(nz.build_document(
            source_type="policy", source_name=source_name,
            title=it["title"], original_url=it["url"], raw_body=body,
            published_raw=it["published_raw"], collected_at=collected_at,
            window=window))

    for row in documents:
        reason = row.get("exclusion_reason")
        if reason:
            log["excluded"][reason] = log["excluded"].get(reason, 0) + 1
    log["documents"] = len(documents)
    return documents, log


# ──────────────────────────── 출처 목록 ────────────────────────────

def load_sources(source_id: str | None = None) -> list[dict]:
    with SOURCES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows
            if r.get("source_type") == "policy"
            and r.get("access_method") == "html_list"
            and r.get("access_check_status") in COLLECTABLE_STATUS
            and (not source_id or r.get("source_id") == source_id)]


# ──────────────────────────── 실행 ────────────────────────────

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="목록 페이지 수집기 (B 담당)")
    ap.add_argument("--source-id", help="특정 출처만 (예: policy-us-002)")
    ap.add_argument("--limit", type=int, help="출처당 처리 항목 수 상한")
    ap.add_argument("--delay", type=float, default=1.5, help="요청 간 대기(초). 기본 1.5")
    ap.add_argument("--replace", action="store_true",
                    help="이 출처의 기존 행을 지우고 이번에 받은 것만 남긴다. "
                         "파서를 고친 뒤 다시 받을 때 쓴다. --limit 과 함께 쓰면 "
                         "상한을 넘는 기존 행도 사라진다")
    ap.add_argument("--exclude-category", default="",
                    help="목록의 분류로 걸러낸다. 쉼표로 여러 개 (예: Podcast). "
                         "기본값은 걸러내지 않음. 주제 선별은 기술용어 사전이 한다")
    ap.add_argument("--max-pages", type=int, default=1,
                    help="목록을 몇 쪽까지 넘길지. 기본 1. 페이징이 되는 출처에만 적용된다")
    ap.add_argument("--no-bodies", action="store_true",
                    help="상세 페이지를 받지 않는다. 목록만 확인할 때 쓴다")
    ap.add_argument("--dry-run", action="store_true", help="대상만 보여주고 요청하지 않는다")
    args = ap.parse_args(argv)

    sources = load_sources(args.source_id)
    if not sources:
        print("수집 가능한 html_list 정책 출처가 없습니다.", file=sys.stderr)
        return 1

    print(f"수집 대상 {len(sources)}곳")
    for s in sources:
        has = "파서 있음" if s["source_id"] in PARSERS else "파서 없음"
        print(f"  {s['source_id']} {s['source_name']} "
              f"({s['access_check_status']}, {s['redistribution']}, {has})")

    if args.dry_run:
        print("\n--dry-run 이므로 요청하지 않았습니다.")
        return 0

    existing = nz.read_documents(DOCUMENTS_CSV)
    print(f"\n기존 documents.csv: {len(existing)}건")
    if args.replace:
        names = {s["source_name"] for s in sources}
        kept = [r for r in existing if r["source_name"] not in names]
        print(f"  --replace: 이 출처의 기존 {len(existing) - len(kept)}건을 새로 받은 것으로 바꿉니다")
        existing = kept

    all_docs, logs = [], []
    for s in sources:
        window = (s.get("collect_from") or "0001-01-01",
                  s.get("collect_to") or "9999-12-31")
        print(f"\n[{s['source_id']}] {s['source_name']}  기간 {window[0]} ~ {window[1]}")
        docs, log = collect_html(s, window, args.delay, args.limit,
                                 fetch_bodies=not args.no_bodies,
                                 max_pages=args.max_pages,
                                 exclude_categories=set(
                                     args.exclude_category.split(",")))
        if log["error"]:
            print(f"  실패: {log['error']}")
        else:
            for page in log["pages"]:
                print(f"  {page['page']}쪽: {page['items']}건"
                      f"(새 항목 {page['new']}건)"
                      f"{' — ' + page['stop'] if page['stop'] else ''}")
            print(f"  목록 {log['items_found']}건 중 {log['items_seen']}건 처리")
            if log["categories"]:
                print(f"  분류: {log['categories']}")
            if log["skipped_categories"]:
                print(f"  분류로 건너뜀: {log['skipped_categories']}")
            if log["excluded"]:
                print(f"  제외: {log['excluded']}")
            if log["body_failures"]:
                print(f"  본문 실패 {len(log['body_failures'])}건")
        all_docs.extend(docs)
        logs.append(log)

    if not all_docs:
        print("\n수집된 문서가 없습니다.")
        return 1

    dup = nz.dedupe(all_docs, existing)
    if dup["duplicate"] or dup["same_document"]:
        print(f"\n중복: {dup}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_written = 0
    for row in all_docs:
        if row["status"] == "analyzed" and row.get("_text"):
            (RAW_DIR / f"{row['document_id']}.txt").write_text(row["_text"],
                                                               encoding="utf-8")
            raw_written += 1

    merged, added = nz.merge_documents(existing, all_docs)
    nz.write_documents(DOCUMENTS_CSV, merged)
    problems = nz.validate_documents(merged)

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    log_path = PROCESSED_DIR / f"collect_html_log_{stamp}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "run_at": datetime.now(KST).isoformat(timespec="seconds"),
        "sources": logs, "documents_total": len(merged),
        "documents_added": added, "raw_files_written": raw_written,
        "validation_problems": problems,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    analyzed = sum(1 for r in all_docs if r["status"] == "analyzed")
    print(f"\n분석 대상 {analyzed}건 / 새로 추가 {added}건 / 총 {len(merged)}건")
    print(f"원문 저장 {raw_written}건 -> data/raw/")
    print(f"실행 기록 -> data/processed/{log_path.name}")

    if problems:
        print(f"\n규격 검증 문제 {len(problems)}건 (완료기준 C2)")
        for p in problems[:5]:
            print(f"  - {p}")
        return 2
    print("규격 검증 통과 (완료기준 C2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
