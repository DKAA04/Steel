"""docs/JARVIS.md section 16 cites real tests, and every Jarvis test is cited."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
CITED_FILES = (
    "test_jarvis_core.py",
    "test_jarvis_loop.py",
    "test_jarvis_check_script.py",
    "test_telemetry_guard.py",
    "test_test_timeout.py",
)


def evidence() -> str:
    doc = (ROOT / "docs" / "JARVIS.md").read_text(encoding="utf-8")
    return doc.split("## 16. Evidence", 1)[1]


class JarvisEvidenceTest(unittest.TestCase):
    def test_every_test_the_document_cites_exists(self) -> None:
        cited = re.findall(r"`(test_\w+\.py)::(\w+)\.(test_\w+)`", evidence())
        self.assertGreater(len(cited), 90, "section 16 cites too few tests")
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

    def test_every_jarvis_test_is_cited(self) -> None:
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

    def test_the_status_says_what_only_the_owners_live_run_can_show(self) -> None:
        doc = (ROOT / "docs" / "JARVIS.md").read_text(encoding="utf-8")
        status = doc.split("\n\n", 2)[1]
        self.assertIn("NOT yet run live by the owner", status)
        self.assertIn("scripts/jarvis_check.py", status)


if __name__ == "__main__":
    unittest.main()
