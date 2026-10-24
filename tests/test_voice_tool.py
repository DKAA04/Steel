"""The microphone as a policy-gated tool, and the real source with a fake process.

Fake audio, fake transcriber, fake terminal keys. No microphone, speaker, model
or network is used, and every test has its own temporary OpenJarvis home.
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolCall, ToolResult
from voice_helpers import FakeSource, FakeTTY, NotATTY, quiet, tone

from steel.security import PolicyError, load_home_policy
from steel.security.executor import build_executor
from steel.tools import MICROPHONE_TOOL, get_microphone_tool
from steel.voice.config import VoiceConfig
from steel.voice.consent import record_consent, revoke_consent
from steel.voice.indicator import Indicator, IndicatorShown
from steel.voice.mic import ArecordSource
from steel.voice.stt import SpeechModelMissing, Transcript

GRANTS = "steel_policy.json"
LOG = "steel_policy_audit.jsonl"
SECRET = "my password is swordfish and my sister Mara lives in Turin"


def voice_grant(pattern: str = MICROPHONE_TOOL, agent: str = "voice") -> dict[str, Any]:
    return {
        "agents": [
            {
                "agent_id": agent,
                "grants": [{"capability": "sensor:microphone", "pattern": pattern}],
            }
        ]
    }


class FakePushToTalk:
    def __init__(self, start: bool = True) -> None:
        self.start = start
        self.start_calls = 0
        self.stop_after: int | None = None
        self._stop_polls = 0
        self.hold: threading.Event | None = None  # blocks the start key if set

    def wait_for_start(self, timeout: float) -> bool:
        self.start_calls += 1
        if self.hold is not None:
            self.hold.wait(5)
        return self.start

    def stop_pressed(self) -> bool:
        self._stop_polls += 1
        return self.stop_after is not None and self._stop_polls > self.stop_after


class FakeTranscriber:
    def __init__(self, text: str = SECRET) -> None:
        self.text = text
        self.fail: Exception | None = None
        self.buffers: list[bytearray] = []
        self.heard: list[bytes] = []

    def transcribe(self, audio: bytes | bytearray) -> Transcript:
        assert isinstance(audio, bytearray)
        self.buffers.append(audio)
        self.heard.append(bytes(audio))
        if self.fail is not None:
            raise self.fail
        return Transcript(self.text, "en")


class VoiceCase(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.screen = FakeTTY()
        self.ptt = FakePushToTalk()
        self.transcriber = FakeTranscriber()
        self.sources: list[FakeSource] = []
        self.chunks = lambda: tone(1.0) + quiet(3.0)

    def write_grants(self, data: object) -> None:
        path = self.home / GRANTS
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        path.chmod(0o600)

    def open_source(self) -> FakeSource:
        source = FakeSource(self.chunks(), screen=self.screen)
        self.sources.append(source)
        return source

    def tool(self, **overrides: Any) -> Any:
        options: dict[str, Any] = {
            "config": VoiceConfig(),
            "open_source": self.open_source,
            "transcriber": self.transcriber,
            "push_to_talk": self.ptt,
            "indicator": Indicator(self.screen),
        }
        options.update(overrides)
        return get_microphone_tool(self.home, **options)

    def executor(self, tool: Any = None, agent: str = "voice") -> Any:
        return build_executor(
            [tool or self.tool()],
            policy=load_home_policy(self.home),
            agent_id=agent,
        )

    def listen(self, tool: Any = None, agent: str = "voice") -> ToolResult:
        call = ToolCall(id="t", name=MICROPHONE_TOOL, arguments="{}")
        return self.executor(tool, agent).execute(call)

    def log_lines(self) -> list[dict[str, str]]:
        path = self.home / LOG
        return (
            [json.loads(x) for x in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    def opened(self) -> int:
        return sum(s.opened for s in self.sources)

    def allow_everything(self) -> None:
        record_consent(self.home)
        self.write_grants(voice_grant())


class PermissionTest(VoiceCase):
    def test_the_tool_declares_exactly_one_capability_and_no_arguments(self) -> None:
        spec = self.tool().spec
        self.assertEqual(spec.name, "microphone_listen")
        self.assertEqual(spec.required_capabilities, ["sensor:microphone"])
        self.assertEqual(spec.parameters["properties"], {})
        self.assertIs(spec.parameters["additionalProperties"], False)

    def test_no_grant_means_denied_logged_and_no_sound_captured(self) -> None:
        record_consent(self.home)  # consent alone is not enough
        self.assertFalse((self.home / GRANTS).exists())
        result = self.listen()
        self.assertFalse(result.success)
        self.assertIn("Capability 'sensor:microphone' denied", result.content)
        self.assertEqual(self.opened(), 0)
        self.assertEqual(self.sources, [])  # not even a source was created
        self.assertEqual(self.screen.getvalue(), "")  # no REC line either
        self.assertEqual(self.ptt.start_calls, 0)
        (entry,) = self.log_lines()
        self.assertEqual(
            (entry["agent_id"], entry["capability"], entry["decision"]),
            ("voice", "sensor:microphone", "deny"),
        )

    def test_a_grant_without_consent_still_opens_nothing(self) -> None:
        self.write_grants(voice_grant())  # grant, but no consent file
        result = self.listen()
        self.assertFalse(result.success)
        self.assertIn("No voice consent", result.content)
        self.assertEqual((self.opened(), self.ptt.start_calls), (0, 0))
        self.assertEqual(self.screen.getvalue(), "")
        # The policy did allow it; the consent check in the tool refused it.
        self.assertEqual(self.log_lines()[-1]["decision"], "allow")

    def test_grant_and_consent_allow_a_recording(self) -> None:
        self.allow_everything()
        result = self.listen()
        self.assertTrue(result.success, result.content)
        self.assertEqual(result.content, SECRET)
        self.assertEqual(self.opened(), 1)

    def test_a_wildcard_sensor_grant_makes_the_whole_file_invalid(self) -> None:
        record_consent(self.home)
        for capability, pattern in (
            ("sensor:*", MICROPHONE_TOOL),
            ("*", MICROPHONE_TOOL),
            ("sensor:microphone", "*"),  # exact capability, but not an exact tool
        ):
            grants = {
                "agents": [
                    {
                        "agent_id": "voice",
                        "grants": [{"capability": capability, "pattern": pattern}],
                    }
                ]
            }
            self.write_grants(grants)
            with self.assertRaises(PolicyError, msg=(capability, pattern)):
                load_home_policy(self.home)

    def test_a_grant_for_some_other_tool_name_does_not_allow_it(self) -> None:
        record_consent(self.home)
        self.write_grants(voice_grant(pattern="camera_snapshot"))
        result = self.listen()
        self.assertFalse(result.success)
        self.assertEqual(self.opened(), 0)
        self.assertEqual(self.log_lines()[-1]["reason"], "no_grant")

    def test_another_agent_is_not_allowed(self) -> None:
        self.allow_everything()
        result = self.listen(agent="cli")
        self.assertFalse(result.success)
        self.assertEqual(self.opened(), 0)

    def test_an_invalid_grants_file_means_nothing_is_granted(self) -> None:
        record_consent(self.home)
        self.write_grants("not json")
        with self.assertRaises(PolicyError):
            load_home_policy(self.home)

    def test_revoking_consent_makes_the_microphone_refuse_again(self) -> None:
        self.allow_everything()
        self.assertTrue(self.listen().success)
        self.assertTrue(revoke_consent(self.home))
        again = self.listen()
        self.assertFalse(again.success)
        self.assertIn("No voice consent", again.content)
        self.assertEqual(self.opened(), 1)  # still only the first recording

    def test_unexpected_arguments_are_refused(self) -> None:
        self.allow_everything()
        call = ToolCall(id="t", name=MICROPHONE_TOOL, arguments='{"seconds": 9999}')
        result = self.executor().execute(call)
        self.assertFalse(result.success)
        self.assertEqual(self.opened(), 0)

    def test_the_tool_is_not_registered_with_openjarvis(self) -> None:
        before = set(ToolRegistry.keys())
        self.tool()
        self.assertEqual(set(ToolRegistry.keys()), before)
        self.assertNotIn(MICROPHONE_TOOL, ToolRegistry.keys())


class RecordingFlowTest(VoiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.allow_everything()

    def test_the_rec_line_is_on_screen_before_the_microphone_opens(self) -> None:
        self.listen()
        self.assertIn("● REC", self.sources[0].screen_at_open or "")

    def test_without_a_terminal_the_microphone_is_never_opened(self) -> None:
        tool = self.tool(indicator=Indicator(NotATTY()))
        result = self.listen(tool)
        self.assertFalse(result.success)
        self.assertIn("needs a terminal", result.content)
        self.assertEqual(self.opened(), 0)
        self.assertEqual(self.transcriber.heard, [])

    def test_without_the_start_key_the_microphone_is_never_opened(self) -> None:
        self.ptt.start = False
        result = self.listen()
        self.assertFalse(result.success)
        self.assertEqual((self.sources, self.screen.getvalue()), ([], ""))

    def test_the_result_is_the_transcript_and_never_audio(self) -> None:
        result = self.listen()
        self.assertIsInstance(result.content, str)
        self.assertEqual(result.content, SECRET)
        self.assertEqual(result.metadata["language"], "en")
        for value in result.metadata.values():
            self.assertNotIsInstance(value, (bytes, bytearray))

    def test_the_microphone_is_closed_after_the_recording(self) -> None:
        self.listen()
        self.assertEqual(self.sources[0].events, ["open", "close"])

    def test_the_audio_buffer_is_wiped_after_transcription(self) -> None:
        self.listen()
        self.assertTrue(any(self.transcriber.heard[0]))  # it did hear something
        self.assertFalse(any(self.transcriber.buffers[0]))  # and then it was zeroed

    def test_the_audio_buffer_is_wiped_when_transcription_fails(self) -> None:
        self.transcriber.fail = RuntimeError(f"boom {SECRET}")
        result = self.listen()
        self.assertFalse(result.success)
        self.assertFalse(any(self.transcriber.buffers[0]))
        self.assertEqual(self.sources[0].closed, 1)
        self.assertNotIn("swordfish", result.content)  # no text in the message

    def test_a_missing_model_is_reported_by_name_of_the_problem(self) -> None:
        self.transcriber.fail = SpeechModelMissing("speech model not found at /x")
        result = self.listen()
        self.assertFalse(result.success)
        self.assertIn("speech model not found", result.content)

    def test_recording_stops_at_the_configured_maximum_length(self) -> None:
        self.chunks = lambda: tone(60.0)
        tool = self.tool(config=VoiceConfig(max_seconds=2.0))
        result = self.listen(tool)
        self.assertEqual(result.metadata["stopped_by"], "max_length")
        self.assertAlmostEqual(result.metadata["seconds"], 2.0, places=1)
        self.assertEqual(len(self.transcriber.heard[0]), 2 * 32000)

    def test_the_silence_cut_off_ends_it_early(self) -> None:
        result = self.listen()
        self.assertEqual(result.metadata["stopped_by"], "silence")
        self.assertAlmostEqual(result.metadata["seconds"], 2.5, places=1)

    def test_the_stop_key_ends_it(self) -> None:
        self.chunks = lambda: tone(30.0)
        self.ptt.stop_after = 6
        result = self.listen()
        self.assertEqual(result.metadata["stopped_by"], "stopped")

    def test_nothing_heard_is_a_success_with_empty_text(self) -> None:
        self.transcriber.text = ""
        self.chunks = lambda: quiet(10.0)
        result = self.listen()
        self.assertTrue(result.success)
        self.assertEqual(result.content, "")

    def test_a_microphone_that_delivers_nothing_is_reported(self) -> None:
        self.chunks = list
        result = self.listen()
        self.assertFalse(result.success)
        self.assertIn("No audio arrived", result.content)
        self.assertEqual(self.transcriber.heard, [])

    def test_a_microphone_that_cannot_be_opened_is_reported(self) -> None:
        class Busy(FakeSource):
            def open(self, shown: IndicatorShown) -> None:
                raise OSError("busy")

        tool = self.tool(open_source=lambda: Busy())
        result = self.listen(tool)
        self.assertFalse(result.success)
        self.assertIn("could not be opened", result.content)

    def test_cancelling_from_another_thread_stops_and_closes_the_microphone(
        self,
    ) -> None:
        endless = FakeSource(forever=tone(0.1)[0], screen=self.screen, delay=0.01)
        tool = self.tool(
            config=VoiceConfig(max_seconds=120), open_source=lambda: endless
        )
        timer = threading.Timer(0.2, tool.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.listen(tool)
        self.assertFalse(result.success)
        self.assertEqual(result.content, "Cancelled.")
        self.assertGreaterEqual(endless.closed, 1)  # close() is safe to repeat
        self.assertFalse(any(self.transcriber.heard))  # nothing was transcribed

    def test_a_cancel_from_an_earlier_call_does_not_cancel_the_next_one(self) -> None:
        tool = self.tool()
        tool.cancel()  # e.g. a Ctrl-C that arrived after the last recording ended
        result = self.listen(tool)
        self.assertTrue(result.success, result.content)
        self.assertEqual(self.opened(), 1)

    def test_cancel_ends_a_recording_whose_device_stopped_delivering_audio(
        self,
    ) -> None:
        # A blocked read never sees the cancel flag; closing the source is what ends it.
        class Stalled(FakeSource):
            def __init__(self) -> None:
                super().__init__(screen=screen)
                self.unblock = threading.Event()

            def read(self) -> bytes:
                self.unblock.wait(10)
                return b""

            def close(self) -> None:
                super().close()
                self.unblock.set()

        screen = self.screen
        stalled = Stalled()
        tool = self.tool(open_source=lambda: stalled)
        timer = threading.Timer(0.2, tool.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        result = self.listen(tool)
        self.assertLess(time.monotonic() - started, 5)  # not the 10 s of the stall
        self.assertEqual(result.content, "Cancelled.")
        self.assertGreaterEqual(stalled.closed, 1)

    def test_cancel_when_nothing_is_recording_is_harmless(self) -> None:
        tool = self.tool()
        tool.cancel()
        tool.cancel()
        self.assertTrue(tool.wait_idle(0.1))

    def test_cancel_after_a_finished_recording_does_not_touch_the_old_source(
        self,
    ) -> None:
        tool = self.tool()
        self.assertTrue(self.listen(tool).success)
        closed_before = self.sources[0].closed
        tool.cancel()
        self.assertEqual(self.sources[0].closed, closed_before)

    def test_two_recordings_cannot_run_at_once(self) -> None:
        tool = self.tool()
        self.ptt.hold = threading.Event()
        first: list[ToolResult] = []
        thread = threading.Thread(target=lambda: first.append(tool.execute()))
        thread.start()
        deadline = time.monotonic() + 5
        while self.ptt.start_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        second = tool.execute()
        self.assertFalse(second.success)
        self.assertIn("already in use", second.content)
        self.ptt.hold.set()
        thread.join(5)
        self.assertTrue(first and first[0].success)


class AuditLogTest(VoiceCase):
    def test_the_decision_log_never_holds_transcript_text(self) -> None:
        self.allow_everything()
        result = self.listen()
        self.assertEqual(result.content, SECRET)
        raw = (self.home / LOG).read_text()
        for word in ("swordfish", "Mara", "Turin", "password", SECRET):
            self.assertNotIn(word, raw)
        (entry,) = self.log_lines()
        self.assertEqual(
            set(entry), {"ts", "agent_id", "capability", "decision", "reason"}
        )
        self.assertEqual(entry["decision"], "allow")

    def test_a_denial_is_logged_with_names_only(self) -> None:
        record_consent(self.home)
        self.listen()
        raw = (self.home / LOG).read_text()
        self.assertNotIn(SECRET, raw)
        self.assertEqual(self.log_lines()[0]["reason"], "unknown_agent")


class AudioNotOnDiskTest(VoiceCase):
    def test_a_whole_capture_writes_no_audio_and_no_temporary_file(self) -> None:
        self.allow_everything()
        with tempfile.TemporaryDirectory() as scratch:
            before_home = {p.name for p in self.home.rglob("*")}
            before_tmp = set(os.listdir(scratch))
            real_open = builtins.open

            def guarded(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
                if any(flag in mode for flag in "wax+"):
                    raise AssertionError(f"tried to write {file!r}")
                return real_open(file, mode, *a, **k)

            def forbidden(*_: Any, **__: Any) -> Any:
                raise AssertionError("a temporary file was requested")

            with (
                mock.patch.dict(os.environ, {"TMPDIR": scratch}),
                mock.patch.object(tempfile, "tempdir", scratch),
                mock.patch.object(tempfile, "mkstemp", forbidden),
                mock.patch.object(tempfile, "NamedTemporaryFile", forbidden),
                mock.patch.object(tempfile, "TemporaryDirectory", forbidden),
                mock.patch.object(builtins, "open", guarded),
            ):
                self.assertTrue(self.listen().success)
            self.assertEqual(set(os.listdir(scratch)), before_tmp)
            created = {p.name for p in self.home.rglob("*")} - before_home
            self.assertEqual(created, {LOG})  # only the names-only decision log

    def test_no_audio_bytes_appear_in_any_file_of_the_home(self) -> None:
        self.allow_everything()
        self.listen()
        needle = tone(0.1)[0][:200]
        for path in self.home.rglob("*"):
            if path.is_file():
                self.assertNotIn(needle, path.read_bytes(), path.name)


class FakeProcess:
    def __init__(self, data: bytes = b"", hang: bool = False) -> None:
        import io

        self.stdout = io.BytesIO(data)
        self.terminated = False
        self.killed = False
        self.waited = 0
        self._hang = hang

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited += 1
        if self._hang and not self.killed and timeout is not None:
            raise subprocess.TimeoutExpired("arecord", timeout)
        return 0


def proof() -> IndicatorShown:
    return Indicator(FakeTTY()).show(30)


class ArecordSourceTest(unittest.TestCase):
    """The real source, with a fake process: no microphone is opened."""

    def setUp(self) -> None:
        self.process = FakeProcess(b"\x01\x02" * 3200)
        self.popen = mock.Mock(return_value=self.process)

    def source(self, **kwargs: Any) -> ArecordSource:
        return ArecordSource(popen=self.popen, **kwargs)

    def test_opening_without_the_rec_proof_starts_nothing(self) -> None:
        source = self.source()
        for forged in (None, "● REC", object(), True):
            with self.assertRaises(PermissionError):
                source.open(forged)  # type: ignore[arg-type]
        self.popen.assert_not_called()

    def test_the_command_is_a_list_with_no_file_and_a_hard_time_limit(self) -> None:
        source = self.source(device="plughw:CARD=Generic_1,DEV=0", max_seconds=12.2)
        source.open(proof())
        (argv,), kwargs = self.popen.call_args
        self.assertEqual(
            argv,
            [
                "arecord", "-q", "-D", "plughw:CARD=Generic_1,DEV=0",
                "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw",
                "-d", "14",  # 12.2 s rounded up, plus one second
            ],
        )  # fmt: skip
        self.assertNotIn("shell", kwargs)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(argv[-1], str(13 + 1))
        # No output file name: the last arguments are options, not a path.
        self.assertFalse(any("/" in a or a.endswith(".wav") for a in argv[1:]))

    def test_a_device_that_looks_like_an_option_is_refused(self) -> None:
        for bad in ("", "-d", "--device=x"):
            with self.assertRaises(ValueError):
                ArecordSource(bad)

    def test_it_reads_chunks_from_the_pipe_and_reports_the_end(self) -> None:
        source = self.source()
        source.open(proof())
        self.assertEqual(len(source.read()), 3200)
        self.assertEqual(len(source.read()), 3200)
        self.assertEqual(source.read(), b"")

    def test_closing_terminates_the_process_and_is_idempotent(self) -> None:
        source = self.source()
        source.open(proof())
        source.close()
        source.close()
        self.assertTrue(self.process.terminated)
        self.assertFalse(self.process.killed)
        self.assertTrue(self.process.stdout.closed)
        self.assertEqual(source.read(), b"")

    def test_a_process_that_will_not_stop_is_killed(self) -> None:
        self.process = FakeProcess(hang=True)
        self.popen.return_value = self.process
        source = self.source()
        source.open(proof())
        source.close()
        self.assertTrue(self.process.killed)

    def test_close_before_open_does_nothing(self) -> None:
        self.source().close()
        self.popen.assert_not_called()

    def test_it_cannot_be_opened_twice(self) -> None:
        source = self.source()
        source.open(proof())
        with self.assertRaises(RuntimeError):
            source.open(proof())

    def test_a_missing_arecord_is_an_oserror(self) -> None:
        self.popen.side_effect = FileNotFoundError("arecord")
        with self.assertRaises(OSError):
            self.source().open(proof())


class ToolHygieneTest(unittest.TestCase):
    def test_the_tool_source_never_builds_an_executor(self) -> None:
        text = (
            Path(__file__).resolve().parent.parent / "steel" / "tools" / "microphone.py"
        ).read_text()
        self.assertNotIn("ToolExecutor(", text)
        self.assertNotIn("build_executor(", text)


if __name__ == "__main__":
    unittest.main()
