#!/usr/bin/env python3
"""키워드 추출·정규화 (이슈 #11). 규칙은 docs/extraction_rules.md 참고.

입력  documents.csv  (status 가 analyzed 인 문서만 분석)
      사전 CSV       (config/keywords.csv)
      본문 폴더      (analysis_scope 가 title_summary/title_body 인 문서의 <document_id>.txt)
출력  document_keywords.csv

사용 예:
  python src/extract_keywords.py --documents data/processed/documents.csv \\
      --dictionary config/keywords.csv --text-dir data/raw/text \\
      --out data/processed/document_keywords.csv

종료 코드: 0 정상 / 2 사전 오류 또는 일부 문서 처리 실패(처리된 결과는 파일에 기록)
"""
import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import KEYWORD_COLUMNS, SCOPES, STATUS_ANALYZED  # noqa: E402

DICTIONARY_COLUMNS = ["keyword_id", "keyword", "term", "case_sensitive", "dictionary_version"]
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")


class DictionaryError(ValueError):
    """사전 CSV가 규칙에 맞지 않을 때 발생한다."""


def normalize_text(text):
    """NFKC 정규화 후 연속 공백(줄바꿈 포함)을 공백 하나로 합친다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


class Term:
    __slots__ = ("keyword_id", "text", "pattern")

    def __init__(self, keyword_id, text, case_sensitive):
        self.keyword_id = keyword_id
        self.text = text
        # 영문·숫자로 시작하거나 끝나는 용어는 그 쪽 바로 옆에 영문·숫자가 붙어 있으면 일치로 보지 않는다.
        # (예: 'AI'는 'MAIL' 안에서 일치하지 않는다. 한글 조사가 붙은 'AI와'는 일치한다.)
        # 영문 글자로 끝나는 용어 뒤에는 복수형 소문자 's' 하나를 허용한다. (예: 'LLMs', 'GPUs')
        pre = r"(?<![A-Za-z0-9])" if ASCII_ALNUM.match(text[0]) else ""
        if re.match(r"[A-Za-z]", text[-1]):
            post = r"s?(?![A-Za-z0-9])"
        elif ASCII_ALNUM.match(text[-1]):
            post = r"(?![A-Za-z0-9])"
        else:
            post = ""
        flags = 0 if case_sensitive else re.IGNORECASE
        self.pattern = re.compile(pre + re.escape(text) + post, flags)


class Dictionary:
    def __init__(self, version, keywords, terms):
        self.version = version
        self.keywords = keywords  # keyword_id -> 표준 키워드
        self.terms = terms


def load_dictionary(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in DICTIONARY_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DictionaryError("사전에 필요한 열이 없음: " + ", ".join(missing))
        rows = list(reader)
    if not rows:
        raise DictionaryError("사전이 비어 있음")
    versions = {(r["dictionary_version"] or "").strip() for r in rows}
    if len(versions) != 1 or "" in versions:
        raise DictionaryError("dictionary_version은 모든 행에서 같은 비어 있지 않은 값이어야 함: %s" % sorted(versions))

    keywords, terms = {}, []
    seen_exact, seen_fold, canonical = set(), {}, set()
    for n, r in enumerate(rows, start=2):
        kid = (r["keyword_id"] or "").strip()
        kw = (r["keyword"] or "").strip()
        text = normalize_text(r["term"] or "")
        cs = (r["case_sensitive"] or "").strip()
        where = "사전 %d행" % n
        if not kid or not kw or not text:
            raise DictionaryError("%s: keyword_id, keyword, term은 비어 있을 수 없음" % where)
        if cs not in ("0", "1"):
            raise DictionaryError("%s: case_sensitive는 0 또는 1이어야 함 (값: %r)" % (where, cs))
        if keywords.setdefault(kid, kw) != kw:
            raise DictionaryError("%s: keyword_id %s에 서로 다른 keyword가 있음" % (where, kid))
        if (text, cs) in seen_exact:
            raise DictionaryError("%s: 같은 term이 중복됨: %s" % (where, text))
        seen_exact.add((text, cs))
        fold = text.casefold()
        if seen_fold.setdefault(fold, kid) != kid:
            raise DictionaryError("%s: term '%s'이 서로 다른 keyword_id(%s, %s)에 등록됨" % (where, text, seen_fold[fold], kid))
        if fold == normalize_text(kw).casefold():
            canonical.add(kid)
        terms.append(Term(kid, text, cs == "1"))
    for kid, kw in keywords.items():
        if kid not in canonical:
            raise DictionaryError("keyword_id %s의 표준 키워드 '%s'가 term으로 등록되어 있지 않음" % (kid, kw))
    return Dictionary(versions.pop(), keywords, terms)


def find_matches(text, terms):
    """최장 일치 우선, 겹치지 않는 일치 구간 [(start, end, keyword_id)]을 시작 위치순으로 반환."""
    candidates = []
    for term in terms:
        for m in term.pattern.finditer(text):
            candidates.append((m.start(), m.end(), term.keyword_id))
    # 긴 일치 우선, 같은 길이면 앞선 위치, 그래도 같으면 keyword_id 순 (결과를 결정적으로 만든다)
    candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0], c[2]))
    used = [False] * len(text)
    selected = []
    for start, end, kid in candidates:
        if any(used[start:end]):
            continue
        for i in range(start, end):
            used[i] = True
        selected.append((start, end, kid))
    selected.sort()
    return selected


def analyze_text(text, dictionary):
    """[(keyword_id, 출현 횟수, 실제 표기 목록)] 을 keyword_id 순으로 반환."""
    return analyze_segments([text], dictionary)


def analyze_segments(segments, dictionary):
    """텍스트 조각(제목, 요약·본문 등)을 각각 따로 매칭해 합친다.

    조각 경계를 넘는 일치(제목 끝 '양자' + 본문 첫머리 '컴퓨팅')는 만들지 않는다.
    실제 표기는 조각 순서, 조각 안에서는 등장 순서대로 중복 없이 모은다.
    """
    found = {}
    for segment in segments:
        text = normalize_text(segment)
        for start, end, kid in find_matches(text, dictionary.terms):
            entry = found.setdefault(kid, [0, []])
            entry[0] += 1
            surface = text[start:end]
            if surface not in entry[1]:
                entry[1].append(surface)
    return [(kid, occ, surfaces) for kid, (occ, surfaces) in sorted(found.items())]


def read_documents(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# 본문 파일(<document_id>.txt)을 찾는 기본 폴더. 정책 수집기는 data/raw, 뉴스 수집기는 data/processed/texts 에 쓴다.
DEFAULT_TEXT_DIRS = ("data/raw", "data/processed/texts")


def as_dirs(text_dir):
    """text_dir 는 None, 폴더 하나, 폴더 여러 개(목록) 중 무엇이든 받는다."""
    if text_dir is None:
        return []
    if isinstance(text_dir, (str, Path)):
        return [Path(text_dir)]
    return [Path(d) for d in text_dir]


def extract_documents(documents, dictionary, text_dir=None):
    """(출력 행 목록, 오류 메시지 목록, 분석 대상 문서 수)를 반환한다.

    text_dir 에 폴더 여러 개를 주면 앞에서부터 <document_id>.txt 를 찾아 처음 있는 파일을 쓴다.
    """
    dirs = as_dirs(text_dir)
    out, errors, analyzed = [], [], 0
    for n, doc in enumerate(documents, start=2):
        if (doc.get("status") or "").strip() != STATUS_ANALYZED:
            continue
        analyzed += 1
        doc_id = (doc.get("document_id") or "").strip()
        where = "documents.csv %d행 (%s)" % (n, doc_id or "document_id 없음")
        if not doc_id:
            errors.append("%s: document_id가 비어 있음" % where)
            continue
        scope = (doc.get("analysis_scope") or "").strip()
        if scope not in SCOPES:
            errors.append("%s: analysis_scope가 %s 중 하나여야 함 (값: %r)" % (where, "/".join(SCOPES), scope))
            continue
        segments = [doc.get("title") or ""]
        if scope != "title":
            if not dirs:
                errors.append("%s: analysis_scope=%s 이지만 본문 폴더가 없음 (--text-dir 로 지정)" % (where, scope))
                continue
            if not SAFE_ID.fullmatch(doc_id):
                errors.append("%s: document_id에 파일 이름으로 쓸 수 없는 문자가 있음" % where)
                continue
            path = next((d / (doc_id + ".txt") for d in dirs if (d / (doc_id + ".txt")).is_file()),
                        dirs[0] / (doc_id + ".txt"))
            try:
                segments.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as e:
                errors.append("%s: 본문 파일을 읽을 수 없음 (%s): %s" % (where, path, e))
                continue
        for kid, occ, surfaces in analyze_segments(segments, dictionary):
            out.append({
                "document_id": doc_id,
                "keyword_id": kid,
                "keyword": dictionary.keywords[kid],
                "matched_terms": json.dumps(surfaces, ensure_ascii=False, separators=(",", ":")),
                "occurrences": str(occ),
                "dictionary_version": dictionary.version,
            })
    out.sort(key=lambda r: (r["document_id"], r["keyword_id"]))
    return out, errors, analyzed


def write_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=KEYWORD_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description="분석 완료 문서에서 기술키워드를 추출해 document_keywords.csv를 만든다.")
    p.add_argument("--documents", default="data/processed/documents.csv")
    p.add_argument("--dictionary", default="config/keywords.csv")
    p.add_argument("--text-dir", action="append", default=None, metavar="DIR",
                   help="title_summary/title_body 범위 문서의 <document_id>.txt 폴더. 여러 번 줄 수 있고 "
                        "주지 않으면 %s 를 차례로 찾는다" % ", ".join(DEFAULT_TEXT_DIRS))
    p.add_argument("--out", default="data/processed/document_keywords.csv")
    args = p.parse_args(argv)

    try:
        dictionary = load_dictionary(args.dictionary)
        documents = read_documents(args.documents)
    except (DictionaryError, OSError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2

    text_dirs = args.text_dir if args.text_dir else [d for d in DEFAULT_TEXT_DIRS if Path(d).is_dir()]
    rows, errors, analyzed = extract_documents(documents, dictionary, text_dirs)
    write_rows(args.out, rows)
    with_keywords = len({r["document_id"] for r in rows})
    print("사전 버전 %s / 분석 대상 %d건 / 키워드 검출 %d건 / 출력 %d행 -> %s"
          % (dictionary.version, analyzed, with_keywords, len(rows), args.out))
    for msg in errors:
        print("처리 실패: " + msg, file=sys.stderr)
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
