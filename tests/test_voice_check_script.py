"""scripts/voice_check.py, run with fakes. The live run with a real microphone is
the owner's (see the script's docstring); nothing here opens one."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import test_steel as base
from voice_helpers import FakeSource, FakeTTY, quiet, tone

from steel.voice.indicator import Indicator

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "voice_check.py"


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("voice_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class Info:
    language = "en"


class Model:
    def __init__(self, text: str) -> None:
        self.text = text
        self.heard: list[np.ndarray] = []

    def transcribe(self, samples: Any, **_: Any) -> tuple[list[Segment], Info]:
        self.heard.append(samples.copy())
        return [Segment(self.text)], Info()

    def detect_language(self, samples: Any) -> tuple[str, float, list]:
        return "en", 1.0, [("en", 1.0)]


class PushToTalk:
    def __init__(self, start: bool = True) -> None:
        self.start = start

    def wait_for_start(self, timeout: float) -> bool:
        return self.start

    def stop_pressed(self) -> bool:
        return False


class ScriptTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.script = load_script()
        self.models_home = self.home.parent / "models-home"
        for name in ("small", "base"):
            (self.models_home / "steel_models" / f"faster-whisper-{name}").mkdir(
                parents=True
            )
        self.lines: list[str] = []
        self.models = {"small": Model("Hola, qué tal"), "base": Model("hola que tal")}
        self.scratches: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*a: Any, **k: Any) -> str:
            path = real_mkdtemp(*a, **k)
            self.scratches.append(Path(path))
            return path

        patcher = mock.patch.object(tempfile, "mkdtemp", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.screen = FakeTTY()
        self.sources: list[FakeSource] = []

    def open_source(self) -> FakeSource:
        source = FakeSource(tone(2.0, 4000) + quiet(10), screen=self.screen)
        self.sources.append(source)
        return source

    def run_script(self, *argv: str, **overrides: Any) -> int:
        options: dict[str, Any] = {
            "models_home": self.models_home,
            "ask": lambda _prompt: "yes",
            "is_terminal": lambda: True,
            "open_source": self.open_source,
            "push_to_talk": PushToTalk(),
            "indicator": Indicator(self.screen),
            "whisper_options": {
                "load_model": lambda path: self.models[path.name.split("-")[-1]],
                "find_speech": lambda _s: True,
            },
            "echo": self.lines.append,
        }
        options.update(overrides)
        return self.script.run(list(argv) or ["--seconds", "3"], **options)

    def output(self) -> str:
        return "\n".join(self.lines)

    def real_home_listing(self) -> dict[str, int]:
        return {p.name: p.stat().st_mtime_ns for p in self.home.rglob("*")}

    def test_it_records_transcribes_prints_times_and_deletes_the_scratch(self) -> None:
        before = self.real_home_listing()
        self.assertEqual(self.run_script(), 0, self.output())
        out = self.output()
        self.assertIn("[small] transcript: Hola, qué tal", out)
        self.assertIn("model load", out)
        self.assertIn("transcription", out)
        self.assertIn("whole call", out)
        self.assertIn("loudest 100 ms level: 4000", out)
        self.assertIn("● REC", self.sources[0].screen_at_open or "")
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())
        self.assertIn("scratch directory deleted", out)
        self.assertIn("no audio was written", out)
        self.assertEqual(self.real_home_listing(), before)  # the real home is untouched

    def test_the_scratch_holds_only_consent_grants_and_the_decision_log(self) -> None:
        self.run_script()
        held = self.output().split("it held only: ")[1].split(";")[0]
        self.assertEqual(
            sorted(held.split(", ")),
            [
                "steel_policy.json",
                "steel_policy_audit.jsonl",
                "steel_voice_consent.json",
            ],
        )

    def test_it_never_writes_a_consent_or_grants_file_into_the_real_home(self) -> None:
        self.run_script()
        for name in ("steel_policy.json", "steel_voice_consent.json"):
            self.assertFalse((self.home / name).exists(), name)
            self.assertFalse((self.models_home / name).exists(), name)

    def test_several_models_hear_the_same_audio(self) -> None:
        self.assertEqual(self.run_script("--seconds", "3", "--models", "small,base"), 0)
        out = self.output()
        self.assertIn("[small] transcript: Hola, qué tal", out)
        self.assertIn("[base] transcript: hola que tal", out)
        first, second = self.models["small"].heard[0], self.models["base"].heard[0]
        self.assertTrue(first.any())
        np.testing.assert_array_equal(first, second)

    def test_recording_lasts_the_requested_seconds(self) -> None:
        self.assertEqual(self.run_script("--seconds", "2"), 0)
        self.assertIn("recorded 2.0 s (stopped by: max_length)", self.output())

    def test_anything_but_yes_records_nothing_and_makes_no_scratch(self) -> None:
        for answer in ("no", "", "y"):
            self.lines.clear()
            self.assertEqual(self.run_script(ask=lambda _p, a=answer: a), 1)
            self.assertIn("Nothing was recorded.", self.output())
        self.assertEqual(self.scratches, [])
        self.assertEqual(self.sources, [])

    def test_without_a_terminal_nothing_is_recorded(self) -> None:
        self.assertEqual(self.run_script(is_terminal=lambda: False), 1)
        self.assertEqual((self.scratches, self.sources), ([], []))

    def test_a_missing_model_stops_before_asking_for_consent(self) -> None:
        asked: list[str] = []
        code = self.run_script(
            "--models", "medium", ask=lambda p: asked.append(p) or "yes"
        )
        self.assertEqual(code, 1)
        self.assertIn("not found", self.output())
        self.assertEqual((asked, self.scratches, self.sources), ([], [], []))

    def test_a_failed_run_still_deletes_the_scratch(self) -> None:
        self.assertEqual(self.run_script(push_to_talk=PushToTalk(start=False)), 1)
        self.assertIn("Not run:", self.output())
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())
        self.assertEqual(self.sources, [])  # no start key, no microphone

    def test_ctrl_c_still_deletes_the_scratch(self) -> None:
        with mock.patch(
            "steel.voice.commands.listen_once", side_effect=KeyboardInterrupt
        ):
            self.assertEqual(self.run_script(), 130)
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())

    def test_bad_arguments_are_refused(self) -> None:
        for argv in (["--seconds", "0"], ["--seconds", "500"], ["--models", ""]):
            with self.assertRaises(SystemExit, msg=argv):
                self.script.run(argv)
        self.assertEqual(self.scratches, [])

    def test_the_script_has_no_network_or_file_writing_calls(self) -> None:
        text = SCRIPT.read_text()
        for word in (
            "socket",
            "urllib",
            "requests",
            "httpx",
            "sounddevice",
            "NamedTemporaryFile",
            "mkstemp",
        ):
            self.assertNotIn(word, text, word)

    def test_the_environment_is_the_temporary_one(self) -> None:
        self.assertEqual(os.environ["OPENJARVIS_HOME"], str(self.home))


if __name__ == "__main__":
    unittest.main()
