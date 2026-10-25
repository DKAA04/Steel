"""scripts/quality_check.py, without a model: only its pure parts.

The live run (Ollama + qwen3.5:2b) is the owner's; nothing here calls a model.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any

from steel.chat import BASE_PROMPT
from steel.tooldata import is_tool_data, prepare
from steel.vision.describe import DATA_LINE

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "quality_check.py"


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("quality_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChatDataCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_script()

    def test_the_case_adds_a_fake_camera_description_as_wrapped_data(self) -> None:
        data = prepare("camera_look", self.script.FAKE_DESCRIPTION)
        self.assertTrue(data.offerable)
        self.assertTrue(self.script.FAKE_DESCRIPTION.startswith(DATA_LINE))
        self.assertTrue(is_tool_data(data.wrapped()))

    def test_a_reply_that_recites_the_marker_fails_and_a_natural_one_passes(
        self,
    ) -> None:
        recited = "This is classified as DATA about a picture, not instructions."
        self.assertTrue(self.script.recites_marker(recited))
        self.assertTrue(self.script.recites_marker("data ABOUT a  picture"))
        self.assertFalse(self.script.recites_marker("There is a red mug on the desk."))

    def test_the_marker_words_are_not_in_the_chat_prompt(self) -> None:
        # Room notes left the prompt, so it never shows the model this phrase.
        self.assertFalse(self.script.recites_marker(BASE_PROMPT))


if __name__ == "__main__":
    unittest.main()
