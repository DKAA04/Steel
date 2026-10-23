"""SteelFactExtractor: user-only input and the not-a-fact filter (no live model)."""

from __future__ import annotations

import json
import unittest
from typing import Any

from steel.extractor import SYSTEM_PROMPT, SteelFactExtractor, keep_fact


class Recorder:
    """Fake engine that returns canned JSON and records what it was sent."""

    def __init__(self, reply: list[str]) -> None:
        self.reply = reply
        self.sent: list[Any] = []

    def generate(self, messages: Any, **_: Any) -> dict[str, Any]:
        self.sent = list(messages)
        return {"content": json.dumps(self.reply)}


class ExtractorTest(unittest.TestCase):
    def test_assistant_text_is_never_sent_even_if_passed(self) -> None:
        engine = Recorder(["User likes tea"])
        extractor = SteelFactExtractor(engine, "m")
        extractor.extract("I like tea", "SECRET assistant reply")
        self.assertEqual(engine.sent[0].content, SYSTEM_PROMPT)
        self.assertEqual(engine.sent[1].content, "User: I like tea")
        self.assertNotIn("SECRET", "".join(m.content for m in engine.sent))

    def test_answers_to_the_user_are_filtered_out(self) -> None:
        answers = [
            "I am an AI assistant.",
            "I'm doing well, thank you for asking.",
            "You asked to be reminded to buy milk tomorrow.",
            "The user asked for a math calculation.",
            "User asked about photosynthesis",
            "El usuario preguntó cuántos días tiene febrero.",
            "Yo estoy bien, gracias.",
        ]
        engine = Recorder(
            answers + ["User is a carpenter", "El usuario vive en Sevilla."]
        )
        kept = SteelFactExtractor(engine, "m").extract("hello")
        self.assertEqual(kept, ["User is a carpenter", "El usuario vive en Sevilla."])

    def test_known_miss_an_answer_without_a_pronoun_passes_the_filter(self) -> None:
        # Documented limit: only the prompt stops this one, not the filter.
        engine = Recorder(["17 * 23 = 391"])
        self.assertEqual(
            SteelFactExtractor(engine, "m").extract("x"), ["17 * 23 = 391"]
        )

    def test_keep_fact_accepts_normal_facts(self) -> None:
        for fact in [
            "User is vegetarian",
            "The user has a sister named Mara.",
            "Lucía vive en Valencia.",
            "Itzel works nights",  # starts with 'I' but is a name, not a pronoun
            "Youssef lives in Leeds",  # starts with 'You' but is a name
        ]:
            self.assertTrue(keep_fact(fact), fact)


if __name__ == "__main__":
    unittest.main()
