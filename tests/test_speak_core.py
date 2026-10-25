"""Spoken replies, the parts below the tool: voices, engine, player, indicator,
keys and playback (docs/SPEAK.md). Fake processes only: no engine or player is
started, no audio device is opened, nothing is played, no network is used.
"""

from __future__ import annotations

import os
import pty
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from speak_helpers import CHUNK, FakeKeys, FakeRun, FakeSink, install_voice, tone
from voice_helpers import BrokenTTY, FakeTTY, NotATTY

from steel.speak import synth as engine_module
from steel.speak.indicator import (
    SpeakingIndicator,
    SpeakingIndicatorError,
    SpeakingShown,
)
from steel.speak.keys import TypedLineStop
from steel.speak.playback import play
from steel.speak.player import AplaySink, PlayerUnavailable
from steel.speak.synth import (
    ENGINE_ENV,
    EngineUnavailable,
    PiperEngine,
    engine_dir,
    engine_version,
)
from steel.speak.voices import (
    REVISION,
    VOICES,
    find_voice,
    installed,
    voice_dir,
    voice_for,
)

ROOT = Path(__file__).resolve().parent.parent
SECRET = "my bank PIN is 4711 and Mara visits on Friday"


class FakePopen:
    """Records how a program would have been started; starts nothing."""

    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []
        self.stdin_data = bytearray()

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": list(argv), **kwargs})
        owner = self

        class Stdin:
            closed = False

            def write(self, data: bytes) -> int:
                owner.stdin_data += data
                return len(data)

            def close(self) -> None:
                self.closed = True

        process = mock.Mock()
        process.stdin = Stdin()
        process.poll.return_value = None
        return process


class VoicesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def test_every_voice_is_pinned_to_one_revision_with_size_and_sha256(self) -> None:
        self.assertEqual(len(REVISION), 40)
        for voice in VOICES:
            for pinned in voice.files():
                self.assertRegex(pinned.sha256, r"^[0-9a-f]{64}$")
                self.assertGreater(pinned.size, 0)
                self.assertIn(f"/resolve/{REVISION}/", voice.url(pinned))
        defaults = [v for v in VOICES if v.default]
        self.assertEqual(sorted(v.language for v in defaults), ["en", "es", "it"])

    def test_the_default_checksums_are_the_ones_in_the_design_document(self) -> None:
        doc = (ROOT / "docs" / "SPEAK.md").read_text(encoding="utf-8")
        for voice in VOICES:
            if voice.default:
                for pinned in voice.files():
                    self.assertIn(pinned.sha256, doc, pinned.name)

    def test_a_voice_counts_only_with_every_file_at_its_pinned_size(self) -> None:
        voice = find_voice("es_ES-carlfm-x_low")
        assert voice is not None
        self.assertFalse(installed(self.home, voice))
        install_voice(self.home, voice)
        self.assertTrue(installed(self.home, voice))
        self.assertFalse(installed(self.home, voice, verify_hash=True))  # wrong bytes
        with open(voice_dir(self.home, voice) / voice.config.name, "ab") as handle:
            handle.write(b"x")
        self.assertFalse(installed(self.home, voice))

    def test_a_symlinked_voice_file_does_not_count(self) -> None:
        voice = find_voice("it_IT-riccardo-x_low")
        assert voice is not None
        install_voice(self.home, voice)
        model = voice_dir(self.home, voice) / voice.model.name
        model.rename(self.home / "elsewhere.onnx")
        model.symlink_to(self.home / "elsewhere.onnx")
        self.assertFalse(installed(self.home, voice))

    def test_the_voice_for_a_language_prefers_one_fetched_on_purpose(self) -> None:
        self.assertIsNone(voice_for(self.home, "en"))
        john = find_voice("en_US-john-medium")
        british = find_voice("en_GB-northern_english_male-medium")
        assert john and british
        install_voice(self.home, john)
        self.assertEqual(voice_for(self.home, "en"), john)
        install_voice(self.home, british)
        self.assertEqual(voice_for(self.home, "en"), british)
        self.assertIsNone(voice_for(self.home, "es"))


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "piper"
        (self.directory / "bin").mkdir(parents=True)
        (self.directory / "bin" / "python").write_text("")
        self.popen = FakePopen()
        self.model = Path("/models/en_US-john-medium.onnx")

    def engine(self, unshare: str | None = "/usr/bin/unshare") -> PiperEngine:
        return PiperEngine(
            self.directory,
            popen=self.popen,
            which=lambda name: unshare if name == "unshare" else None,
        )

    def test_the_engine_runs_without_network_and_gets_the_text_only_on_stdin(
        self,
    ) -> None:
        self.engine().start(SECRET, self.model)
        (call,) = self.popen.calls
        self.assertEqual(
            call["argv"],
            [
                "/usr/bin/unshare",
                "--net",
                "--map-root-user",
                "--",
                str(self.directory / "bin" / "python"),
                "-I",
                "-m",
                "piper",
                "-m",
                str(self.model),
                "--output-raw",
            ],
        )
        # Not on the command line (ps shows it) and not in the environment.
        self.assertFalse(any("4711" in part or "Mara" in part for part in call["argv"]))
        self.assertEqual(call["env"], ENGINE_ENV)
        self.assertFalse(any("4711" in value for value in call["env"].values()))
        self.assertEqual(self.popen.stdin_data, (SECRET + "\n").encode())
        self.assertEqual(call["stdin"], subprocess.PIPE)
        self.assertEqual(call["stdout"], subprocess.PIPE)
        self.assertEqual(call["stderr"], subprocess.DEVNULL)
        self.assertEqual(call["cwd"], "/")

    def test_the_engine_environment_has_no_home_proxy_or_key_and_no_telemetry(
        self,
    ) -> None:
        self.assertEqual(ENGINE_ENV["ORT_DISABLE_TELEMETRY"], "1")
        self.assertEqual(set(ENGINE_ENV), {"LANG", "LC_ALL", "ORT_DISABLE_TELEMETRY"})

    def test_several_lines_become_one_line_on_stdin(self) -> None:
        self.engine().start("one\ntwo\r\n three", self.model)
        self.assertEqual(self.popen.stdin_data, b"one two three\n")

    def test_without_unshare_the_engine_is_never_started(self) -> None:
        with self.assertRaises(EngineUnavailable) as caught:
            self.engine(unshare=None).start(SECRET, self.model)
        self.assertEqual(self.popen.calls, [])
        self.assertIn("without network access", str(caught.exception))
        self.assertNotIn("4711", str(caught.exception))

    def test_without_the_engine_installed_nothing_is_started(self) -> None:
        (self.directory / "bin" / "python").unlink()
        with self.assertRaises(EngineUnavailable):
            self.engine().start(SECRET, self.model)
        self.assertEqual(self.popen.calls, [])

    def test_the_engine_location_and_version_are_found_without_running_it(
        self,
    ) -> None:
        self.assertEqual(
            engine_dir({"STEEL_PIPER_DIR": "/opt/piper"}), Path("/opt/piper")
        )
        self.assertEqual(
            engine_dir({"STEEL_PIPER_DIR": "relative/x"}),
            engine_module.default_engine_dir(),
        )
        self.assertIsNone(engine_version(self.directory))
        meta = self.directory / "lib/python3.13/site-packages/piper_tts-1.8.0.dist-info"
        meta.mkdir(parents=True)
        (meta / "METADATA").write_text("Name: piper-tts\nVersion: 1.8.0\n")
        self.assertEqual(engine_version(self.directory), "1.8.0")


class SinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.popen = FakePopen()
        self.sink = AplaySink(popen=self.popen, which=lambda _n: "/usr/bin/aplay")
        self.shown = SpeakingIndicator(FakeTTY()).show()

    def test_aplay_reads_raw_audio_from_stdin_and_is_given_no_file(self) -> None:
        self.sink.open(self.shown, 22050)
        (call,) = self.popen.calls
        self.assertEqual(
            call["argv"],
            [
                "/usr/bin/aplay",
                "-q",
                "-t",
                "raw",
                "-f",
                "S16_LE",
                "-r",
                "22050",
                "-c",
                "1",
                "-",
            ],
        )
        self.assertEqual(call["stdin"], subprocess.PIPE)
        self.assertEqual(call["bufsize"], 0)

    def test_the_speaker_never_opens_without_the_speaking_proof(self) -> None:
        for fake in (None, object(), "SPEAKING"):
            with self.assertRaises(TypeError):
                self.sink.open(fake, 22050)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            SpeakingShown("▶ SPEAKING", object())
        self.assertEqual(self.popen.calls, [])

    def test_an_unexpected_rate_or_a_missing_player_starts_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.sink.open(self.shown, 44100)
        missing = AplaySink(popen=self.popen, which=lambda _n: None)
        with self.assertRaises(PlayerUnavailable):
            missing.open(self.shown, 16000)
        self.assertEqual(self.popen.calls, [])


class IndicatorTest(unittest.TestCase):
    def test_no_terminal_or_a_failing_write_means_no_proof(self) -> None:
        for stream in (NotATTY(), BrokenTTY()):
            with self.assertRaises(SpeakingIndicatorError):
                SpeakingIndicator(stream).show()

    def test_the_line_says_how_to_stop(self) -> None:
        screen = FakeTTY()
        SpeakingIndicator(screen).show()
        self.assertIn("▶ SPEAKING 00:00", screen.getvalue())
        self.assertIn("Enter or a typed line stops; Ctrl-C stops", screen.getvalue())


class PlaybackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = FakeTTY()
        self.indicator = SpeakingIndicator(self.screen)
        self.sink = FakeSink(screen=self.screen)
        self.now = 0.0

    def clock(self) -> float:
        self.now += 0.01
        return self.now

    def play(self, run: FakeRun, **kw: Any) -> Any:
        kw.setdefault("max_seconds", 120.0)
        return play(
            run,
            self.sink,
            self.indicator,
            rate=22050,
            clock=self.clock,
            sleep=lambda _s: None,
            **kw,
        )

    def test_the_speaking_line_is_on_screen_before_the_speaker_opens(self) -> None:
        self.play(FakeRun())
        assert self.sink.screen_at_open is not None
        self.assertIn("▶ SPEAKING", self.sink.screen_at_open)

    def test_without_a_terminal_nothing_plays_and_the_engine_is_stopped(self) -> None:
        run = FakeRun()
        with self.assertRaises(SpeakingIndicatorError):
            play(
                run, self.sink, SpeakingIndicator(NotATTY()), rate=22050, max_seconds=5
            )
        self.assertEqual(self.sink.opened, [])
        self.assertEqual(run.stopped, 1)

    def test_all_audio_reaches_the_player_and_then_it_finishes(self) -> None:
        run = FakeRun(tone(5))
        done = self.play(run)
        self.assertEqual(done.reason, "finished")
        self.assertEqual(bytes(self.sink.data), CHUNK * 5)
        self.assertEqual(
            (self.sink.finished, self.sink.stopped, run.stopped), (1, 1, 1)
        )
        self.assertIn("■ finished speaking", self.screen.getvalue())

    def test_the_audio_buffer_is_zeroed_after_use(self) -> None:
        self.play(FakeRun(tone(3)))
        buffers = {id(b): b for b in self.sink.buffers}
        self.assertEqual(len(buffers), 1)  # one buffer, reused
        (buffer,) = buffers.values()
        self.assertEqual(bytes(buffer), bytes(len(buffer)))

    def test_enter_stops_the_player_and_the_engine_at_once(self) -> None:
        run = FakeRun(tone(50))
        done = self.play(run, stop_requested=FakeKeys(stop_after=2).stop_requested)
        self.assertEqual(done.reason, "key")
        self.assertEqual(len(self.sink.data), 2 * len(CHUNK))
        self.assertEqual((self.sink.stopped, run.stopped), (1, 1))
        self.assertEqual(self.sink.finished, 0)
        self.assertIn("■ stopped speaking", self.screen.getvalue())

    def test_a_key_while_the_player_drains_also_stops_it(self) -> None:
        self.sink.done_after = 100
        done = self.play(
            FakeRun(tone(2)), stop_requested=FakeKeys(stop_after=4).stop_requested
        )
        self.assertEqual(done.reason, "key")
        self.assertEqual(self.sink.stopped, 1)

    def test_ctrl_c_cancels_and_stops_both(self) -> None:
        cancel = threading.Event()
        cancel.set()
        run = FakeRun(tone(5))
        done = self.play(run, cancelled=cancel.is_set)
        self.assertEqual(done.reason, "cancelled")
        self.assertEqual(self.sink.data, bytearray())
        self.assertEqual((self.sink.stopped, run.stopped), (1, 1))

    def test_the_audio_limit_stops_a_long_reply(self) -> None:
        # 22050 Hz, 16 bit: 4096 bytes is about 0.093 s.
        done = self.play(FakeRun(tone(100)), max_seconds=0.3)
        self.assertEqual(done.reason, "limit")
        self.assertLess(len(self.sink.data), 5 * len(CHUNK))

    def test_an_engine_that_produces_nothing_is_reported(self) -> None:
        done = self.play(FakeRun([]))
        self.assertEqual(done.reason, "no_audio")

    def test_if_the_player_cannot_open_the_engine_is_stopped(self) -> None:
        run = FakeRun()
        self.sink.fail_open = PlayerUnavailable("no aplay")
        with self.assertRaises(PlayerUnavailable):
            self.play(run)
        self.assertEqual(run.stopped, 1)
        self.assertIn("nothing was played", self.screen.getvalue())


class TypedLineStopTest(unittest.TestCase):
    """A real pseudo-terminal: real key handling, no audio."""

    def setUp(self) -> None:
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.keys = TypedLineStop(self.slave)

        def too_slow(*_: object) -> None:
            raise TimeoutError("key test hung")

        old = signal.signal(signal.SIGALRM, too_slow)
        signal.alarm(10)
        self.addCleanup(signal.signal, signal.SIGALRM, old)
        self.addCleanup(signal.alarm, 0)

    def wait_for_stop(self) -> None:
        for _ in range(200):
            if self.keys.stop_requested():
                return
            threading.Event().wait(0.01)
        self.fail("no stop")

    def test_a_bare_enter_stops_and_leaves_no_line(self) -> None:
        self.keys.begin()
        self.assertFalse(self.keys.stop_requested())
        os.write(self.master, b"\n")
        self.wait_for_stop()
        self.assertIsNone(self.keys.take_line())

    def test_a_typed_line_stops_and_is_kept_once_for_the_chat(self) -> None:
        self.keys.begin()
        os.write(self.master, b"and tomorrow?\n")
        self.wait_for_stop()
        self.assertEqual(self.keys.take_line(), "and tomorrow?")
        self.assertIsNone(self.keys.take_line())

    def test_keys_typed_before_the_reply_do_not_stop_it(self) -> None:
        os.write(self.master, b"\n")
        threading.Event().wait(0.05)
        self.keys.begin()
        self.assertFalse(self.keys.stop_requested())


if __name__ == "__main__":
    unittest.main()
