"""docs/LATENCY.md section 8 cites real tests, and every Phase 5b test is cited."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
CITED_FILES = (
    "test_latency.py",
    "test_latency_check_script.py",
    "test_licence.py",
    "test_context.py",
    "test_warmup_report.py",
    "test_prompt_budget.py",
)


def evidence() -> str:
    doc = (ROOT / "docs" / "LATENCY.md").read_text(encoding="utf-8")
    return doc.split("### Evidence", 1)[1]


def methods_by_class(file: str) -> dict[str, set[str]]:
    tree = ast.parse((TESTS / file).read_text(encoding="utf-8"))
    return {
        node.name: {
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


class LatencyEvidenceTest(unittest.TestCase):
    def test_every_test_the_document_cites_exists(self) -> None:
        cited = re.findall(r"`(test_\w+\.py)::(\w+)\.(test_\w+)`", evidence())
        self.assertGreater(len(cited), 30, "section 8 cites too few tests")
        for file, cls, name in sorted(set(cited)):
            self.assertIn(
                name, methods_by_class(file).get(cls, set()), f"{file}::{cls}"
            )

    def test_every_phase_5b_test_is_cited(self) -> None:
        text, missing = evidence(), []
        for file in CITED_FILES:
            for cls, names in methods_by_class(file).items():
                missing += [
                    f"{file}::{cls}.{n}"
                    for n in names
                    if f"{file}::{cls}.{n}" not in text
                ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
