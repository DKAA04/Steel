"""docs/INJECTION.md section 6 cites real tests, and every Phase 4 test is cited."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
CITED_FILES = ("test_tooldata.py", "test_chat_slash.py", "test_room_tool.py")


def evidence() -> str:
    doc = (ROOT / "docs" / "INJECTION.md").read_text(encoding="utf-8")
    return doc.split("## 6. Evidence", 1)[1]


class InjectionEvidenceTest(unittest.TestCase):
    def test_every_test_the_document_cites_exists(self) -> None:
        cited = re.findall(r"`(test_\w+\.py)::(\w+)\.(test_\w+)`", evidence())
        self.assertGreater(len(cited), 50, "section 6 cites too few tests")
        for file, cls, name in sorted(set(cited)):
            tree = ast.parse((TESTS / file).read_text(encoding="utf-8"))
            classes = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == cls
            ]
            self.assertTrue(classes, f"{file} has no class {cls}")
            methods = {
                n.name for n in classes[0].body if isinstance(n, ast.FunctionDef)
            }
            self.assertIn(name, methods, f"{file}::{cls}.{name}")

    def test_every_phase_4_test_is_cited(self) -> None:
        text, missing = evidence(), []
        for file in CITED_FILES:
            tree = ast.parse((TESTS / file).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name.startswith(
                            "test_"
                        ):
                            key = f"{file}::{node.name}.{item.name}"
                            if key not in text:
                                missing.append(key)
        self.assertEqual(missing, [])

    def test_every_rule_has_a_cited_test(self) -> None:
        rows = [line for line in evidence().splitlines() if line.startswith("| Rule ")]
        for rule in range(1, 8):
            cells = [r for r in rows if r.startswith(f"| Rule {rule}")]
            self.assertTrue(cells, f"rule {rule} has no row")
            self.assertTrue(all("::" in r for r in cells), f"rule {rule} cites no test")


if __name__ == "__main__":
    unittest.main()
