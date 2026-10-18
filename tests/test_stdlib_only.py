"""표준 라이브러리만 써야 하는 모듈이 외부 패키지를 가져오지 않는지 검사한다 (docs/environment.md §의존성 경계).

수집 계층(src/collect_policy.py, src/collect_html.py)은 requirements.txt 의 외부 패키지를 쓸 수 있어 검사하지 않는다.
이 경계를 넓힐 때는 환경 문서와 이 목록을 함께 바꾸고 상대와 합의한다.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STDLIB_ONLY = [
    "src/schema.py", "src/extract_keywords.py", "src/validate_csv.py", "src/collect_news.py", "dashboard/app.py",
]
# B 담당 모듈은 아직 main 에 없을 수 있어(PR이 순서대로 들어옴) 파일이 있을 때만 검사한다.
STDLIB_ONLY_IF_PRESENT = [
    "src/normalize.py", "src/aggregate.py", "src/generate_report.py", "src/pipeline.py",
]
LOCAL = {"schema", "validate_csv", "extract_keywords", "collect_news", "app",  # 이 저장소의 모듈
         "normalize", "aggregate", "generate_report", "pipeline", "collect_policy", "collect_html"}


def imported_top_level(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class StdlibOnlyTest(unittest.TestCase):
    def test_no_third_party_imports(self):
        for rel in STDLIB_ONLY + [r for r in STDLIB_ONLY_IF_PRESENT if (ROOT / r).exists()]:
            path = ROOT / rel
            self.assertTrue(path.exists(), rel)
            third_party = {n for n in imported_top_level(path) if n not in sys.stdlib_module_names and n not in LOCAL}
            self.assertEqual(third_party, set(), "%s 가 표준 라이브러리 밖의 패키지를 가져옴" % rel)


if __name__ == "__main__":
    unittest.main()
