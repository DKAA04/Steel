"""Voice core: config, consent file, the REC line, the recorder and the keys.

Fake audio only. No test opens a microphone or a speaker, loads a model, uses the
network or touches the real ~/.openjarvis (each test has a temporary directory).
"""

from __future__ import annotations

import json
import os
import pty
import re
import signal
import stat
import tempfile
import termios
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from voice_helpers import BrokenTTY, FakeSource, FakeTTY, NotATTY, quiet, tone

from steel.voice.config import VoiceConfig, voice_config
from steel.voice.consent import (
    CONSENT_TEXT,
    consent_path,
    has_consent,
    record_consent,
    revoke_consent,
)
from steel.voice.indicator import Indicator, IndicatorError, IndicatorShown
from steel.voice.recorder import record, rms, wipe
from steel.voice.terminal import Terminal

DOCS = Path(__file__).resolve().parent.parent / "docs" / "VOICE.md"


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class ConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        config = voice_config({})
        self.assertEqual(config, VoiceConfig())
        self.assertEqual(config.max_seconds, 30.0)
        self.assertEqual(config.languages, ("en", "es", "it"))
        self.assertEqual(config.model, "small")

    def test_the_maximum_length_is_configurable_but_capped(self) -> None:
        self.assertEqual(
            voice_config({"STEEL_VOICE_MAX_SECONDS": "12"}).max_seconds, 12.0
        )
        self.assertEqual(
            voice_config({"STEEL_VOICE_MAX_SECONDS": "100000"}).max_seconds, 120.0
        )

    def test_a_bad_number_falls_back_to_the_default(self) -> None:
        for bad in ("abc", "", "nan", "inf", "-5", "0"):
            config = voice_config({"STEEL_VOICE_MAX_SECONDS": bad})
            self.assertEqual(config.max_seconds, 30.0, bad)

    def test_language_forms(self) -> None:
        self.assertEqual(voice_config({"STEEL_VOICE_LANGUAGE": "auto"}).languages, ())
        self.assertEqual(
            voice_config({"STEEL_VOICE_LANGUAGE": "es"}).languages, ("es",)
        )
        self.assertEqual(
            voice_config({"STEEL_VOICE_LANGUAGE": "EN, it"}).languages, ("en", "it")
        )
        self.assertEqual(
            voice_config({"STEEL_VOICE_LANGUAGE": "en;rm -rf"}).languages,
            ("en", "es", "it"),
        )

    def test_model_and_device_cannot_carry_paths_or_options(self) -> None:
        for bad in ("../small", "/etc", "-D", "a b", ""):
            self.assertEqual(
                voice_config({"STEEL_VOICE_MODEL": bad}).model, "small", bad
            )
            self.assertEqual(
                voice_config({"STEEL_VOICE_DEVICE": bad}).device, "default", bad
            )
        self.assertEqual(
            voice_config({"STEEL_VOICE_DEVICE": "plughw:CARD=Generic_1,DEV=0"}).device,
            "plughw:CARD=Generic_1,DEV=0",
        )
        self.assertEqual(voice_config({"STEEL_VOICE_MODEL": "base"}).model, "base")


class ConsentTest(TempDirCase):
    def test_no_file_means_no_consent(self) -> None:
        self.assertFalse(has_consent(self.dir))

    def test_recording_consent_writes_only_a_timestamp_with_mode_600(self) -> None:
        stamp = record_consent(self.dir, now=datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
        self.assertTrue(has_consent(self.dir))
        path = consent_path(self.dir)
        self.assertEqual(json.loads(path.read_text()), {"consented_at": stamp})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_an_existing_wide_open_file_becomes_private_again(self) -> None:
        path = consent_path(self.dir)
        path.write_text("old")
        path.chmod(0o644)
        record_consent(self.dir)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_revoking_deletes_the_file(self) -> None:
        record_consent(self.dir)
        self.assertTrue(revoke_consent(self.dir))
        self.assertFalse(consent_path(self.dir).exists())
        self.assertFalse(has_consent(self.dir))
        self.assertFalse(revoke_consent(self.dir))  # nothing left to revoke

    def test_a_malformed_file_is_not_consent(self) -> None:
        path = consent_path(self.dir)
        for text in (
            "",
            "yes",
            "[]",
            "{}",
            '{"consented_at": "not a date"}',
            '{"consented_at": 5}',
            '{"consented_at": "2026-10-24T12:00:00+00:00", "extra": 1}',
        ):
            path.write_text(text)
            path.chmod(0o600)
            self.assertFalse(has_consent(self.dir), text)

    def test_a_symlink_is_not_consent(self) -> None:
        real = self.dir / "real.json"
        real.write_text('{"consented_at": "2026-10-24T12:00:00+00:00"}')
        real.chmod(0o600)
        consent_path(self.dir).symlink_to(real)
        self.assertFalse(has_consent(self.dir))

    def test_a_file_others_can_write_is_not_consent(self) -> None:
        record_consent(self.dir)
        consent_path(self.dir).chmod(0o662)
        self.assertFalse(has_consent(self.dir))

    def test_recording_consent_does_not_write_through_a_symlink(self) -> None:
        victim = self.dir / "victim.txt"
        victim.write_text("untouched")
        consent_path(self.dir).symlink_to(victim)
        with self.assertRaises(OSError):
            record_consent(self.dir)
        self.assertEqual(victim.read_text(), "untouched")

    def test_the_consent_text_is_the_one_in_the_design_document(self) -> None:
        doc = DOCS.read_text(encoding="utf-8")
        quoted = [
            re.sub(r"^> ?", "", line).replace("**", "")
            for line in doc.splitlines()
            if line.startswith(">")
        ]
        self.assertEqual("\n".join(quoted).strip(), CONSENT_TEXT.strip())

    def test_the_consent_text_says_the_things_the_owner_must_know(self) -> None:
        for phrase in (
            "everyone who is speaking near it",
            "No audio file is written",
            "nothing is sent over the network",
            "steel voice revoke",
            "granted the microphone tool by hand".replace("granted", "grant"),
        ):
            self.assertIn(phrase, CONSENT_TEXT, phrase)


class IndicatorTest(unittest.TestCase):
    def test_show_prints_the_rec_line_and_returns_proof(self) -> None:
        out = FakeTTY()
        shown = Indicator(out).show(30)
        self.assertIsInstance(shown, IndicatorShown)
        self.assertIn("● REC 00:00", out.getvalue())

    def test_no_terminal_means_no_proof(self) -> None:
        with self.assertRaises(IndicatorError):
            Indicator(NotATTY()).show(30)

    def test_a_failing_write_means_no_proof(self) -> None:
        with self.assertRaises(IndicatorError):
            Indicator(BrokenTTY()).show(30)

    def test_the_proof_cannot_be_made_by_hand(self) -> None:
        with self.assertRaises(TypeError):
            IndicatorShown("● REC", object())

    def test_it_shows_elapsed_seconds_and_the_limit(self) -> None:
        out = FakeTTY()
        indicator = Indicator(out)
        indicator.show(30)
        indicator.update(7.4, 30)
        self.assertIn("● REC 00:07", out.getvalue())
        self.assertIn("30 s max", out.getvalue())
        indicator.stopped(7.4)
        self.assertIn("microphone closed", out.getvalue())


class RecorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = FakeTTY()
        self.indicator = Indicator(self.screen)

    def run_record(self, source: FakeSource, **config: float) -> object:
        return record(source, self.indicator, VoiceConfig(**config))

    def test_the_rec_line_is_printed_before_the_source_is_opened(self) -> None:
        source = FakeSource(tone(0.3), screen=self.screen)
        record(source, self.indicator, VoiceConfig())
        self.assertIsNotNone(source.screen_at_open)
        self.assertIn("● REC", source.screen_at_open or "")

    def test_without_a_terminal_the_source_is_never_opened(self) -> None:
        source = FakeSource(tone(1))
        with self.assertRaises(IndicatorError):
            record(source, Indicator(NotATTY()), VoiceConfig())
        self.assertEqual((source.opened, source.reads, source.closed), (0, 0, 0))

    def test_a_failing_print_means_the_source_is_never_opened(self) -> None:
        source = FakeSource(tone(1))
        with self.assertRaises(IndicatorError):
            record(source, Indicator(BrokenTTY()), VoiceConfig())
        self.assertEqual(source.opened, 0)

    def test_the_source_is_opened_then_closed_exactly_once(self) -> None:
        source = FakeSource(tone(0.5))
        record(source, self.indicator, VoiceConfig())
        self.assertEqual(source.events, ["open", "close"])

    def test_recording_stops_at_the_maximum_length(self) -> None:
        source = FakeSource(forever=tone(0.1)[0])
        rec = record(source, self.indicator, VoiceConfig(max_seconds=2.0))
        self.assertEqual(rec.reason, "max_length")
        self.assertAlmostEqual(rec.seconds, 2.0, places=3)
        self.assertEqual(source.closed, 1)
        self.assertLessEqual(source.reads, 21)  # it did not keep reading

    def test_the_silence_cut_off_ends_the_recording_after_speech(self) -> None:
        source = FakeSource(tone(1.0) + quiet(10.0))
        rec = record(
            source,
            self.indicator,
            VoiceConfig(max_seconds=30, silence_seconds=1.5),
        )
        self.assertEqual(rec.reason, "silence")
        self.assertAlmostEqual(rec.seconds, 2.5, places=3)  # 1 s speech + 1.5 s quiet
        self.assertTrue(rec.heard_speech)

    def test_silence_does_not_cut_while_the_owner_is_still_talking(self) -> None:
        source = FakeSource(tone(1.0) + quiet(1.0) + tone(1.0) + quiet(3.0))
        rec = record(source, self.indicator, VoiceConfig(silence_seconds=1.5))
        self.assertAlmostEqual(rec.seconds, 4.5, places=3)  # the 1 s pause was kept

    def test_nobody_speaking_gives_up_after_the_startup_wait(self) -> None:
        source = FakeSource(quiet(20.0))
        rec = record(
            source,
            self.indicator,
            VoiceConfig(startup_silence_seconds=5.0, max_seconds=30),
        )
        self.assertEqual(rec.reason, "no_speech")
        self.assertAlmostEqual(rec.seconds, 5.0, places=3)
        self.assertFalse(rec.heard_speech)

    def test_the_stop_key_ends_the_recording(self) -> None:
        source = FakeSource(forever=tone(0.1)[0])
        presses = iter([False] * 4 + [True])
        rec = record(
            source,
            self.indicator,
            VoiceConfig(max_seconds=30),
            stop_requested=lambda: next(presses),
        )
        self.assertEqual(rec.reason, "stopped")
        self.assertAlmostEqual(rec.seconds, 0.5, places=3)

    def test_cancelling_ends_the_recording_and_closes_the_source(self) -> None:
        source = FakeSource(forever=tone(0.1)[0])
        flags = iter([False, False, True])
        rec = record(
            source,
            self.indicator,
            VoiceConfig(max_seconds=30),
            cancelled=lambda: next(flags),
        )
        self.assertEqual(rec.reason, "cancelled")
        self.assertEqual(source.closed, 1)

    def test_slow_audio_is_cut_by_the_wall_clock(self) -> None:
        now = [0.0]

        def clock() -> float:
            now[0] += 3.0  # each read "takes" three seconds
            return now[0]

        source = FakeSource(forever=tone(0.1)[0])
        rec = record(source, self.indicator, VoiceConfig(max_seconds=2.0), clock=clock)
        self.assertEqual(rec.reason, "max_length")
        self.assertLess(rec.seconds, 2.0)  # stopped on time, not on audio length

    def test_a_read_error_still_closes_the_source_and_says_so(self) -> None:
        source = FakeSource(fail_read=OSError("device gone"))
        with self.assertRaises(OSError):
            record(source, self.indicator, VoiceConfig())
        self.assertEqual(source.closed, 1)
        self.assertIn("microphone closed", self.screen.getvalue())

    def test_audio_already_recorded_is_wiped_when_reading_fails_midway(self) -> None:
        class Breaks(FakeSource):
            def read(self) -> bytes:
                if not self.chunks:
                    raise OSError("device gone")
                return super().read()

        seen: dict[str, bytes] = {}

        def spy(buffer: bytearray) -> None:
            seen["before"] = bytes(buffer)
            wipe(buffer)
            seen["after"] = bytes(buffer)

        with (
            mock.patch("steel.voice.recorder.wipe", side_effect=spy),
            self.assertRaises(OSError),
        ):
            record(Breaks(tone(0.5)), self.indicator, VoiceConfig())
        self.assertTrue(any(seen["before"]))  # there was audio to lose
        self.assertFalse(any(seen["after"]))  # and it was zeroed before raising

    def test_a_source_that_fails_to_open_is_reported_and_not_read(self) -> None:
        class Refuses(FakeSource):
            def open(self, shown: IndicatorShown) -> None:
                raise OSError("busy")

        source = Refuses(tone(1))
        with self.assertRaises(OSError):
            record(source, self.indicator, VoiceConfig())
        self.assertEqual(source.reads, 0)
        self.assertIn("did not open", self.screen.getvalue())

    def test_the_rec_line_is_redrawn_while_recording(self) -> None:
        record(FakeSource(tone(2.2)), self.indicator, VoiceConfig(max_seconds=30))
        shown = self.screen.getvalue()
        self.assertIn("● REC 00:01", shown)
        self.assertIn("● REC 00:02", shown)

    def test_audio_can_be_wiped(self) -> None:
        rec = record(FakeSource(tone(0.5)), self.indicator, VoiceConfig())
        self.assertTrue(any(rec.audio))
        wipe(rec.audio)
        self.assertFalse(any(rec.audio))
        self.assertEqual(len(rec.audio), 16000)  # same length, all zeros

    def test_the_level_meter(self) -> None:
        self.assertEqual(rms(tone(0.1, 3000)[0]), 3000)
        self.assertEqual(rms(quiet(0.1)[0]), 0)
        self.assertEqual(rms(b""), 0)


class TerminalTest(unittest.TestCase):
    """Real pseudo-terminal: no microphone, but real key handling.

    Keys are pressed from a timer *after* the call is waiting, because the
    terminal code deliberately throws away keys typed before it starts. An
    alarm turns a hang into a failure instead of stalling the whole suite.
    """

    def setUp(self) -> None:
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.out = FakeTTY()
        self.terminal = Terminal(self.slave, self.out)

        def too_slow(*_: object) -> None:
            raise TimeoutError("terminal test hung")

        old = signal.signal(signal.SIGALRM, too_slow)
        signal.alarm(10)
        self.addCleanup(signal.signal, signal.SIGALRM, old)
        self.addCleanup(signal.alarm, 0)

    def press(self, data: bytes) -> None:
        os.write(self.master, data)

    def press_soon(self, data: bytes, delay: float = 0.15) -> None:
        timer = threading.Timer(delay, self.press, args=(data,))
        timer.start()
        self.addCleanup(timer.cancel)

    def test_enter_starts_and_the_prompt_is_shown(self) -> None:
        self.press_soon(b"\n")
        self.assertTrue(self.terminal.wait_for_start(timeout=5))
        self.assertIn("Press Enter to start", self.out.getvalue())

    def test_no_key_means_no_start(self) -> None:
        self.assertFalse(self.terminal.wait_for_start(timeout=0.2))
        self.assertIn("was not opened", self.out.getvalue())

    def test_keys_typed_earlier_do_not_start_a_recording(self) -> None:
        self.press(b"\n\n")  # typed before the prompt appeared
        self.assertFalse(self.terminal.wait_for_start(timeout=0.2))

    def test_without_a_terminal_there_is_no_start(self) -> None:
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)
        os.write(write_end, b"\n")
        self.assertFalse(Terminal(read_end, FakeTTY()).wait_for_start(timeout=1))
        self.assertFalse(Terminal(read_end, FakeTTY()).confirm())

    def test_enter_at_the_chat_prompt_counts_as_the_start_key(self) -> None:
        self.terminal.arm()
        self.assertTrue(self.terminal.wait_for_start(timeout=0.2))
        self.assertNotIn("Press Enter", self.out.getvalue())
        # ...once: the next recording needs a new key press.
        self.assertFalse(self.terminal.wait_for_start(timeout=0.1))

    def test_an_old_arm_expires(self) -> None:
        self.terminal.arm()
        with mock.patch("steel.voice.terminal.time.monotonic", return_value=1e12):
            self.assertFalse(self.terminal.wait_for_start(timeout=0.1))

    def test_stop_is_false_until_enter_is_pressed(self) -> None:
        self.assertFalse(self.terminal.stop_pressed())
        self.press(b"\n")
        self.assertTrue(self.terminal.stop_pressed())
        self.assertFalse(self.terminal.stop_pressed())  # consumed

    def test_confirm_enter_sends_and_esc_discards(self) -> None:
        self.press_soon(b"\n")
        self.assertTrue(self.terminal.confirm())
        self.press_soon(b"\x1b")
        self.assertFalse(self.terminal.confirm())

    def test_other_keys_and_arrow_keys_do_not_decide(self) -> None:
        # letters, space, up and down arrows, then Enter
        self.press_soon(b"x \x1b[A\x1b[B\n")
        self.assertTrue(self.terminal.confirm())

    def test_keys_pressed_before_the_question_do_not_answer_it(self) -> None:
        self.press(b"\n")  # e.g. Enter pressed while the transcript was computed
        self.press_soon(b"\x1b")
        self.assertFalse(self.terminal.confirm())  # only the later Esc counts

    def test_confirm_restores_the_terminal_settings(self) -> None:
        before = termios.tcgetattr(self.slave)
        self.press_soon(b"\n")
        self.terminal.confirm()
        self.assertEqual(termios.tcgetattr(self.slave), before)


if __name__ == "__main__":
    unittest.main()
