"""Only the two microphone modules may start a microphone program.

Structural check (ast) over steel/ and scripts/: every string constant that
names a capture program, and every use of subprocess (or another way to start
a program), must be in an explicit allowlist below, each with its reason. A new
file fails with a message telling its author to review it first.

What this is: a guard against mistakes and against a quiet new code path (a new
module that runs `arecord` on its own, without the consent, the grant and the
indicators). What it is not: protection against code written to hide what it
does (`"are" + "cord"`, `getattr(subprocess, ...)`); a review catches that.
"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Programs that capture sound from a microphone.
CAPTURE_PROGRAMS = re.compile(r"arecord|pw-record|pw-cat|parecord|\bparec\b")
# Audio libraries that could open a microphone from inside the process.
AUDIO_LIBRARIES = {"sounddevice", "pyaudio", "alsaaudio", "soundcard", "pyaudiowpatch"}
# Ways to start another program.
LAUNCHERS = {
    "subprocess": {"Popen", "run", "call", "check_output", "check_call"},
    "os": {"system", "popen", "execv", "execvp", "execl", "execlp", "posix_spawn"},
    "pty": {"spawn"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}

# Files whose strings name a capture program, and why that is allowed.
MENTIONS_CAPTURE_PROGRAM = {
    "steel/voice/mic.py": "STARTS arecord for push-to-talk (ArecordSource); opens only "
    "with the REC-line proof (docs/VOICE.md 6.1)",
    "steel/jarvis/source.py": "STARTS arecord for the Jarvis loop (WakeSource, "
    "RequestSource) through setpriv; WakeSource opens only with the "
    "ListeningShown proof (docs/JARVIS.md 3b, 3c)",
    "steel/voice/commands.py": "looks for the program with shutil.which and prints "
    "its name (steel voice check); starts nothing",
    "steel/jarvis/commands.py": "looks for the program with `which` and prints its "
    "name (steel jarvis check, preflight); starts nothing",
    "steel/jarvis/procs.py": "the process name the guard and `steel jarvis stop` "
    "look for in /proc in order to KILL it, and the pkill hint; starts nothing",
    "scripts/jarvis_check.py": "looks for the program with shutil.which; starts "
    "nothing (the microphone is opened by the real loop parts)",
}
# Files that start another program, and why that is allowed.
STARTS_PROGRAMS = {
    "steel/voice/mic.py": "arecord (push-to-talk microphone)",
    "steel/jarvis/source.py": "setpriv + arecord (Jarvis microphones)",
    "steel/jarvis/indicators.py": "the guard process (python -m steel.jarvis.guard)",
    "steel/vision/camera.py": "ffmpeg (the camera, one picture; tested in "
    "test_vision_hygiene.py)",
    "steel/speak/player.py": "aplay (the speaker)",
    "steel/speak/synth.py": "Piper in a network namespace (speech synthesis)",
    "scripts/latency_check.py": "ollama ps (the owner's live latency check)",
    "scripts/speak_setup.py": "venv and pip for the Piper engine (the owner's setup)",
}
# Of the two lists, the files that do BOTH (name a capture program AND start a
# program) are the only ones that may open a microphone.
MAY_START_A_MICROPHONE = {"steel/voice/mic.py", "steel/jarvis/source.py"}


def python_files() -> list[Path]:
    return [
        *sorted((ROOT / "steel").rglob("*.py")),
        *sorted((ROOT / "scripts").glob("*.py")),
    ]


def _docstring_ids(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def scan(path: Path) -> tuple[list[int], list[int], list[int]]:
    """Lines with a capture-program string, a program launch, an audio library."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_ids(tree)
    names, launches, libraries = [], [], []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
            and CAPTURE_PROGRAMS.search(node.value)
        ):
            names.append(node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                root = module.split(".")[0]
                if root in ("subprocess", "pty"):
                    launches.append(node.lineno)
                if root in AUDIO_LIBRARIES:
                    libraries.append(node.lineno)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.attr in LAUNCHERS.get(node.value.id, set())
        ):
            launches.append(node.lineno)
    return names, launches, libraries


class MicrophoneProgramTest(unittest.TestCase):
    def results(self) -> dict[str, tuple[list[int], list[int], list[int]]]:
        return {p.relative_to(ROOT).as_posix(): scan(p) for p in python_files()}

    def test_only_listed_files_name_a_capture_program(self) -> None:
        found = {f for f, (names, _, _) in self.results().items() if names}
        new = sorted(found - set(MENTIONS_CAPTURE_PROGRAM))
        self.assertEqual(
            new,
            [],
            f"{new} name a microphone capture program (arecord, pw-record, ...). "
            "A new place that can capture sound needs a review against "
            "docs/JARVIS.md section 3 and docs/VOICE.md section 7 (consent, exact "
            "grant, both indicators). Review it, then add it to "
            "MENTIONS_CAPTURE_PROGRAM in tests/test_microphone_programs.py "
            "with the reason.",
        )
        self.assertEqual(
            sorted(set(MENTIONS_CAPTURE_PROGRAM) - found),
            [],
            "an allowlisted file no longer names a capture program: remove it",
        )

    def test_only_listed_files_start_a_program(self) -> None:
        found = {f for f, (_, launches, _) in self.results().items() if launches}
        new = sorted(found - set(STARTS_PROGRAMS))
        self.assertEqual(
            new,
            [],
            f"{new} start another program (subprocess, os.system, ...). Review "
            "what it starts (a microphone program must come only from "
            "steel/voice/mic.py or steel/jarvis/source.py), then add it to "
            "STARTS_PROGRAMS in tests/test_microphone_programs.py with the reason.",
        )
        self.assertEqual(
            sorted(set(STARTS_PROGRAMS) - found),
            [],
            "an allowlisted file no longer starts a program: remove it",
        )

    def test_only_the_two_microphone_modules_both_name_and_start_a_capture_program(
        self,
    ) -> None:
        both = sorted(
            f
            for f, (names, launches, _) in self.results().items()
            if names and launches
        )
        self.assertEqual(both, sorted(MAY_START_A_MICROPHONE))

    def test_no_audio_library_that_could_open_a_microphone_is_imported(self) -> None:
        found = {f: lines for f, (_, _, lines) in self.results().items() if lines}
        self.assertEqual(found, {})

    def test_the_scan_really_finds_things(self) -> None:
        # A guard that scans nothing would pass everything.
        results = self.results()
        self.assertTrue(results["steel/voice/mic.py"][0])
        self.assertTrue(results["steel/jarvis/source.py"][1])

    def scan_text(self, text: str) -> tuple[list[int], list[int], list[int]]:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.py"
            sample.write_text(text, encoding="utf-8")
            return scan(sample)

    def test_the_scan_catches_a_new_microphone_launcher(self) -> None:
        names, launches, _ = self.scan_text(
            'import subprocess\nsubprocess.Popen(["arecord", "-q"])\n'
        )
        self.assertEqual((len(names), len(launches)), (1, 2))  # string; import + use

    def test_the_scan_catches_other_launchers_and_audio_libraries(self) -> None:
        _, launches, libraries = self.scan_text(
            "import os\nimport sounddevice\nos.system('pw-record x')\n"
        )
        self.assertEqual((len(launches), len(libraries)), (1, 1))

    def test_docstrings_and_comments_may_mention_the_program(self) -> None:
        names, launches, _ = self.scan_text(
            '"""Uses arecord."""\n# arecord here\nx = 1\n'
        )
        self.assertEqual((names, launches), ([], []))


if __name__ == "__main__":
    unittest.main()
