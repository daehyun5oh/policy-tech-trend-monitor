"""정제·중복 제거 공통 모듈 (이슈 #9, 담당 B)

수집기(뉴스·정책)가 공통으로 쓰는 정제·식별자·중복 제거 규칙을 모아 둔다.
규격은 `docs/data_dictionary.md` 에 있고, 이 모듈은 그 규격의 구현이다.

표준 라이브러리만 쓴다. 네트워크에 접속하지 않으므로 고정 입력으로 검증할 수 있다
(완료기준 C8).

쓰는 쪽
- `src/collect_policy.py` (정책, B)
- 미국·중국 html_list 수집기 (이슈 #37, B)
- 뉴스 수집기 (이슈 #7, A)
"""

from __future__ import annotations

import csv
import hashlib
import html
import re
import unicodedata
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# ──────────────────────────── 규격 상수 ────────────────────────────

# data_dictionary.md §2 의 필드 순서
DOCUMENT_FIELDS = [
    "document_id", "source_type", "source_name", "source_document_id",
    "title", "original_url", "published_date", "collected_at",
    "analysis_scope", "content_hash", "status", "exclusion_reason",
    "duplicate_of",
    # 선택 열. 정제를 마친 본문 글자 수(제목 제외). 제목만 분석한 행은 0,
    # 제외·실패 행은 빈 값 (docs/data_dictionary.md §2, schema.DOCUMENT_OPTIONAL_COLUMNS)
    "text_chars",
]

# data_dictionary.md §2 에서 status=analyzed 일 때 반드시 채워져야 하는 필드
REQUIRED_WHEN_ANALYZED = ["document_id", "title", "original_url", "published_date"]

# data_dictionary.md §3 의 허용값
STATUS_VALUES = {"analyzed", "excluded", "failed"}
EXCLUSION_REASONS = {
    "duplicate", "no_published_date", "out_of_period", "not_target_topic",
    "no_text", "fetch_error", "parse_error", "pdf_scan_unsupported",
    "access_restricted",
}
ANALYSIS_SCOPES = {"title", "title_summary", "title_body"}
SOURCE_TYPES = {"news", "policy"}

# data_dictionary.md §5 의 추적용 질의 매개변수
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid",
}

# 출처가 제공하는 원문 식별자로 쓸 질의 매개변수 (앞에서부터 먼저 찾은 것을 쓴다)
SOURCE_ID_PARAMS = ("nttSeqNo", "newsId", "list_no", "contentId", "id")


# ──────────────────────────── 식별자·해시 ────────────────────────────

def normalize_url(url: str) -> str:
    """data_dictionary.md §5 의 URL 정규화 규칙.

    스킴·호스트 소문자화, 기본 포트 제거, 조각 제거, 추적 매개변수 제거,
    남은 매개변수 이름 오름차순 정렬, 경로 끝 슬래시 제거.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    query.sort(key=lambda kv: kv[0])
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def content_hash(text: str) -> str:
    """data_dictionary.md §5 의 content_hash 계산 규칙.

    NFKC 정규화 → 연속 공백 축약 → 앞뒤 공백 제거 → UTF-8 SHA-256 소문자 16진수.
    해시 대상 텍스트는 `analysis_scope` 와 일치해야 한다.
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_document_id(source_type: str, source_name: str,
                     source_document_id: str, normalized_url: str) -> str:
    """data_dictionary.md §5 의 document_id 생성 규칙.

    원문 식별자가 있으면 `source_name|source_document_id`, 없으면 정규화 URL을
    해시한다. 같은 입력이면 재실행해도 같은 값이 나온다 (완료기준 C8).
    """
    seed = f"{source_name}|{source_document_id}" if source_document_id else normalized_url
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"{source_type}-{digest}"


def extract_source_document_id(url: str) -> str:
    """출처가 제공하는 원문 식별자를 URL 질의 매개변수에서 꺼낸다.

    없으면 빈 문자열. 과기정통부는 `nttSeqNo` 를 쓴다.
    """
    query = dict(parse_qsl(urlsplit(url).query))
    for key in SOURCE_ID_PARAMS:
        if query.get(key):
            return f"{key}={query[key]}"
    return ""


# ──────────────────────────── 날짜 ────────────────────────────

_DATE_FORMATS = ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d",
                 "%a, %d %b %Y %H:%M:%S %z", "%d %b %Y", "%B %d, %Y",
                 # 영문 출처는 월을 줄여 쓰기도 한다 (중국과학원 `Oct 04, 2026`)
                 "%b %d, %Y", "%b %d %Y", "%B %d %Y")
_DATE_RE = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def parse_published_date(raw: str) -> str | None:
    """발행일을 `YYYY-MM-DD` 로 바꾼다. 확인할 수 없으면 None.

    확인할 수 없는 문서는 수집일로 대체하지 않는다 (기획안 §3).
    호출하는 쪽에서 `no_published_date` 로 제외해야 한다.

    과기정통부 RSS 는 `2026.10.15` 로 내려준다 (RFC 822 아님).
    """
    if not raw:
        return None
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    m = _DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


def in_window(published_date: str, start: str, end: str) -> bool:
    """수집 기간 안인지. 시작일과 종료일을 모두 포함한다."""
    return start <= published_date <= end


# ──────────────────────────── 텍스트 정제 ────────────────────────────

HWP_MARKER = "<!--[data-hwpjson]"
_HWP_RE = re.compile(r"<!--\s*\[data-hwpjson\].*?-->", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"</(p|div|li|tr|h[1-6])\s*>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PHONE_RE = re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")


def unescape_fully(text: str, max_rounds: int = 3) -> str:
    """더 이상 바뀌지 않을 때까지 HTML 엔티티를 되돌린다.

    엔티티가 두 겹으로 싸여 오는 경우가 있다 (`&amp;nbsp;` → `&nbsp;` → 공백).
    """
    for _ in range(max_rounds):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return text


def clean_text(raw: str, *, strip_hwp: bool = True, strip_phone: bool = True) -> str:
    """HTML 본문에서 분석에 쓸 텍스트만 남긴다.

    순서가 중요하다. HWP 블록은 피드에서 `&lt;!--[data-hwpjson]` 처럼
    **이스케이프된 상태**로 오기 때문에, 태그를 먼저 지우고 이스케이프를 나중에
    풀면 블록이 되살아난다. 이스케이프를 먼저 풀어야 한다.

    strip_phone 은 기획안 §11("불필요한 개인정보를 수집하지 않는다")에 따라
    담당자 연락처 형태를 지운다. 기본값은 True.
    """
    text = unescape_fully(raw)

    if strip_hwp:
        text = _HWP_RE.sub(" ", text)
        # 닫는 --> 없이 잘려 온 경우: 시작 지점부터 끝까지 버린다
        cut = text.find(HWP_MARKER)
        if cut != -1:
            text = text[:cut]

    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)

    if strip_phone:
        text = _PHONE_RE.sub(" ", text)

    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ──────────────────────────── 문서 행 만들기 ────────────────────────────

def build_document(*, source_type: str, source_name: str, title: str,
                   original_url: str, raw_body: str, published_raw: str,
                   collected_at: str, window: tuple[str, str],
                   strip_hwp: bool = True) -> dict:
    """수집한 항목 하나를 `documents.csv` 한 행으로 만든다.

    제목이 없으면 `no_text`, 발행일을 못 읽으면 `no_published_date`,
    수집 기간 밖이면 `out_of_period` 로 제외 표시한다.

    `_normalized_url` 과 `_text` 는 뒤 단계에서 쓰는 내부 값으로,
    CSV에는 기록하지 않는다.
    """
    title = (title or "").strip()
    normalized = normalize_url(original_url)
    sdid = extract_source_document_id(original_url)
    doc_id = make_document_id(source_type, source_name, sdid, normalized)

    body = clean_text(raw_body or "", strip_hwp=strip_hwp)
    scope = "title_body" if body else "title"
    analyzed_text = f"{title}\n\n{body}" if body else title

    published = parse_published_date(published_raw)

    row = {
        "document_id": doc_id,
        "source_type": source_type,
        "source_name": source_name,
        "source_document_id": sdid,
        "title": title,
        "original_url": original_url,
        "published_date": published or "",
        "collected_at": collected_at,
        "analysis_scope": scope,
        "content_hash": content_hash(analyzed_text),
        "status": "analyzed",
        "exclusion_reason": "",
        "duplicate_of": "",
        # 본문 글자 수. 제목은 세지 않는다. 본문을 못 받았으면 0
        "text_chars": str(len(body)),
        "_normalized_url": normalized,
        "_text": analyzed_text,
    }

    if not title:
        row["status"], row["exclusion_reason"] = "excluded", "no_text"
    elif published is None:
        row["status"], row["exclusion_reason"] = "excluded", "no_published_date"
    elif not in_window(published, *window):
        row["status"], row["exclusion_reason"] = "excluded", "out_of_period"

    # 제외·실패 행은 빈 값으로 둔다 (A의 collect_news.py 와 같은 규격)
    if row["status"] != "analyzed":
        row["text_chars"] = ""

    return row


def failed_document(*, source_type: str, source_name: str, original_url: str,
                    collected_at: str, reason: str, title: str = "") -> dict:
    """수집·추출에 실패한 문서를 기록한다.

    reason 은 `fetch_error`, `parse_error`, `pdf_scan_unsupported`,
    `access_restricted` 중 하나다 (data_dictionary.md §3).
    """
    if reason not in EXCLUSION_REASONS:
        raise ValueError(f"허용되지 않은 사유: {reason}")
    normalized = normalize_url(original_url) if original_url else ""
    sdid = extract_source_document_id(original_url) if original_url else ""
    return {
        "document_id": make_document_id(source_type, source_name, sdid, normalized),
        "source_type": source_type,
        "source_name": source_name,
        "source_document_id": sdid,
        "title": (title or "").strip(),
        "original_url": original_url,
        "published_date": "",
        "collected_at": collected_at,
        "analysis_scope": "title",
        "content_hash": "",
        "status": "failed",
        "exclusion_reason": reason,
        "duplicate_of": "",
        "text_chars": "",
        "_normalized_url": normalized,
        "_text": "",
    }


# ──────────────────────────── 중복 제거 ────────────────────────────

def dedupe(documents: list[dict], existing: list[dict] | None = None) -> dict:
    """data_dictionary.md §5 의 중복 제거 순서를 적용한다.

    중복은 두 종류로 갈린다. `document_id` 가 원문 식별자 또는 정규화 URL에서
    만들어지기 때문이다.

    **같은 문서** (`same_document`)
        중복 제거 1단계(`source_document_id` 일치) 또는 2단계(정규화 URL 일치)에
        걸린 경우. 이때는 `document_id` 가 **같게** 나오므로 글자 그대로 같은
        문서다. `merge_documents` 가 ID 중복을 걸러 CSV에는 한 행만 남는다.

    **내용 중복** (`duplicate`)
        3단계(`content_hash` 일치)에만 걸린 경우. 주소와 식별자가 달라
        `document_id` 도 다르다. 이때는 행을 남기고
        `status=excluded`, `exclusion_reason=duplicate`, `duplicate_of=대표 ID`
        로 연결을 보존한다.

    먼저 들어온 문서를 대표로 남긴다. `documents` 를 제자리에서 수정하고
    종류별 건수를 돌려준다.
    """
    seen_ids: set[str] = set()
    seen_hash: dict[str, str] = {}

    for row in (existing or []):
        seen_ids.add(row["document_id"])
        if row.get("status") == "analyzed" and row.get("content_hash"):
            seen_hash.setdefault(row["content_hash"], row["document_id"])

    counts = {"duplicate": 0, "same_document": 0}
    for row in documents:
        if row["status"] != "analyzed":
            continue

        if row["document_id"] in seen_ids:
            # 1·2단계: 식별자나 주소가 같아 document_id 까지 같은 경우
            row["status"] = "excluded"
            row["exclusion_reason"] = "duplicate"
            row["duplicate_of"] = row["document_id"]
            row["text_chars"] = ""
            row["_same_id"] = True
            counts["same_document"] += 1
            continue

        rep = seen_hash.get(row["content_hash"]) if row["content_hash"] else None
        if rep and rep != row["document_id"]:
            # 3단계: 내용만 같은 경우
            row["status"] = "excluded"
            row["exclusion_reason"] = "duplicate"
            row["duplicate_of"] = rep
            row["text_chars"] = ""
            counts["duplicate"] += 1
            continue

        seen_ids.add(row["document_id"])
        if row["content_hash"]:
            seen_hash.setdefault(row["content_hash"], row["document_id"])

    return counts


# ──────────────────────────── CSV 읽기·쓰기 ────────────────────────────

def read_documents(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def merge_documents(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """기존 문서에 새 문서를 더한다. `document_id` 가 같으면 기존 것을 남긴다."""
    seen = {r["document_id"] for r in existing}
    merged = list(existing)
    added = 0
    for row in new:
        if row["document_id"] in seen:
            continue
        merged.append({k: row.get(k, "") for k in DOCUMENT_FIELDS})
        seen.add(row["document_id"])
        added += 1
    return merged, added


def write_documents(path: Path, rows: list[dict]) -> None:
    """`documents.csv` 를 규격대로 쓴다. UTF-8, LF, 필드 순서 고정."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DOCUMENT_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in DOCUMENT_FIELDS})


# ──────────────────────────── 검증 ────────────────────────────

def validate_documents(rows: list[dict]) -> list[str]:
    """완료기준 C2 점검. 문제를 문장 목록으로 돌려준다. 빈 목록이면 통과.

    CSV 스키마 검증 스크립트(이슈 #15)에서 이 함수를 쓸 수 있다.
    """
    problems: list[str] = []
    ids: dict[str, int] = {}

    for i, row in enumerate(rows, start=2):  # 헤더가 1행
        did = row.get("document_id", "")
        if not did:
            problems.append(f"{i}행: document_id 가 비어 있다")
        else:
            if did in ids:
                problems.append(f"{i}행: document_id 중복 ({did}, {ids[did]}행과 같다)")
            ids[did] = i

        status = row.get("status", "")
        if status not in STATUS_VALUES:
            problems.append(f"{i}행: status 허용값이 아니다 ({status!r})")

        scope = row.get("analysis_scope", "")
        if scope and scope not in ANALYSIS_SCOPES:
            problems.append(f"{i}행: analysis_scope 허용값이 아니다 ({scope!r})")

        stype = row.get("source_type", "")
        if stype not in SOURCE_TYPES:
            problems.append(f"{i}행: source_type 허용값이 아니다 ({stype!r})")

        # 선택 열. 있으면 0 이상의 정수여야 한다 (A의 validate_csv.py 와 같은 기준)
        chars = row.get("text_chars", "")
        if chars not in ("", None):
            if not str(chars).isdigit():
                problems.append(f"{i}행: text_chars 는 0 이상의 정수이거나 비어 있어야 한다 ({chars!r})")
            elif status != "analyzed":
                problems.append(f"{i}행: status={status} 인데 text_chars 에 값이 있다 ({chars!r})")

        reason = row.get("exclusion_reason", "")
        if status == "analyzed":
            for field in REQUIRED_WHEN_ANALYZED:
                if not row.get(field):
                    problems.append(f"{i}행: status=analyzed 인데 {field} 가 비어 있다")
            if reason:
                problems.append(f"{i}행: status=analyzed 인데 exclusion_reason 이 있다 ({reason})")
            if len(row.get("content_hash", "")) != 64:
                problems.append(f"{i}행: content_hash 가 64자가 아니다")
        else:
            if not reason:
                problems.append(f"{i}행: status={status} 인데 exclusion_reason 이 없다")
            elif reason not in EXCLUSION_REASONS:
                problems.append(f"{i}행: exclusion_reason 허용값이 아니다 ({reason!r})")

        if reason == "duplicate" and not row.get("duplicate_of"):
            problems.append(f"{i}행: exclusion_reason=duplicate 인데 duplicate_of 가 없다")

    # duplicate_of 참조 유효성
    analyzed = {r["document_id"] for r in rows if r.get("status") == "analyzed"}
    for i, row in enumerate(rows, start=2):
        target = row.get("duplicate_of", "")
        if not target:
            continue
        if target not in ids:
            problems.append(f"{i}행: duplicate_of 가 가리키는 문서가 없다 ({target})")
        elif target not in analyzed:
            problems.append(f"{i}행: duplicate_of 가 analyzed 문서가 아니다 ({target})")

    return problems
