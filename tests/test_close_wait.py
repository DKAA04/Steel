"""Leaving `steel chat` waits a bounded time for fact extraction (fake engine)."""

from __future__ import annotations

import contextlib
import io
import os
import threading
import time
from typing import Any
from unittest import mock

import test_steel as base  # shares the fake engine and temp-home setup

from steel.chat import (
    EXTRACT_WAIT_ENV,
    EXTRACTION_DRAIN_SECONDS,
    extraction_wait_seconds,
    run_repl,
)


class SlowExtractionEngine(base.FakeEngine):
    """Extraction calls block until the test lets them go."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.started = threading.Event()

    def generate(self, messages: Any, *, model: str, **kw: Any) -> dict[str, Any]:
        if messages and messages[0].content == base.EXTRACTOR_PROMPT:
            self.started.set()
            self.release.wait(timeout=20)  # safety net so a failure cannot hang
        return super().generate(messages, model=model, **kw)


class CloseWaitTest(base.HomeTestCase):
    def slow_session(self) -> tuple[Any, SlowExtractionEngine]:
        engine = SlowExtractionEngine()
        # Cleanups run last-in first, so this ends the worker thread before the
        # temporary home is deleted (otherwise it would still be writing there).
        self.addCleanup(self._end_worker, engine)
        session = self.session(engine)
        session.ask("My favourite colour is teal.")
        self.assertTrue(engine.started.wait(timeout=5))
        return session, engine

    @staticmethod
    def _end_worker(engine: SlowExtractionEngine) -> None:
        engine.release.set()
        for thread in threading.enumerate():
            if thread.name == "memory-service":
                thread.join(timeout=10)

    def test_default_wait_is_30_seconds(self) -> None:
        self.assertEqual(EXTRACTION_DRAIN_SECONDS, 30.0)
        with mock.patch.dict(os.environ):
            os.environ.pop(EXTRACT_WAIT_ENV, None)
            self.assertEqual(extraction_wait_seconds(), 30.0)

    def test_env_var_sets_the_wait_and_bad_values_fall_back(self) -> None:
        for raw, expected in [
            ("5", 5.0),
            ("0", 0.0),
            ("0.25", 0.25),
            ("abc", 30.0),
            ("", 30.0),
            ("-3", 30.0),
            ("nan", 30.0),
            ("inf", 30.0),
        ]:
            with mock.patch.dict(os.environ, {EXTRACT_WAIT_ENV: raw}):
                self.assertEqual(extraction_wait_seconds(), expected, raw)

    def test_slow_extractor_makes_close_give_up_and_report_it(self) -> None:
        session, _engine = self.slow_session()
        started = time.monotonic()
        with mock.patch.dict(os.environ, {EXTRACT_WAIT_ENV: "0.3"}):
            finished = session.close()
        self.assertFalse(finished)
        self.assertLess(time.monotonic() - started, 5)  # nowhere near 30 s

    def test_repl_prints_one_plain_line_when_it_gives_up(self) -> None:
        session, _engine = self.slow_session()
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {EXTRACT_WAIT_ENV: "0.3"}),
            mock.patch("builtins.input", side_effect=["/quit"]),
            contextlib.redirect_stdout(out),
        ):
            run_repl(session)
        lines = [ln for ln in out.getvalue().splitlines() if "not saved" in ln]
        self.assertEqual(
            lines,
            ["Fact extraction was still running, so the pending facts were not saved."],
        )

    def test_fast_extraction_closes_cleanly_and_saves(self) -> None:
        session = self.session()
        session.ask("My favourite colour is teal.")
        with mock.patch.dict(os.environ, {EXTRACT_WAIT_ENV: "10"}):
            self.assertTrue(session.close())
        self.assertEqual(
            [f.text for f in base.SteelMemory(self.home).facts.list()],
            ["User's favourite colour is teal"],
        )
        out = io.StringIO()
        session2 = self.session()
        with (
            mock.patch("builtins.input", side_effect=["/quit"]),
            contextlib.redirect_stdout(out),
        ):
            run_repl(session2)
        self.assertNotIn("not saved", out.getvalue())
