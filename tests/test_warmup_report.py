"""Phase 5b-3: the warm-up made visible with --timings (docs/LATENCY.md section 10).

Fake engines, a fake clock, an httpx.MockTransport in place of Ollama. No model,
Ollama or network is used.
"""

from __future__ import annotations

import contextlib
import io
import itertools
from typing import Any
from unittest import mock

import httpx
import test_steel as base
from openjarvis.engine.ollama import OllamaEngine
from test_latency import USER_TEXT, FakeClock, TimedEngine, no_network

from steel.chat import run_repl
from steel.timings import model_timing, timing_line
from steel.warm import WarmUp, failure_code, start_warmup


def ollama_engine(handler: Any) -> OllamaEngine:
    host = "http://127.0.0.1:11434"
    engine = OllamaEngine(host)
    engine._client = httpx.Client(base_url=host, transport=httpx.MockTransport(handler))
    return engine


def ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": "."}, "done": True})


class WarmUpOutcomeTest(base.HomeTestCase):
    def run_warm(self, handler: Any) -> WarmUp:
        ticks = itertools.chain([100.0, 104.24], itertools.repeat(110.0))
        with no_network():
            warm = start_warmup(
                ollama_engine(handler), "fake", clock=lambda: next(ticks)
            )
            warm.join(5)
        return warm

    def test_a_finished_warm_up_says_how_long_it_took(self) -> None:
        warm = self.run_warm(ok)
        self.assertEqual(warm.status, "done")
        self.assertEqual(warm.line(), "warm-up: done in 4.2 s")
        self.assertEqual(warm.finished_at, 104.24)

    def test_a_failed_warm_up_gives_a_reason_code_and_never_text(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("secret words from the server", request=request)

        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("secret words from the server", request=request)

        def server_error(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="secret words from the server")

        for handler, code in (
            (timeout, "timeout"),
            (refused, "connection"),
            (server_error, "http-500"),
        ):
            warm = self.run_warm(handler)
            self.assertEqual(warm.line(), f"warm-up: failed ({code})")
            self.assertNotIn("secret", warm.line())
        self.assertEqual(failure_code(ValueError("x")), "error")
        self.assertEqual(failure_code(RuntimeError("Ollama returned 7")), "error")

    def test_a_warm_up_that_was_not_started_says_why(self) -> None:
        warm = start_warmup(base.FakeEngine(), "fake")
        self.assertEqual(warm.line(), "warm-up: skipped (not-local)")
        remote = ollama_engine(ok)
        remote._host = "http://192.0.2.10:11434"
        self.assertEqual(
            start_warmup(remote, "fake").line(), "warm-up: skipped (not-local)"
        )

    def test_the_warm_up_thread_prints_nothing_itself(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.run_warm(ok)
            self.run_warm(lambda r: httpx.Response(500))
        self.assertEqual(out.getvalue(), "")  # lines come only between turns


class WarmUpInTheChatTest(base.HomeTestCase):
    def chat(
        self,
        warm: WarmUp | None,
        *,
        timings: bool,
        engine: Any = None,
        clock: FakeClock | None = None,
    ) -> tuple[str, list[str], Any]:
        clock = clock or FakeClock()
        session = self.session(engine or TimedEngine(clock, "Noted."))
        session.clock = clock
        session.warmup = warm
        notices: list[str] = []
        session.notice = notices.append
        self.addCleanup(session.close, 5)
        out = io.StringIO()
        with (
            mock.patch(
                "builtins.input", side_effect=[USER_TEXT, "second one", "/quit"]
            ),
            mock.patch.dict("os.environ", {"STEEL_EXTRACT_WAIT_SECONDS": "5"}),
            contextlib.redirect_stdout(out),
        ):
            run_repl(session, None, None, timings=timings)
        return out.getvalue(), notices, session

    def test_the_warm_up_line_is_printed_once_between_turns_with_timings(self) -> None:
        warm = WarmUp(status="done", seconds=4.2, finished_at=1.0)
        out, notices, _ = self.chat(warm, timings=True)
        lines = out.splitlines()
        self.assertEqual(lines.count("warm-up: done in 4.2 s"), 1)
        at = lines.index("warm-up: done in 4.2 s")
        self.assertTrue(lines[at + 1].startswith("steel>"))  # before the first reply
        self.assertEqual(notices, [])

    def test_without_timings_nothing_about_the_warm_up_is_printed(self) -> None:
        out, notices, _ = self.chat(WarmUp(status="done", seconds=4.2), timings=False)
        self.assertNotIn("warm-up", out)
        self.assertEqual(notices, [])

    def test_a_failed_or_skipped_warm_up_is_reported_the_same_way(self) -> None:
        out, _, _ = self.chat(WarmUp(status="failed", reason="timeout"), timings=True)
        self.assertEqual(out.splitlines().count("warm-up: failed (timeout)"), 1)
        out, _, _ = self.chat(
            WarmUp(status="skipped", reason="not-local"), timings=True
        )
        self.assertEqual(out.splitlines().count("warm-up: skipped (not-local)"), 1)

    def test_a_message_sent_during_the_warm_up_waits_and_says_so(self) -> None:
        warm = WarmUp()  # still running
        clock = FakeClock()

        class Finishing(TimedEngine):
            """The warm-up ends 2 s into this request, as it would in Ollama."""

            def generate(self, messages: Any, *, model: str, **kw: Any) -> Any:
                chat = messages and messages[0].content != base.EXTRACTOR_PROMPT
                if chat and warm.running:
                    warm.status, warm.seconds = "done", 6.0
                    warm.finished_at = self.clock.now + 2.0
                return super().generate(messages, model=model, **kw)

        out, notices, session = self.chat(
            warm, timings=True, engine=Finishing(clock, "Noted."), clock=clock
        )
        self.assertEqual(notices, ["waiting for the warm-up to finish"])  # once
        first, second = [x for x in out.splitlines() if x.startswith("timings:")]
        self.assertIn("queued behind warm-up 2.0 s", first)
        self.assertNotIn("queued behind warm-up", second)
        self.assertEqual(out.splitlines().count("warm-up: done in 6.0 s"), 1)
        self.assertIsNone(session.last_timing.queued)

    def test_the_queued_time_is_the_wait_until_the_warm_up_ended(self) -> None:
        clock = FakeClock()
        warm = WarmUp()

        class Finishing(TimedEngine):
            def generate(self, messages: Any, *, model: str, **kw: Any) -> Any:
                answer = super().generate(messages, model=model, **kw)
                if warm.running and messages[0].content != base.EXTRACTOR_PROMPT:
                    warm.status = "done"
                    warm.finished_at = 102.0  # the request started at 100.0
                return answer

        session = self.session(Finishing(clock, "Noted."))
        session.clock = clock
        session.warmup = warm
        session.notice = lambda _line: None
        self.addCleanup(session.close, 5)
        session.ask("hello")
        self.assertEqual(session.last_timing.queued, 2.0)  # not the whole 5.75 s
        session.ask("again")
        self.assertIsNone(session.last_timing.queued)  # the warm-up was over


class PromptTokensTest(base.HomeTestCase):
    def test_the_prompt_token_counts_are_shown_and_reuse_is_not_claimed(self) -> None:
        timing = model_timing(
            {
                "usage": {
                    "prompt_tokens": 340,
                    "prompt_tokens_evaluated": 312,
                    "completion_tokens": 40,
                },
                "engine_timing": {"eval_duration": 4_000_000_000},
            },
            5.0,
        )
        line = timing_line(timing)
        self.assertIn("prompt tokens 312 evaluated of ~340 (reused n/a)", line)

    def test_missing_or_odd_counts_show_as_not_available(self) -> None:
        for usage in (
            {},
            {"prompt_tokens": 340},
            {"prompt_tokens": True, "prompt_tokens_evaluated": 3},
            {"prompt_tokens": -1, "prompt_tokens_evaluated": "3"},
        ):
            line = timing_line(model_timing({"usage": usage}, 1.0))
            self.assertIn("prompt tokens n/a", line, usage)
            self.assertNotIn("evaluated", line)
