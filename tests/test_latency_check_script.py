"""scripts/latency_check.py, without a model or Ollama: only its pure parts.

The live run is the owner's; nothing here calls a model or the ollama CLI.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from steel.timings import ModelTiming

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "latency_check.py"
PS = (
    "NAME          ID              SIZE      PROCESSOR          CONTEXT    "
    "RUNNER    UNTIL\n"
    "qwen3.5:2b    0689d44085e0    3.4 GB    48%/52% CPU/GPU    16384      "
    "llama     4 minutes from now\n"
    "qwen2.5vl:3b  fb90415cde1e    4.1 GB    100% CPU           4096       "
    "llama     2 minutes from now\n"
)


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("latency_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LatencyCheckScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_script()

    def test_the_processor_column_is_read_for_the_chat_model(self) -> None:
        self.assertEqual(self.script.processor_of(PS, "qwen3.5:2b"), "48%/52% CPU/GPU")
        self.assertEqual(self.script.processor_of(PS, "qwen2.5vl:3b"), "100% CPU")
        self.assertEqual(self.script.processor_of(PS, "llama3"), "")
        self.assertEqual(self.script.processor_of("", "qwen3.5:2b"), "")
        self.assertEqual(self.script.processor_of("Error: refused", "x"), "")

    def test_the_context_and_the_model_size_are_read_from_ollama_ps(self) -> None:
        for column, first, second in (
            ("CONTEXT", "16384", "4096"),
            ("SIZE", "3.4 GB", "4.1 GB"),
            ("PROCESSOR", "48%/52% CPU/GPU", "100% CPU"),
        ):
            got = (
                self.script.ps_column(PS, "qwen3.5:2b", column),
                self.script.ps_column(PS, "qwen2.5vl:3b", column),
            )
            self.assertEqual(got, (first, second), column)
        self.assertEqual(self.script.ps_column(PS, "llama3", "CONTEXT"), "")
        self.assertEqual(self.script.ps_column("", "x", "CONTEXT"), "")
        timing = ModelTiming(total=1.0)
        row = self.script.row("warm 1", timing, "100% CPU", 5, "4096", "2.6 GB")
        self.assertEqual((row["context"], row["model_ram"]), ("4096", "2.6 GB"))
        row = self.script.row("warm 1", timing, "", 5)
        self.assertEqual((row["context"], row["model_ram"]), ("not listed",) * 2)

    def test_a_result_row_has_numbers_and_the_reply_length_never_its_text(
        self,
    ) -> None:
        timing = ModelTiming(total=3.0, load=1.0, prompt=0.5, generate=1.0, tokens=12)
        row = self.script.row("cold", timing, "100% CPU", 42)
        self.assertEqual(row["first_token_s"], 1.5)
        self.assertEqual(row["tokens_per_s"], 12.0)
        self.assertEqual(row["reply_chars"], 42)
        self.assertTrue(
            all(not isinstance(v, str) or len(v) < 20 for v in row.values())
        )

    def test_the_prompts_are_fixed_short_and_impersonal(self) -> None:
        self.assertEqual(len(self.script.PROMPTS), 3)
        for prompt in self.script.PROMPTS:
            self.assertLess(len(prompt), 80)
            self.assertNotIn("{", prompt)

    def test_it_plays_nothing_keeps_memory_off_and_stays_in_the_scratch_home(
        self,
    ) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]  # after the docstring
        for banned in ("aplay", "steel.speak", "microphone", "camera", "shell=True"):
            self.assertNotIn(banned, code)
        self.assertIn("config.memory.enabled = False", code)
        # The scratch home is set before OpenJarvis reads any path.
        self.assertLess(
            code.index('os.environ["OPENJARVIS_HOME"]'),
            code.index("from openjarvis"),
        )

    def test_without_a_scratch_directory_it_stops_before_contacting_anything(
        self,
    ) -> None:
        with (
            mock.patch("sys.argv", ["latency_check.py"]),
            mock.patch.object(self.script.subprocess, "run") as run,
            self.assertRaises(SystemExit),
        ):
            self.script.main()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
