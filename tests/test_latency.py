"""Phase 5b: --timings, the model warm-up and keep-alive (docs/LATENCY.md).

Fake engines, fake clocks, temporary home. No model, Ollama, engine or
player is used, and nothing is played.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any
from unittest import mock

import test_steel as base
from speak_helpers import FakeRun
from test_chat_slash import ReplyEngine
from test_speak_tool import SpeakCase, speak_grant

from steel.chat import run_repl
from steel.cli import main
from steel.speak.commands import ChatSpeaker
from steel.timings import ModelTiming, SpeechTiming, model_timing, timing_line

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
            "generation 4.0 s | 10.0 tokens/s | first audio 0.4 s after the reply | "
            "played 4.7 s",
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
                "generation n/a | n/a tokens/s",
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
            "generation 4.0 s | 10.0 tokens/s",
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
