"""Phase 5b-2: one long-lived Piper engine per voice (docs/SPEAK.md section 6,
"Kept loaded"). Fake processes with real pipes, a fake clock, a fake player; a
few tests use real processes, but a shell script stands in for Piper, so no
engine, voice, model or player is started and nothing is played.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from speak_helpers import CHUNK, FakeKeys, FakeSink, install_voice
from test_chat_slash import ReplyEngine
from test_speak_core import FakePopen, _processes_with
from test_speak_tool import SpeakCase, speak_grant

from steel.chat import open_session, run_repl
from steel.speak.commands import ChatSpeaker
from steel.speak.config import (
    DEFAULT_IDLE_END,
    IDLE_END_ENV,
    PERSISTENT_ENV,
    speak_config,
)
from steel.speak.player import RATES
from steel.speak.synth import (
    FIRST_AUDIO_WAIT,
    PersistentRun,
    PiperEngine,
    PiperRun,
    idle_gap,
)
from steel.speak.voices import VOICES, find_voice, sample_rate_of, voice_dir
from steel.timings import SpeechTiming, model_timing, timing_line

SECRET = "my bank PIN is 4711 and Mara visits on Friday"
LATE = b"\x07" * 4096  # audio of an earlier reply that arrives too late


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def drain(run: Any) -> tuple[bytes, bool]:
    """Read what is there. Returns (bytes, whether the reply has ended)."""
    buffer = memoryview(bytearray(4096))
    data = b""
    while run.readable(0):
        count = run.read_into(buffer)
        if not count:
            return data, True
        data += bytes(buffer[:count])
    return data, False


class PersistentEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "piper"
        (self.directory / "bin").mkdir(parents=True)
        (self.directory / "bin" / "python").write_text("")
        self.popen = FakePopen()
        self.addCleanup(self.popen.close_pipes)
        self.clock = FakeClock()
        self.en = Path("/models/en_US-john-medium.onnx")
        self.es = Path("/models/es_ES-carlfm-x_low.onnx")
        self.tools = {"unshare": "/usr/bin/unshare", "setpriv": "/usr/bin/setpriv"}
        self.engine = PiperEngine(
            self.directory,
            popen=self.popen,
            which=self.tools.get,
            idle_end=1.5,
            clock=self.clock,
        )
        self.addCleanup(self.engine.close)

    def say(self, text: str = "Hello there.", model: Path | None = None) -> Any:
        """Start a reply and let the engine answer with three chunks."""
        run = self.engine.start(text, model or self.en)
        self.popen.audio[len(self.popen.processes) - 1].write(CHUNK * 3)
        return run

    def finish(self, run: Any, silence: float = 1.6) -> bytes:
        """Read everything, then let *silence* seconds pass: the reply's end."""
        data, ended = drain(run)
        self.assertFalse(ended)
        self.clock.now += silence
        more, ended = drain(run)
        self.assertTrue(ended)
        return data + more

    def test_the_second_reply_reuses_the_loaded_engine_with_no_new_process(
        self,
    ) -> None:
        self.assertTrue(self.engine.prepare(self.en))
        first = self.say("One.")
        self.assertEqual(self.finish(first), CHUNK * 3)
        first.stop()  # a clean end: the engine stays
        second = self.say("Two.")
        self.assertEqual(self.finish(second), CHUNK * 3)
        second.stop()
        (process,) = self.popen.processes
        self.assertEqual(len(self.popen.calls), 1)  # one load for the session
        self.assertEqual(process.stdin.data, b"One.\nTwo.\n")
        self.assertFalse(process.stdin.closed)  # still waiting for the next text
        process.kill.assert_not_called()

    def test_a_reply_ends_after_the_idle_gap_and_not_before(self) -> None:
        run = self.say()
        data, ended = drain(run)
        self.assertEqual(data, CHUNK * 3)
        self.assertFalse(ended)
        self.clock.now += 1.4  # a pause between sentences is shorter than the gap
        self.assertEqual(drain(run), (b"", False))
        self.popen.audio[0].write(CHUNK)  # the next sentence arrives in time
        self.assertEqual(drain(run), (CHUNK, False))
        self.clock.now += 1.4
        self.assertFalse(drain(run)[1])
        self.clock.now += 0.2  # 1.6 s after the last byte
        self.assertTrue(drain(run)[1])
        self.assertTrue(run.clean)

    def test_a_long_sentence_gets_a_longer_gap_than_the_setting(self) -> None:
        self.assertEqual(idle_gap("Short one. Another short one.", 1.5), 1.5)
        long = "word " * 80  # a 400 character sentence
        self.assertAlmostEqual(idle_gap(long + ". Short.", 1.5), 4.0, delta=0.1)
        self.assertEqual(idle_gap(long, 5.0), 5.0)  # the setting is a floor
        run = self.say(long.strip() + ".")
        drain(run)
        self.clock.now += 3.0
        self.assertFalse(drain(run)[1])
        self.clock.now += 1.2
        self.assertTrue(drain(run)[1])

    def test_with_no_audio_yet_it_waits_for_the_voice_to_load(self) -> None:
        run = self.engine.start("Hello.", self.en)  # the voice is still loading
        self.clock.now += FIRST_AUDIO_WAIT - 1
        self.assertEqual(drain(run), (b"", False))  # no idle gap before audio
        self.popen.audio[0].write(CHUNK)  # it did load, 44 s in
        self.assertEqual(drain(run), (CHUNK, False))

    def test_a_silent_engine_is_given_up_and_replaced(self) -> None:
        run = self.engine.start("...", self.en)
        self.clock.now += FIRST_AUDIO_WAIT + 1
        self.assertEqual(drain(run), (b"", True))
        self.assertFalse(run.clean)  # no audio: something is wrong with it
        run.stop()
        self.assertEqual(len(self.popen.processes), 2)
        self.popen.processes[0].kill.assert_called_once_with()

    def test_an_interrupted_reply_drops_late_audio_and_loads_a_clean_engine(
        self,
    ) -> None:
        run = self.say("A long reply.")
        buffer = memoryview(bytearray(4096))
        run.readable(0)
        run.read_into(buffer)  # one chunk played, two still in the pipe
        run.stop()  # Enter, a typed line or Ctrl-C: the reply is cut
        old, new = self.popen.processes
        old.kill.assert_called_once_with()
        new.kill.assert_not_called()
        self.assertEqual(new.stdin.data, b"")  # loading in the background
        with contextlib.suppress(OSError):
            self.popen.audio[0].write(LATE)  # the old engine's last words
        run2 = self.engine.start("The next reply.", self.en)
        self.assertEqual(len(self.popen.processes), 2)  # no third process
        self.assertEqual(new.stdin.data, b"The next reply.\n")
        self.popen.audio[1].write(CHUNK)
        self.clock.now += 0.1
        self.assertEqual(drain(run2)[0], CHUNK)  # none of the old audio

    def test_audio_that_arrives_after_the_gap_replaces_the_engine_next_time(
        self,
    ) -> None:
        run = self.say()
        self.finish(run)
        run.stop()
        self.popen.audio[0].write(LATE)  # synthesis was slower than the gap
        self.say("The next reply.")
        old, new = self.popen.processes
        old.kill.assert_called_once_with()
        self.assertEqual(new.stdin.data, b"The next reply.\n")
        self.assertEqual(old.stdin.data, b"Hello there.\n")

    def test_an_engine_killed_during_a_reply_ends_it_and_a_new_one_loads(
        self,
    ) -> None:
        run = self.say()
        buffer = memoryview(bytearray(4096))
        run.read_into(buffer)
        self.popen.processes[0].poll.return_value = -9  # killed from outside
        self.popen.audio[0].close()  # its end of the pipe goes with it
        self.assertEqual(drain(run)[1], True)
        self.assertTrue(run.died)
        self.assertFalse(run.clean)
        run.stop()
        self.assertEqual(len(self.popen.processes), 2)  # replaced, loading
        run2 = self.say("Next.")
        self.assertEqual(len(self.popen.processes), 2)
        self.assertFalse(run2.died)

    def test_an_engine_that_exits_without_closing_its_pipe_is_noticed(self) -> None:
        run = self.say()
        drain(run)
        self.popen.processes[0].poll.return_value = 1
        self.assertEqual(drain(run), (b"", True))
        self.assertTrue(run.died)

    def test_a_language_switch_stops_the_other_voice_and_starts_this_one(self) -> None:
        first = self.say("Hello.", self.en)
        self.finish(first)
        first.stop()
        second = self.say("Hola.", self.es)
        english, spanish = self.popen.processes
        english.kill.assert_called_once_with()
        self.assertIn(str(self.es), self.popen.calls[1]["argv"])
        self.assertEqual(spanish.stdin.data, b"Hola.\n")
        self.finish(second)
        second.stop()
        self.say("Hello again.", self.en)
        self.assertEqual(len(self.popen.processes), 3)
        spanish.kill.assert_called_once_with()  # never two voices at once

    def test_text_is_never_on_a_command_line_or_in_the_environment(self) -> None:
        for _ in range(3):
            run = self.say(SECRET)
            self.finish(run)
            run.stop()
        run = self.say(SECRET)
        drain(run)
        run.stop()  # an interrupted one: a replacement is started too
        self.assertGreaterEqual(len(self.popen.calls), 2)
        for call in self.popen.calls:
            self.assertFalse(any("4711" in a or "Mara" in a for a in call["argv"]))
            self.assertFalse(any("4711" in v for v in call["env"].values()))
            self.assertEqual(call["stdin"], subprocess.PIPE)
        self.assertEqual(
            self.popen.processes[0].stdin.data, (SECRET + "\n").encode() * 4
        )

    def test_without_unshare_the_long_lived_engine_is_never_started(self) -> None:
        del self.tools["unshare"]
        self.assertFalse(self.engine.prepare(self.en))
        with self.assertRaises(Exception) as caught:
            self.engine.start(SECRET, self.en)
        self.assertIn("without network access", str(caught.exception))
        self.assertEqual(self.popen.calls, [])

    def test_the_old_per_reply_path_stays_when_the_long_lived_engine_is_off(
        self,
    ) -> None:
        engine = PiperEngine(
            self.directory,
            popen=self.popen,
            which=self.tools.get,
            persistent=False,
            clock=self.clock,
        )
        self.addCleanup(engine.close)
        self.assertTrue(engine.prepare(self.en))
        run = engine.start("One.", self.en)
        self.assertIsInstance(run, PiperRun)
        self.assertEqual(len(self.popen.processes), 1)  # the early start is used
        self.assertTrue(self.popen.processes[0].stdin.closed)  # EOF ends the reply
        engine.start("Two.", self.en)
        self.assertEqual(len(self.popen.processes), 2)  # a process per reply
        self.assertTrue(self.popen.processes[1].stdin.closed)

    def test_the_old_path_is_the_fallback_when_the_long_lived_engine_fails(
        self,
    ) -> None:
        self.popen.broken_pipes = 1
        run = self.engine.start("Hello.", self.en)
        self.assertIsInstance(run, PiperRun)
        self.assertTrue(self.popen.processes[1].stdin.closed)

    def test_close_stops_the_engine_and_a_late_stop_starts_nothing(self) -> None:
        run = self.say()
        self.engine.close()
        self.popen.processes[0].kill.assert_called_once_with()
        run.stop()  # the chat is leaving: no replacement is started
        self.assertEqual(len(self.popen.processes), 1)

    def test_the_run_reports_the_wait_for_the_first_audio(self) -> None:
        run = self.engine.start("Hello.", self.en)
        self.clock.now += 0.3
        self.popen.audio[0].write(CHUNK)
        drain(run)
        assert isinstance(run, PersistentRun)
        self.assertAlmostEqual(run.engine_seconds or 0, 0.3)
        for seconds, state in ((0.3, "warm"), (4.9, "loading")):
            line = timing_line(
                model_timing({}, 1.0), SpeechTiming(0.5, 3.0, engine=seconds)
            )
            self.assertTrue(line.endswith(f"engine: {state} ({seconds:.1f} s)"), line)
        self.assertNotIn(
            "engine:", timing_line(model_timing({}, 1.0), SpeechTiming(0.5, 3.0))
        )


class SettingsTest(unittest.TestCase):
    def test_the_idle_gap_is_validated_from_half_a_second_to_five(self) -> None:
        for good, value in (("0.5", 0.5), ("1.5", 1.5), ("5", 5.0), (" 2.5 ", 2.5)):
            self.assertEqual(speak_config({IDLE_END_ENV: good}).idle_end_seconds, value)
        for bad in ("0.4", "5.1", "0", "-1", "nan", "inf", "soon", "1,5", ""):
            self.assertEqual(
                speak_config({IDLE_END_ENV: bad}).idle_end_seconds,
                DEFAULT_IDLE_END,
                bad,
            )

    def test_the_long_lived_engine_is_on_unless_switched_off_with_a_zero(self) -> None:
        self.assertTrue(speak_config({}).persistent)
        self.assertTrue(speak_config({PERSISTENT_ENV: "1"}).persistent)
        self.assertTrue(speak_config({PERSISTENT_ENV: "maybe"}).persistent)
        self.assertFalse(speak_config({PERSISTENT_ENV: "0"}).persistent)


class SampleRateTest(unittest.TestCase):
    def test_the_rate_comes_from_the_voices_own_config_file(self) -> None:
        voice = find_voice("es_ES-carlfm-x_low")
        assert voice is not None
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            install_voice(home, voice)  # zero bytes: not JSON, so the pinned rate
            self.assertEqual(sample_rate_of(home, voice), voice.sample_rate)
            config = voice_dir(home, voice) / voice.config.name
            for text, expected in (
                ('{"audio": {"sample_rate": 22050}}', 22050),
                ('{"audio": {"sample_rate": 16000}}', 16000),
                ('{"audio": {"sample_rate": 44100}}', voice.sample_rate),  # not allowed
                ('{"audio": {"sample_rate": true}}', voice.sample_rate),
                ('{"audio": {"sample_rate": "22050"}}', voice.sample_rate),
                ('{"audio": 3}', voice.sample_rate),
                ("[]", voice.sample_rate),
            ):
                config.write_text(text)
                self.assertEqual(sample_rate_of(home, voice), expected, text)
            config.unlink()
            self.assertEqual(sample_rate_of(home, voice), voice.sample_rate)

    def test_the_pinned_rates_are_the_ones_the_player_accepts(self) -> None:
        for voice in VOICES:
            self.assertIn(voice.sample_rate, RATES, voice.name)


class CheckOutputTest(SpeakCase):
    def check(self, env: dict[str, str]) -> str:
        from steel.speak.commands import speak_check

        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(
                subprocess, "Popen", side_effect=AssertionError("started")
            ),
            contextlib.redirect_stdout(out),
        ):
            speak_check(self.home, which=lambda _n: None)
        return out.getvalue()

    def test_check_tells_how_long_the_engine_lives_and_what_it_costs(self) -> None:
        out = self.check({IDLE_END_ENV: "2"})
        self.assertIn("one engine per voice stays loaded", out)
        self.assertIn("about 142 MiB of RAM each", out)
        self.assertIn("a reply ends after 2 s without new audio", out)
        out = self.check({PERSISTENT_ENV: "0"})
        self.assertIn("one engine process per reply", out)


class PersistentTurnTest(SpeakCase):
    """The whole tool and chat, with the real engine class on fake processes."""

    def setUp(self) -> None:
        super().setUp()
        self.popen = FakePopen()
        self.addCleanup(self.popen.close_pipes)
        self.deaths: set[int] = set()
        self.popen.on_write = self.answer
        self.piper = PiperEngine(
            self.home / "piper",
            popen=self.popen,
            which={"unshare": "/usr/bin/unshare"}.get,
            idle_end=0.15,
        )
        (self.home / "piper" / "bin").mkdir(parents=True)
        (self.home / "piper" / "bin" / "python").write_text("")
        self.addCleanup(self.piper.close)
        self.write_grants(speak_grant())

    def answer(self, index: int, _text: bytes) -> None:
        """The engine's answer to a line: three chunks, or a crash after one."""
        writer = self.popen.audio[index]
        if index in self.deaths:
            writer.write(CHUNK)
            self.popen.processes[index].poll.return_value = -9
            writer.close()
        else:
            writer.write(CHUNK * 3)

    def speaker(self) -> ChatSpeaker:
        return ChatSpeaker(self.home, tool=self.tool(engine=self.piper), keys=self.keys)

    def test_two_spoken_replies_use_one_engine_and_both_are_played(self) -> None:
        first = self.speak("One reply.", tool=self.tool(engine=self.piper))
        second = self.speak("Another reply.", tool=self.tool(engine=self.piper))
        self.assertTrue(first.success and second.success, (first, second))
        self.assertEqual(len(self.popen.processes), 1)
        self.assertEqual([bytes(s.data) for s in self.sinks], [CHUNK * 3, CHUNK * 3])
        self.assertEqual(
            self.popen.processes[0].stdin.data, b"One reply.\nAnother reply.\n"
        )
        self.assertIsNotNone(second.metadata["engine_seconds"])

    def test_an_engine_that_dies_mid_reply_gives_one_line_and_stays_usable(
        self,
    ) -> None:
        speaker = self.speaker()
        self.deaths.add(0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            speaker.say("First reply.")
        lines = [x for x in out.getvalue().splitlines() if "engine stopped" in x]
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertEqual(len(self.popen.processes), 2)  # a new one is loading
        with contextlib.redirect_stdout(io.StringIO()):
            speaker.say("Second reply.")
        self.assertEqual(len(self.popen.processes), 2)
        self.assertEqual(bytes(self.sinks[-1].data), CHUNK * 3)

    def test_a_key_press_stops_the_player_and_replaces_the_engine(self) -> None:
        self.keys = FakeKeys(stop_after=1)
        result = self.speak("A long reply.", tool=self.tool(engine=self.piper))
        self.assertEqual(result.metadata["stopped_by"], "key")
        old, new = self.popen.processes
        old.kill.assert_called_once_with()
        self.assertEqual(new.stdin.data, b"")

    def test_a_whole_spoken_chat_uses_no_network_and_writes_no_file(self) -> None:
        speaker = self.speaker()
        speaker.say("warm up")
        config = self.config()
        config.memory.enabled = False
        session = open_session(config, self.home, engine=ReplyEngine("Sure.", "Yes."))
        real_open = builtins.open

        def guarded(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
            if any(flag in mode for flag in "wx+") or (
                "a" in mode and not str(file).endswith("audit.jsonl")
            ):
                raise AssertionError(f"tried to write {file!r}")
            return real_open(file, mode, *a, **k)

        def forbidden(*_: Any, **__: Any) -> Any:
            raise AssertionError("no network and no temporary file in a turn")

        with (
            mock.patch("builtins.input", side_effect=["hello", "and again", "/quit"]),
            mock.patch.object(builtins, "open", guarded),
            mock.patch.object(tempfile, "mkstemp", forbidden),
            mock.patch.object(tempfile, "NamedTemporaryFile", forbidden),
            mock.patch.object(socket.socket, "connect", forbidden),
            mock.patch.object(socket.socket, "connect_ex", forbidden),
            mock.patch.object(socket, "create_connection", forbidden),
            mock.patch.object(socket, "getaddrinfo", forbidden),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run_repl(session, None, speaker)
        session.close(5)
        self.assertEqual(len(self.popen.processes), 1)  # three replies, one engine
        self.assertTrue(
            all("--net" in c["argv"] for c in self.popen.calls),
            "every engine runs in its own network namespace",
        )


@unittest.skipUnless(shutil.which("setpriv"), "util-linux setpriv is not installed")
class RealProcessTest(unittest.TestCase):
    """Real processes, but neither Piper nor unshare: a shell script stands in
    for both. It answers each input line with 8 KB of silence and exits at the
    end of its stdin. Nothing is played."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.marker = str(self.root)
        (self.root / "piper" / "bin").mkdir(parents=True)
        (self.root / "piper" / "bin" / "python").write_text("")
        stand_in = self.root / "unshare-stand-in"
        stand_in.write_text(
            "#!/bin/sh\nwhile read line; do head -c 8192 /dev/zero; done\n"
        )
        stand_in.chmod(0o700)
        self.which = {"unshare": str(stand_in), "setpriv": shutil.which("setpriv")}.get
        self.addCleanup(self._kill_leftovers)
        self.model = self.root / "voice.onnx"

    def _kill_leftovers(self) -> None:
        for pid in _processes_with(self.marker):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def gone(self, seconds: float = 5.0) -> list[int]:
        deadline = time.monotonic() + seconds
        while _processes_with(self.marker) and time.monotonic() < deadline:
            time.sleep(0.05)
        return _processes_with(self.marker)

    def engine(self) -> PiperEngine:
        engine = PiperEngine(self.root / "piper", which=self.which, idle_end=0.3)
        self.addCleanup(engine.close)
        return engine

    def read_all(self, run: Any) -> bytes:
        buffer = memoryview(bytearray(4096))
        data = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if run.readable(0.05):
                count = run.read_into(buffer)
                if not count:
                    return data
                data += bytes(buffer[:count])
        self.fail("the reply never ended")

    def test_a_real_engine_serves_two_replies_and_ends_each_by_silence(self) -> None:
        engine = self.engine()
        first = engine.start(SECRET, self.model)
        self.assertEqual(len(self.read_all(first)), 8192)
        first.stop()
        (pid,) = _processes_with(self.marker)
        second = engine.start("Again.", self.model)
        self.assertEqual(len(self.read_all(second)), 8192)
        second.stop()
        self.assertEqual(_processes_with(self.marker), [pid])  # the same process
        # The text is on stdin only: not in what ps shows, not in the environment.
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        environ = Path(f"/proc/{pid}/environ").read_bytes()
        for secret in (b"4711", b"Mara", b"Again"):
            self.assertNotIn(secret, cmdline)
            self.assertNotIn(secret, environ)

    def test_an_interrupt_replaces_the_process_and_close_leaves_none(self) -> None:
        engine = self.engine()
        run = engine.start("A long reply.", self.model)
        (old,) = _processes_with(self.marker)
        buffer = memoryview(bytearray(10))
        run.readable(1)
        run.read_into(buffer)  # audio has started, the end has not come
        run.stop()  # the reply is cut
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _processes_with(self.marker) in (
            [],
            [old],
        ):
            time.sleep(0.05)
        (new,) = _processes_with(self.marker)
        self.assertNotEqual(new, old)
        engine.close()
        self.assertEqual(self.gone(), [])

    def test_ctrl_c_while_speaking_leaves_no_process_after_the_chat_closes_it(
        self,
    ) -> None:
        interrupts: list[int] = []

        class Interrupting(FakeSink):
            def write(self, data: memoryview) -> None:
                interrupts.append(1)
                raise KeyboardInterrupt

        home = Path(tempfile.mkdtemp(prefix="steel-persist-"))
        self.addCleanup(shutil.rmtree, home, True)
        engine = self.engine()
        from speak_helpers import install_defaults

        install_defaults(home)
        (home / "steel_policy.json").write_text(
            '{"agents":[{"agent_id":"voice","grants":'
            '[{"capability":"audio:play","pattern":"speak"}]}]}'
        )
        (home / "steel_policy.json").chmod(0o600)
        from voice_helpers import FakeTTY

        from steel.speak.config import SpeakConfig
        from steel.speak.indicator import SpeakingIndicator
        from steel.tools import get_speak_tool

        tool = get_speak_tool(
            home,
            config=SpeakConfig(),
            engine=engine,
            open_sink=Interrupting,
            indicator=SpeakingIndicator(FakeTTY()),
            keys=FakeKeys(),
        )
        speaker = ChatSpeaker(home, tool=tool, keys=FakeKeys())
        speaker.prepare("hello")
        self.assertEqual(len(_processes_with(self.marker)), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(speaker.say("Hello there."))  # the chat goes on
        self.assertEqual(interrupts, [1])
        speaker.close()  # what the chat does on every exit path
        self.assertEqual(self.gone(), [])

    def test_no_engine_is_left_if_steel_is_killed_during_a_reply(self) -> None:
        code = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from steel.speak.synth import PiperEngine\n"
            "root = Path(os.environ['STAND_IN_ROOT'])\n"
            "tools = {'unshare': str(root / 'unshare-stand-in'),"
            " 'setpriv': os.environ['STAND_IN_SETPRIV']}\n"
            "engine = PiperEngine(root / 'piper', which=tools.get)\n"
            "run = engine.start('Hello.', root / 'voice.onnx')\n"
            "print('ready', flush=True)\n"
            "sys.stdin.read()\n"
        )
        env = {
            **os.environ,
            "STAND_IN_ROOT": str(self.root),
            "STAND_IN_SETPRIV": str(self.which("setpriv")),
        }
        steel = subprocess.Popen(
            [sys.executable, "-I", "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )

        def stop_steel() -> None:
            steel.kill()
            steel.wait(5)
            for stream in (steel.stdin, steel.stdout):
                with contextlib.suppress(OSError):
                    stream.close()

        self.addCleanup(stop_steel)
        self.assertEqual(steel.stdout.readline().strip(), "ready")
        self.assertEqual(len(_processes_with(self.marker)), 1)
        steel.kill()  # no chance to clean up
        steel.wait(5)
        self.assertEqual(self.gone(), [])


if __name__ == "__main__":
    unittest.main()
