"""scripts/vision_check.py, run with fakes. The live run with a real camera is
the owner's (see the script's docstring); nothing here opens one."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from vision_helpers import FakeDescriber, FakeFrameSource, decode_png, make_frame
from voice_helpers import FakeTTY

from steel.vision.indicator import CameraIndicator

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vision_check.py"


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("vision_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.script = load_script()
        self.lines: list[str] = []
        self.describers = {
            "qwen2.5vl:3b": FakeDescriber("A desk with a mug."),
            "qwen3.5:2b": FakeDescriber("A table, a red cup."),
        }
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
        self.sources: list[FakeFrameSource] = []
        self.installed = ["qwen2.5vl:3b", "qwen3.5:2b"]

    def open_source(self, warmup: int = 0) -> FakeFrameSource:
        source = FakeFrameSource(make_frame(640, 480), screen=self.screen)
        self.sources.append(source)
        return source

    def run_script(self, *argv: str, **overrides: Any) -> int:
        options: dict[str, Any] = {
            "ask": lambda _prompt: "yes",
            "is_terminal": lambda: True,
            "open_source": self.open_source,
            "indicator": CameraIndicator(self.screen),
            "list_models": lambda _host: self.installed,
            "describer_for": lambda _config, name: self.describers[name],
            "echo": self.lines.append,
        }
        options.update(overrides)
        return self.script.run(list(argv), **options)

    def output(self) -> str:
        return "\n".join(self.lines)

    def real_home_listing(self) -> dict[str, int]:
        return {p.name: p.stat().st_mtime_ns for p in self.home.rglob("*")}

    def test_it_looks_prints_times_and_deletes_the_scratch(self) -> None:
        before = self.real_home_listing()
        self.assertEqual(self.run_script(), 0, self.output())
        out = self.output()
        # The default model is the chat model, qwen3.5:2b.
        self.assertIn("[qwen3.5:2b] description: A table, a red cup.", out)
        self.assertNotIn("qwen2.5vl:3b", out)
        self.assertRegex(out, r"\[qwen3.5:2b\] \d+\.\d s")
        self.assertIn("camera open for", out)
        self.assertIn("448x336", out)
        self.assertIn("whole call", out)
        self.assertIn("● CAMERA OPEN", self.sources[0].screen_at_open or "")
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())
        self.assertIn("scratch directory deleted", out)
        self.assertIn("no picture was written", out)
        self.assertEqual(self.real_home_listing(), before)  # the real home is untouched

    def test_the_scratch_holds_only_consent_grants_and_the_decision_log(self) -> None:
        self.run_script()
        held = self.output().split("it held only: ")[1].split(";")[0]
        self.assertEqual(
            sorted(held.split(", ")),
            [
                "steel_camera_consent.json",
                "steel_policy.json",
                "steel_policy_audit.jsonl",
            ],
        )

    def test_it_never_writes_a_consent_or_grants_file_into_the_real_home(self) -> None:
        self.run_script()
        for name in ("steel_policy.json", "steel_camera_consent.json"):
            self.assertFalse((self.home / name).exists(), name)

    def test_several_models_see_the_same_picture(self) -> None:
        self.assertEqual(self.run_script("--models", "qwen2.5vl:3b,qwen3.5:2b"), 0)
        out = self.output()
        self.assertIn("[qwen2.5vl:3b] description: A desk with a mug.", out)
        self.assertIn("[qwen3.5:2b] description: A table, a red cup.", out)
        first = self.describers["qwen2.5vl:3b"].calls[0][0]
        second = self.describers["qwen3.5:2b"].calls[0][0]
        self.assertEqual(first, second)
        self.assertEqual(decode_png(first)[:2], (448, 336))
        self.assertEqual(len(self.sources), 1)  # one picture, not one per model
        self.assertEqual(self.sources[0].reads, 1)

    def test_a_failing_model_is_reported_and_the_others_still_answer(self) -> None:
        self.describers["qwen2.5vl:3b"].fail = RuntimeError("secret detail")
        self.assertEqual(self.run_script("--models", "qwen2.5vl:3b,qwen3.5:2b"), 0)
        out = self.output()
        self.assertIn("[qwen2.5vl:3b] FAILED", out)
        self.assertIn("(RuntimeError)", out)
        self.assertNotIn("secret detail", out)
        self.assertIn("[qwen3.5:2b] description: A table, a red cup.", out)

    def test_every_model_failing_is_a_failed_run_and_still_deletes_the_scratch(
        self,
    ) -> None:
        for describer in self.describers.values():
            describer.fail = RuntimeError("down")
        self.assertEqual(self.run_script(), 1)
        self.assertIn("Not run:", self.output())
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())

    def test_the_question_reaches_the_model(self) -> None:
        self.run_script("--question", "is the desk tidy?")
        self.assertEqual(self.describers["qwen3.5:2b"].calls[0][1], "is the desk tidy?")

    def test_anything_but_yes_takes_no_picture_and_makes_no_scratch(self) -> None:
        for answer in ("no", "", "y"):
            self.lines.clear()
            self.assertEqual(self.run_script(ask=lambda _p, a=answer: a), 1)
            self.assertIn("No picture was taken.", self.output())
        self.assertEqual((self.scratches, self.sources), ([], []))

    def test_without_a_terminal_no_picture_is_taken(self) -> None:
        self.assertEqual(self.run_script(is_terminal=lambda: False), 1)
        self.assertEqual((self.scratches, self.sources), ([], []))

    def test_a_missing_model_stops_before_asking_for_consent(self) -> None:
        asked: list[str] = []
        code = self.run_script(
            "--models", "llava:7b", ask=lambda p: asked.append(p) or "yes"
        )
        self.assertEqual(code, 1)
        self.assertIn("not installed", self.output())
        self.assertEqual((asked, self.scratches, self.sources), ([], [], []))

    def test_an_ollama_that_does_not_answer_stops_before_consent(self) -> None:
        def down(_host: str) -> list[str]:
            raise ConnectionError("refused")

        self.assertEqual(self.run_script(list_models=down), 1)
        self.assertIn("Cannot reach Ollama", self.output())
        self.assertEqual((self.scratches, self.sources), ([], []))

    def test_a_camera_that_fails_still_deletes_the_scratch(self) -> None:
        def broken(_warmup: int) -> FakeFrameSource:
            return FakeFrameSource(fail_read=OSError("busy"))

        self.assertEqual(self.run_script(open_source=broken), 1)
        self.assertIn("Not run:", self.output())
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())

    def test_ctrl_c_still_deletes_the_scratch(self) -> None:
        with mock.patch(
            "steel.vision.commands.look_once", side_effect=KeyboardInterrupt
        ):
            self.assertEqual(self.run_script(), 130)
        (scratch,) = self.scratches
        self.assertFalse(scratch.exists())

    def test_bad_arguments_are_refused(self) -> None:
        for argv in (
            ["--models", ""],
            ["--models", "../evil"],
            ["--models", "a b"],
            ["--question", ""],
            ["--question", "x" * 300],
        ):
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
            "cv2",
            "NamedTemporaryFile",
            "mkstemp",
            "subprocess",
        ):
            self.assertNotIn(word, text, word)


if __name__ == "__main__":
    unittest.main()
