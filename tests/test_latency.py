"""Phase 5b: --timings, the model warm-up and keep-alive (docs/LATENCY.md).

Fake engines, fake clocks, temporary home. No model, Ollama, engine or
player is used, and nothing is played.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import threading
from typing import Any
from unittest import mock

import httpx
import test_steel as base
from openjarvis.engine.ollama import OllamaEngine
from speak_helpers import FakeRun
from test_chat_slash import ReplyEngine
from test_speak_tool import SpeakCase, speak_grant

from steel import warm as warm_module
from steel.chat import open_session, run_repl
from steel.cli import _open_chat, main
from steel.memory import SteelMemory
from steel.speak.commands import ChatSpeaker
from steel.timings import ModelTiming, SpeechTiming, model_timing, timing_line
from steel.warm import (
    KEEP_ALIVE_ENV,
    WARMUP_TEXT,
    install_request_settings,
    keep_alive_setting,
    start_warmup,
)

USER_TEXT = "my bank PIN is 4711"
REPLY = "Mara visits on Friday at four."
# What Ollama reports, in nanoseconds: warm model, 1.5 s prompt, 40 tokens in 4 s.
OLLAMA = {
    "load_duration": 0,
    "prompt_eval_duration": 1_500_000_000,
    "eval_duration": 4_000_000_000,
    "total_duration": 5_600_000_000,
}


class TimedEngine(ReplyEngine):
    """Answers like Ollama, with its durations, and moves a fake clock."""

    def __init__(self, clock: FakeClock, *replies: str) -> None:
        super().__init__(*replies)
        self.clock = clock

    def generate(self, messages: Any, *, model: str, **kw: Any) -> dict[str, Any]:
        answer = super().generate(messages, model=model, **kw)
        if messages and messages[0].content != base.EXTRACTOR_PROMPT:
            self.clock.now += 5.75  # the whole request, seen from STEEL
            answer["engine_timing"] = dict(OLLAMA)
            answer["usage"] = {"completion_tokens": 40}
        return answer


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TimingLineTest(base.HomeTestCase):
    def test_ollama_durations_give_first_token_and_tokens_per_second(self) -> None:
        timing = model_timing(
            {"engine_timing": OLLAMA, "usage": {"completion_tokens": 40}}, 5.75
        )
        self.assertEqual(timing.first_token, 1.5)
        self.assertEqual(timing.tokens_per_second, 10.0)
        self.assertEqual(
            timing_line(timing, SpeechTiming(first_audio=0.4, played=4.7)),
            "timings: model 5.8 s | first token 1.5 s (load 0.0 s, prompt 1.5 s) | "
            "generation 4.0 s | 10.0 tokens/s | prompt tokens n/a | "
            "first audio 0.4 s after the reply | played 4.7 s",
        )

    def test_missing_or_odd_engine_numbers_show_as_not_available(self) -> None:
        for result in (
            "plain text",
            {"content": "x"},
            {"engine_timing": {"load_duration": True, "eval_duration": -1}},
            {"engine_timing": "x", "usage": {"completion_tokens": "40"}},
        ):
            timing = model_timing(result, 2.0)
            self.assertEqual(timing, ModelTiming(total=2.0), result)
            self.assertEqual(
                timing_line(timing),
                "timings: model 2.0 s | first token n/a (load n/a, prompt n/a) | "
                "generation n/a | n/a tokens/s | prompt tokens n/a",
            )


class ChatTimingsTest(SpeakCase):
    def chat(
        self, *, timings: bool, speaker: Any = None, lines: tuple[str, ...] = ()
    ) -> tuple[str, Any]:
        clock = FakeClock()
        engine = TimedEngine(clock, REPLY)
        session = self.session(engine)
        session.clock = clock
        self.addCleanup(session.close, 5)
        out = io.StringIO()
        with (
            mock.patch("builtins.input", side_effect=[*lines, USER_TEXT, "/quit"]),
            mock.patch.dict("os.environ", {"STEEL_EXTRACT_WAIT_SECONDS": "5"}),
            contextlib.redirect_stdout(out),
        ):
            run_repl(session, None, speaker, timings=timings)
        return out.getvalue(), session

    def timing_lines(self, out: str) -> list[str]:
        return [line for line in out.splitlines() if line.startswith("timings:")]

    def test_timings_are_off_unless_asked_for(self) -> None:
        out, session = self.chat(timings=False)
        self.assertEqual(self.timing_lines(out), [])
        self.assertIsNotNone(session.last_timing)  # measured, never printed

    def test_each_turn_prints_one_line_of_numbers_through_the_guardrails(self) -> None:
        out, _ = self.chat(timings=True)
        (line,) = self.timing_lines(out)
        # Ollama's numbers survive the OpenJarvis guardrails wrapper.
        self.assertEqual(
            line,
            "timings: model 5.8 s | first token 1.5 s (load 0.0 s, prompt 1.5 s) | "
            "generation 4.0 s | 10.0 tokens/s | prompt tokens n/a",
        )

    def test_the_timing_line_never_contains_the_message_or_the_reply(self) -> None:
        self.write_grants(speak_grant())
        speaker = ChatSpeaker(self.home, tool=self.tool(), keys=self.keys)
        out, _ = self.chat(timings=True, speaker=speaker)
        (line,) = self.timing_lines(out)
        for word in ("4711", "PIN", "Mara", "Friday", "four"):
            self.assertNotIn(word, line)
        self.assertIn("first audio", line)

    def test_timings_are_never_written_to_any_file(self) -> None:
        out, session = self.chat(timings=True)
        session.close(5)
        self.assertTrue(self.timing_lines(out))
        for path in self.home.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                self.assertNotIn(b"tokens/s", data, path.name)
                self.assertNotIn(b"first token", data, path.name)

    def test_the_spoken_reply_reports_when_its_first_audio_started(self) -> None:
        clock = FakeClock()

        class SlowStart(FakeRun):
            def read_into(self, view: memoryview) -> int:
                if self.reads == 0:
                    clock.now += 0.4  # the engine loading and synthesizing
                return super().read_into(view)

        self.engine.start = lambda text, model: SlowStart()  # type: ignore[method-assign]
        self.write_grants(speak_grant())
        speaker = ChatSpeaker(self.home, tool=self.tool(clock=clock), keys=self.keys)
        speaker.say(REPLY)
        self.assertEqual(speaker.last_timing, SpeechTiming(first_audio=0.4, played=0.5))

    def test_the_command_line_flag_reaches_both_chats(self) -> None:
        with (
            mock.patch("steel.chat.open_session"),
            mock.patch("steel.chat.run_repl") as repl,
        ):
            self.assertEqual(main(["chat", "--timings"]), 0)
            self.assertEqual(main(["chat"]), 0)
        self.assertEqual(
            [c.kwargs["timings"] for c in repl.call_args_list], [True, False]
        )
        with (
            mock.patch("steel.chat.open_session"),
            mock.patch("steel.voice.consent.has_consent", return_value=True),
            mock.patch("steel.voice.commands.run_voice_repl", return_value=0) as voice,
        ):
            self.assertEqual(main(["chat", "--voice", "--timings"]), 0)
        self.assertTrue(voice.call_args.kwargs["timings"])


SECRET_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD"


class OllamaFake:
    """Stands in for the Ollama server behind a real OpenJarvis OllamaEngine.

    Requests go to an httpx.MockTransport, never to a socket. ``gate`` lets a
    test hold a request open, like a model that is still loading.
    """

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.gate = threading.Event()
        self.gate.set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append({"path": request.url.path, **body})
        self.gate.wait(10)
        system = body["messages"][0]["content"] if body["messages"] else ""
        content = "[]" if system == base.EXTRACTOR_PROMPT else "Noted."
        return httpx.Response(
            200, json={"message": {"content": content}, "done": True, **OLLAMA}
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def engine(self, host: str = "http://127.0.0.1:11434") -> OllamaEngine:
        engine = OllamaEngine(host)
        engine._client = httpx.Client(base_url=host, transport=self.transport())
        return engine


@contextlib.contextmanager
def no_network() -> Any:
    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("a real network connection was attempted")

    with (
        mock.patch.object(socket.socket, "connect", forbidden),
        mock.patch.object(socket.socket, "connect_ex", forbidden),
        mock.patch.object(socket, "create_connection", forbidden),
        mock.patch.object(socket, "getaddrinfo", forbidden),
    ):
        yield


class WarmUpTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ollama = OllamaFake()
        os.environ.pop(KEEP_ALIVE_ENV, None)
        # STEEL's request client wraps httpx.HTTPTransport(): use the fake one.
        patch = mock.patch.object(
            warm_module.httpx, "HTTPTransport", self.ollama.transport
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_warm_up_sends_one_fixed_word_and_no_user_text(self) -> None:
        SteelMemory(self.home).remember("My bank PIN is 4711.")
        session = self.session(self.ollama.engine())  # type: ignore[arg-type]
        self.addCleanup(session.close, 5)
        with no_network():
            start_warmup(session.engine, session.model).join(5)
        (body,) = self.ollama.bodies
        self.assertEqual(body["path"], "/api/chat")
        self.assertEqual(body["messages"], [{"role": "user", "content": WARMUP_TEXT}])
        self.assertEqual(body["options"]["num_predict"], 1)
        self.assertNotIn("4711", json.dumps(body))
        self.assertNotIn("keep_alive", body)  # unset: Ollama's default applies

    def test_the_first_prompt_is_not_held_up_by_the_warm_up(self) -> None:
        self.ollama.gate.clear()  # the model is still "loading"
        engine = self.ollama.engine()
        with (
            mock.patch(
                "steel.chat.open_session",
                side_effect=lambda config, home: open_session(
                    config, home, engine=engine
                ),
            ),
            no_network(),
        ):
            session = _open_chat(self.config(), self.home)
        self.addCleanup(session.close, 5)
        (warm,) = [t for t in threading.enumerate() if t.name == "steel-warmup"]
        self.assertTrue(warm.is_alive())  # returned while the request is open
        self.ollama.gate.set()
        warm.join(5)
        self.assertFalse(warm.is_alive())

    def test_a_remote_or_non_ollama_engine_gets_no_warm_up_and_no_keep_alive(
        self,
    ) -> None:
        for engine in (
            self.ollama.engine("http://192.0.2.10:11434"),
            self.ollama.engine("http://ollama.example:11434"),
            base.FakeEngine(),
        ):
            with no_network():
                self.assertEqual(start_warmup(engine, "fake").status, "skipped")
                self.assertFalse(install_request_settings(engine, "30m", 4096))
        self.assertEqual(self.ollama.bodies, [])

    def test_a_failed_warm_up_is_silent_and_changes_nothing(self) -> None:
        engine = OllamaEngine("http://127.0.0.1:11434")
        engine._client = httpx.Client(
            base_url="http://127.0.0.1:11434",
            transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err), no_network():
            warm = start_warmup(engine, "fake")
            warm.join(5)
        self.assertEqual((warm.status, warm.reason), ("failed", "http-500"))
        self.assertEqual(err.getvalue(), "")


class KeepAliveTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ollama = OllamaFake()
        os.environ.pop(KEEP_ALIVE_ENV, None)

    def chat_bodies(self, env: dict[str, str]) -> list[dict[str, Any]]:
        engine = self.ollama.engine()
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(
                warm_module.httpx, "HTTPTransport", self.ollama.transport
            ),
            no_network(),
        ):
            session = self.session(engine)  # type: ignore[arg-type]
            session.ask("hello there")
            session.close(5)
        return self.ollama.bodies

    def test_with_the_setting_every_request_carries_the_keep_alive(self) -> None:
        bodies = self.chat_bodies({KEEP_ALIVE_ENV: "30m"})
        self.assertGreaterEqual(len(bodies), 2)  # the chat and the extraction
        self.assertEqual({b["keep_alive"] for b in bodies}, {"30m"})
        self.assertEqual(bodies[0]["messages"][-1]["content"], "hello there")

    def test_without_the_setting_requests_are_left_alone(self) -> None:
        bodies = self.chat_bodies({})
        self.assertTrue(bodies)
        self.assertFalse(any("keep_alive" in b for b in bodies))

    def test_only_a_duration_from_one_second_to_a_day_is_accepted(self) -> None:
        for good in ("30m", "1h", "90s", "24h", "1440m"):
            self.assertEqual(keep_alive_setting({KEEP_ALIVE_ENV: good}), (good, ""))
        for bad in ("forever", "-1", "0", "30", "25h", "1.5h", "30 m", "0m", "5d"):
            value, problem = keep_alive_setting({KEEP_ALIVE_ENV: bad})
            self.assertIsNone(value, bad)
            self.assertIn("Ollama's own keep-alive applies", problem)
        self.assertEqual(keep_alive_setting({}), (None, ""))

    def test_a_bad_setting_is_reported_at_start_and_the_default_kept(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {KEEP_ALIVE_ENV: "forever"}),
            mock.patch("steel.chat.open_session"),
            mock.patch("steel.warm.start_warmup"),
            contextlib.redirect_stderr(err),
        ):
            _open_chat(self.config(), self.home)
        self.assertIn(
            "STEEL_OLLAMA_KEEP_ALIVE='forever' is not a duration", err.getvalue()
        )

    def test_the_keep_alive_client_never_uses_a_proxy(self) -> None:
        engine = self.ollama.engine()
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:3128"}):
            self.assertTrue(
                install_request_settings(
                    engine, "30m", 4096, inner=self.ollama.transport()
                )
            )
        self.assertFalse(engine._client._trust_env)


class NoStreamingTest(base.HomeTestCase):
    def test_the_chat_never_streams_so_the_guardrails_redact_the_reply(self) -> None:
        class Leaky(base.FakeEngine):
            def generate(self, messages: Any, *, model: str, **kw: Any) -> Any:
                answer = super().generate(messages, model=model, **kw)
                if messages[0].content != base.EXTRACTOR_PROMPT:
                    answer["content"] = f"Your key is {SECRET_KEY}."
                return answer

            async def stream(self, *_: Any, **__: Any) -> Any:
                raise AssertionError("the chat must not stream (docs/LATENCY.md 4)")
                yield ""

        session = self.session(Leaky())
        self.addCleanup(session.close, 5)
        reply = session.ask("what is my key?")
        self.assertNotIn(SECRET_KEY, reply)
        self.assertIn("[REDACTED:", reply)
        self.assertNotIn(SECRET_KEY, session.history[-1].content)
