"""onnxruntime telemetry must be off before onnxruntime can load (steel/offline.py).

On 2026-10-25 a one-off probe imported onnxruntime without the switch and
recreated ~/.cache/Microsoft/DeveloperTools/.onnxruntime. These tests make
sure no STEEL module or script can do that:

* structurally: no module-level import of onnxruntime or faster_whisper
  (which loads onnxruntime for its voice detection), no openwakeword at all,
  and every function that imports one calls telemetry_off() on an earlier line;
* functionally, in a clean interpreter with the switch unset: a fake
  onnxruntime / faster_whisper records whether the switch was on at the moment
  each real STEEL loader imported it. No real model or library is loaded.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOADED = ("onnxruntime", "faster_whisper")
FORBIDDEN = ("openwakeword",)


def _imports(node: ast.AST) -> list[tuple[int, str]]:
    """(line, module) for every import of a guarded library under *node*."""
    found = []
    for child in ast.walk(node):
        names: list[str] = []
        if isinstance(child, ast.Import):
            names = [alias.name for alias in child.names]
        elif isinstance(child, ast.ImportFrom) and child.module:
            names = [child.module]
        elif (
            isinstance(child, ast.Call)
            and getattr(child.func, "attr", None) == "import_module"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            names = [child.args[0].value]
        for name in names:
            root = name.split(".")[0]
            if root in LOADED + FORBIDDEN:
                found.append((child.lineno, root))
    return found


def _telemetry_lines(node: ast.AST) -> list[int]:
    return [
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and getattr(child.func, "id", getattr(child.func, "attr", None))
        == "telemetry_off"
    ]


def python_files() -> list[Path]:
    return [*(ROOT / "steel").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]


class StructuralTest(unittest.TestCase):
    def test_no_module_level_import_and_no_openwakeword_package(self) -> None:
        problems = []
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for line, module in _imports(tree):
                if module in FORBIDDEN:
                    problems.append(f"{path.name}:{line} imports {module}")
            for statement in tree.body:  # module level, not inside a function
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    for line, module in _imports(statement):
                        problems.append(f"{path.name}:{line} imports {module} at load")
        self.assertEqual(problems, [])

    def test_every_loader_turns_telemetry_off_on_an_earlier_line(self) -> None:
        loaders = []
        problems = []
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # Only imports directly in this function, not in nested ones.
                own = [
                    (line, module)
                    for line, module in _imports(func)
                    if not any(
                        isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and inner is not func
                        and inner.lineno <= line <= (inner.end_lineno or line)
                        for inner in ast.walk(func)
                    )
                ]
                if not own:
                    continue
                name = f"{path.relative_to(ROOT).as_posix()}:{func.name}"
                loaders.append(name)
                first = min(line for line, _ in own)
                if not any(line < first for line in _telemetry_lines(func)):
                    problems.append(name)
        self.assertEqual(problems, [])
        # The scan found the loaders it must find (so it really looks).
        for expected in (
            "steel/jarvis/wakeword.py:_load_sessions",
            "steel/voice/stt.py:_load_whisper",
            "steel/voice/stt.py:_find_speech",
            "scripts/wake_bench.py:main",
        ):
            self.assertIn(expected, loaders)


# A clean interpreter: the switch unset, fake libraries that record whether the
# switch was on at the moment they were imported.
_PRELUDE = """
import importlib.abc, importlib.machinery, json, os, sys, types
os.environ.pop("ORT_DISABLE_TELEMETRY", None)
seen = []

class Fake(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("onnxruntime", "faster_whisper"):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        seen.append([module.__name__, os.environ.get("ORT_DISABLE_TELEMETRY")])
        module.__path__ = []
        module.SessionOptions = lambda: types.SimpleNamespace()
        class Session:
            def __init__(self, *a, **k): pass
            def get_inputs(self): return [types.SimpleNamespace(name="x")]
            def run(self, *a): return [[0.0]]
        module.InferenceSession = Session
        module.WhisperModel = lambda *a, **k: object()
        module.VadOptions = lambda: None
        module.get_speech_timestamps = lambda *a: []

sys.meta_path.insert(0, Fake())
"""

_LOADERS = {
    "wake-word engine": (
        "from pathlib import Path\n"
        "from steel.jarvis.wakeword import _load_sessions\n"
        "_load_sessions(Path('/nonexistent'), 'hey_jarvis_v0.1.onnx')\n"
    ),
    "Whisper": (
        "from pathlib import Path\n"
        "from steel.voice.stt import _load_whisper\n"
        "_load_whisper(Path('/nonexistent'))\n"
    ),
    "voice detection": (
        "from steel.voice.stt import _find_speech\n_find_speech([0.0] * 10)\n"
    ),
    "jarvis_check.py": (
        "import importlib.util\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('jc', {str(ROOT / 'scripts' / 'jarvis_check.py')!r})\n"
        "jc = importlib.util.module_from_spec(spec); spec.loader.exec_module(jc)\n"
        "import steel.jarvis.wakeword as ww\n"
        "class Engine:\n"
        "    def __init__(self, directory):\n"
        "        ww._load_sessions(directory, 'hey_jarvis_v0.1.onnx')\n"
        "ww.OpenWakeWord = Engine\n"
        "jc.load_engine(Path('/nonexistent'))\n"
    ),
}


class FunctionalTest(unittest.TestCase):
    def test_each_loader_imports_its_library_only_with_the_switch_on(self) -> None:
        for name, body in _LOADERS.items():
            with self.subTest(loader=name):
                code = _PRELUDE + body + "print(json.dumps(seen))\n"
                done = subprocess.run(
                    [sys.executable, "-I", "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                seen = json.loads(done.stdout.strip().splitlines()[-1])
                self.assertTrue(seen, "the loader imported nothing")
                for module, switch in seen:
                    self.assertEqual(switch, "1", f"{name}: {module} imported first")

    def test_the_steel_command_turns_it_off_before_anything_else(self) -> None:
        code = (
            "import os\n"
            "os.environ.pop('ORT_DISABLE_TELEMETRY', None)\n"
            "from unittest import mock\n"
            "import steel.cli as cli\n"
            "with mock.patch.object(cli, 'check_openjarvis_pin'):\n"
            "    cli._prepare()\n"
            "print(os.environ.get('ORT_DISABLE_TELEMETRY'))\n"
        )
        done = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
