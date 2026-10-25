"""STEEL's vision code must not be able to reach a cloud, write a file or open a second camera path."""

from __future__ import annotations

import ast
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_steel as base

from steel.tools import get_camera_tool

ROOT = Path(__file__).resolve().parent.parent
VISION = ROOT / "steel" / "vision"
TOOLS = ROOT / "steel" / "tools"
TESTS = Path(__file__).resolve().parent


def _without_docstrings(tree: ast.AST) -> ast.AST:
    """The tree with docstrings dropped, so words in explanations do not count."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return tree


class CodeFacts:
    """What one source file imports, calls, names and quotes (docstrings excluded)."""

    def __init__(self, text: str) -> None:
        tree = _without_docstrings(ast.parse(text))
        self.imports: list[str] = []
        self.calls: set[str] = set()
        self.bare_calls: set[str] = set()  # f(...) as opposed to x.f(...)
        self.names: set[str] = set()
        self.strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.imports += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.imports.append(node.module)
                self.names.update(a.name for a in node.names)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    self.calls.add(func.id)
                    self.bare_calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    self.calls.add(func.attr)
                    if isinstance(func.value, ast.Name):
                        self.calls.add(f"{func.value.id}.{func.attr}")
            elif isinstance(node, ast.Name):
                self.names.add(node.id)
            elif isinstance(node, ast.Attribute):
                self.names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.strings.append(node.value)


class SourceHygieneTest(unittest.TestCase):
    def facts(self) -> dict[str, CodeFacts]:
        files = list(VISION.glob("*.py")) + [TOOLS / "camera.py"]
        return {
            p.relative_to(ROOT).as_posix(): CodeFacts(p.read_text(encoding="utf-8"))
            for p in files
        }

    def test_no_camera_library_cloud_client_or_http_library_is_imported(self) -> None:
        banned = (
            "cv2",
            "PIL",
            "imageio",
            "pygame",
            "sounddevice",
            "openjarvis.speech",
            "openai",
            "anthropic",
            "httpx",
            "requests",
            "urllib.request",
            "http.client",
            "socket",
            "ssl",
            "tempfile",
        )
        for name, facts in self.facts().items():
            for module in facts.imports:
                for word in banned:
                    self.assertFalse(
                        module == word or module.startswith(word + "."),
                        f"{name} imports {module}",
                    )

    def test_no_engine_discovery_and_no_cloud_key_is_read(self) -> None:
        for name, facts in self.facts().items():
            for word in ("get_engine", "discover_engines", "get_speech_backend"):
                self.assertNotIn(word, facts.names | facts.calls, f"{name} uses {word}")
            for text in facts.strings:
                self.assertNotRegex(text, r"[A-Z]+_API_KEY", name)

    def test_nothing_writes_a_file_or_makes_a_temporary_one(self) -> None:
        banned_calls = {
            "os.open",
            "write_text",
            "write_bytes",
            "NamedTemporaryFile",
            "TemporaryDirectory",
            "mkstemp",
            "imwrite",
            "save",
            "copyfile",
            "rename",
            "mkdir",
        }
        for name, facts in self.facts().items():
            self.assertEqual(facts.calls & banned_calls, set(), name)
            self.assertNotIn("open", facts.bare_calls, name)  # the builtin

    def test_only_the_camera_module_runs_a_program(self) -> None:
        for name, facts in self.facts().items():
            used = {"subprocess", "Popen", "system"} & (
                set(facts.imports) | facts.names | facts.calls
            )
            if name == "steel/vision/camera.py":
                self.assertIn("subprocess", used)
            else:
                self.assertEqual(used, set(), name)

    def test_the_tool_does_not_build_an_executor_or_read_the_camera_itself(
        self,
    ) -> None:
        facts = self.facts()["steel/tools/camera.py"]
        self.assertNotIn("ToolExecutor", facts.calls)
        self.assertNotIn("FfmpegCameraSource", facts.names | facts.calls)

    def test_the_source_is_opened_only_by_capture_one(self) -> None:
        callers = []
        for path in (ROOT / "steel").rglob("*.py"):
            if re.search(r"\bsource\.open\(", path.read_text(encoding="utf-8")):
                callers.append(path.relative_to(ROOT).as_posix())
        # voice/recorder.py opens the push-to-talk microphone, jarvis/listen.py
        # the always-on one (Phase 5c), vision/capture.py the camera.
        self.assertEqual(
            sorted(callers),
            [
                "steel/jarvis/listen.py",
                "steel/vision/capture.py",
                "steel/voice/recorder.py",
            ],
        )

    def test_importing_the_vision_package_loads_no_camera_or_audio_library(
        self,
    ) -> None:
        code = (
            "import sys, steel.vision, steel.vision.capture, steel.vision.frame, "
            "steel.vision.indicator, steel.vision.consent, steel.vision.config, "
            "steel.vision.camera, steel.vision.describe, steel.tools.camera; "
            "bad = [m for m in sys.modules if m.split('.')[0] in "
            "{'cv2', 'PIL', 'sounddevice', 'faster_whisper', 'imageio'} "
            "or m.startswith('openjarvis.speech')]; "
            "print(bad)"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)


class BuildingTheToolTest(base.HomeTestCase):
    def test_building_the_default_tool_opens_nothing_and_contacts_nothing(self) -> None:
        boom = AssertionError("building the tool must not do this")
        with (
            mock.patch.object(subprocess, "Popen", side_effect=boom),
            mock.patch.object(subprocess, "run", side_effect=boom),
            mock.patch.object(socket.socket, "connect", side_effect=boom),
            mock.patch.object(socket, "getaddrinfo", side_effect=boom),
            mock.patch.object(tempfile, "mkstemp", side_effect=boom),
        ):
            tool = get_camera_tool(self.home)
        self.assertEqual(tool.spec.name, "camera_look")

    def test_a_remote_ollama_host_fails_when_the_tool_is_built_not_when_it_looks(
        self,
    ) -> None:
        from steel.vision.describe import VisionUnavailable

        with (
            mock.patch.dict("os.environ", {"OLLAMA_HOST": "http://10.1.2.3:11434"}),
            self.assertRaises(VisionUnavailable),
        ):
            get_camera_tool(self.home)


class DocumentEvidenceTest(unittest.TestCase):
    def test_every_test_the_document_cites_exists(self) -> None:
        doc = (ROOT / "docs" / "VISION.md").read_text(encoding="utf-8")
        evidence = doc.split("## 9. Evidence", 1)[1]
        cited = re.findall(r"`(test_\w+\.py)::(\w+)\.(test_\w+)`", evidence)
        self.assertGreater(len(cited), 100, "section 9 cites too few tests")
        parsed: dict[str, ast.Module] = {}
        for file, cls, name in sorted(set(cited)):
            if file not in parsed:
                parsed[file] = ast.parse((TESTS / file).read_text(encoding="utf-8"))
            classes = [
                n
                for n in ast.walk(parsed[file])
                if isinstance(n, ast.ClassDef) and n.name == cls
            ]
            self.assertTrue(classes, f"{file} has no class {cls}")
            methods = {
                n.name for n in classes[0].body if isinstance(n, ast.FunctionDef)
            }
            self.assertIn(name, methods, f"{file}::{cls}.{name}")

    def test_every_vision_test_is_cited_in_the_document(self) -> None:
        doc = (ROOT / "docs" / "VISION.md").read_text(encoding="utf-8")
        evidence = doc.split("## 9. Evidence", 1)[1]
        missing = []
        for path in sorted(TESTS.glob("test_vision_*.py")):
            if path.name == "test_vision_hygiene.py":
                continue  # this file's own plumbing tests are not claims
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Test"):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name.startswith(
                            "test_"
                        ):
                            key = f"{path.name}::{node.name}.{item.name}"
                            if key not in evidence:
                                missing.append(key)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
