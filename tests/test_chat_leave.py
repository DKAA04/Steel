"""Leaving steel chat: /quit, a bare "exit" or "quit", or Ctrl-D.

Fake engine, temporary home, scripted input. No model, microphone or camera.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any
from unittest import mock

import test_steel as base

from steel.chat import LEAVE_HINT, is_leave_line, run_repl


class LeaveTest(base.HomeTestCase):
    def repl(self, lines: list[Any]) -> tuple[str, base.FakeEngine]:
        engine = base.FakeEngine()
        session = self.session(engine)
        self.addCleanup(session.close, 5)
        out = io.StringIO()
        with (
            mock.patch("builtins.input", side_effect=lines),
            mock.patch.dict("os.environ", {"STEEL_EXTRACT_WAIT_SECONDS": "5"}),
            contextlib.redirect_stdout(out),
        ):
            run_repl(session)
        return out.getvalue(), engine

    def test_a_bare_exit_or_quit_in_any_case_leaves(self) -> None:
        for word in ("exit", "quit", "EXIT", "Quit", "  exit  "):
            with self.subTest(word=word):
                # Nothing after the word: input() would raise if it were asked.
                out, engine = self.repl([word])
                self.assertIn("Saving memory", out)
                self.assertEqual(engine.chat_prompts, [])

    def test_exit_with_anything_else_on_the_line_goes_to_the_model(self) -> None:
        for line in ("exit now", "quit?", "exit.", "how do I quit vim"):
            with self.subTest(line=line):
                _, engine = self.repl([line, "/quit"])
                self.assertEqual(len(engine.chat_prompts), 1)

    def test_slash_quit_and_ctrl_d_still_leave(self) -> None:
        out, engine = self.repl(["/quit"])
        self.assertEqual(engine.chat_prompts, [])
        out, engine = self.repl([EOFError])  # what input() raises on Ctrl-D
        self.assertIn("Saving memory", out)

    def test_the_banner_names_the_three_ways_to_leave(self) -> None:
        out, _ = self.repl(["/quit"])
        self.assertIn(LEAVE_HINT, out.splitlines()[0])
        for way in ("/quit", "exit", "quit", "Ctrl-D"):
            self.assertIn(way, LEAVE_HINT)

    def test_only_the_exact_words_count(self) -> None:
        yes: list[Any] = ["exit", "QUIT", "/quit", "/exit"]
        no: list[Any] = ["", "exit!", "e xit", "quit it", "exiting", "/quit now"]
        self.assertTrue(all(is_leave_line(x) for x in yes))
        self.assertFalse(any(is_leave_line(x) for x in no))
