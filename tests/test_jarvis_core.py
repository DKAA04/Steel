"""The Jarvis loop's parts (docs/JARVIS.md). Fakes, fake clocks and temporary
homes; where real processes are needed a copy of sleep named "arecord", or a
shell script, stands in. No microphone, speaker, model, window or network."""

from __future__ import annotations

import contextlib
import io
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from jarvis_helpers import (
    QUIET,
    FakeGuard,
    give_consents,
    stand_in_arecord,
)
from voice_helpers import BrokenTTY, FakeTTY, NotATTY

from steel.jarvis import procs
from steel.jarvis.config import JarvisConfig, jarvis_config
from steel.jarvis.consent import (
    CONSENT_TEXT,
    has_wake_consent,
    record_wake_consent,
    revoke_wake_consent,
)
from steel.jarvis.echo import ECHO_PAUSE, EchoGate, PlayerStillRunning, TrackedSink
from steel.jarvis.guard import Guard
from steel.jarvis.guard import main as guard_main
from steel.jarvis.indicators import (
    IndicatorError,
    Indicators,
    ListeningShown,
)
from steel.jarvis.limits import Limits
from steel.jarvis.models import WAKE_FILES, file_ok, models_ready, wake_dir
from steel.jarvis.source import MicUnavailable, RequestSource, WakeSource
from steel.jarvis.wakeword import (
    EMBEDDINGS,
    FRAME_BYTES,
    MEL_WINDOW,
    RAW_RING_SAMPLES,
    OpenWakeWord,
)
from steel.security.policy import (
    EXACT_NAME_CAPS,
    SENSOR_CAPS,
    VOCABULARY,
    PolicyError,
    SteelPolicy,
)

ROOT = Path(__file__).resolve().parent.parent


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- 3f: its own capability ---------------------------------------------------


class CapabilityTest(unittest.TestCase):
    CAP = "sensor:microphone_always_on"

    def test_the_always_on_microphone_is_its_own_sensor(self) -> None:
        self.assertIn(self.CAP, VOCABULARY)
        self.assertIn(self.CAP, SENSOR_CAPS)
        self.assertIn(self.CAP, EXACT_NAME_CAPS)
        self.assertNotEqual(self.CAP, "sensor:microphone")

    def test_a_wildcard_or_star_grant_for_it_is_refused(self) -> None:
        for capability, pattern in (
            (self.CAP, "*"),
            ("sensor:*", "wake_listen"),
            ("*", "wake_listen"),
        ):
            with self.subTest(capability=capability), self.assertRaises(PolicyError):
                SteelPolicy().grant("jarvis", capability, pattern)

    def test_a_star_grant_for_it_makes_the_whole_grants_file_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "steel_policy.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "jarvis",
                                "grants": [{"capability": self.CAP, "pattern": "*"}],
                            }
                        ]
                    }
                )
            )
            path.chmod(0o600)
            with self.assertRaises(PolicyError):
                SteelPolicy.from_file(path)

    def test_only_the_exact_grant_for_agent_jarvis_allows_it(self) -> None:
        exact = SteelPolicy()
        exact.grant("jarvis", self.CAP, "wake_listen")
        self.assertTrue(exact.check("jarvis", self.CAP, "wake_listen"))
        self.assertFalse(exact.check("jarvis", self.CAP, "other_tool"))
        self.assertFalse(exact.check("voice", self.CAP, "wake_listen"))
        push_to_talk = SteelPolicy()
        push_to_talk.grant("jarvis", "sensor:microphone", "wake_listen")
        self.assertFalse(push_to_talk.check("jarvis", self.CAP, "wake_listen"))
        other_agent = SteelPolicy()
        other_agent.grant("voice", self.CAP, "wake_listen")
        self.assertFalse(other_agent.check("jarvis", self.CAP, "wake_listen"))


class WakeToolBuilderTest(base.HomeTestCase):
    """get_wake_tool is the only builder of wake_listen (docs/JARVIS.md 3f)."""

    def test_nothing_else_in_steel_or_scripts_constructs_wake_listen(self) -> None:
        import ast

        builders = []
        for path in [*(ROOT / "steel").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name == "WakeListen":
                        builders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(builders, ["steel/tools/__init__.py"])

    def test_building_it_opens_nothing_and_declares_only_its_sensor(self) -> None:
        from steel.tools import get_wake_tool

        with mock.patch("subprocess.Popen", side_effect=AssertionError("started")):
            tool = get_wake_tool(
                self.home,
                config=JarvisConfig(),
                limits=Limits(JarvisConfig()),
                indicators=Indicators(FakeGuard(), FakeTTY()),
                keys=mock.Mock(),
                in_foreground=lambda: True,
                device="default",
            )
        self.assertEqual(tool.spec.name, "wake_listen")
        self.assertEqual(
            tool.spec.required_capabilities, ["sensor:microphone_always_on"]
        )

    def test_building_it_loads_no_model_or_audio_library(self) -> None:
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from unittest import mock\n"
            "from steel.jarvis.config import JarvisConfig\n"
            "from steel.jarvis.indicators import Indicators\n"
            "from steel.jarvis.limits import Limits\n"
            "from steel.tools import get_wake_tool\n"
            "get_wake_tool(Path('/nonexistent'), config=JarvisConfig(),\n"
            "    limits=Limits(JarvisConfig()), indicators=Indicators(mock.Mock()),\n"
            "    keys=mock.Mock(), in_foreground=lambda: True, device='default')\n"
            "bad = [m for m in ('onnxruntime', 'numpy', 'sounddevice', 'tkinter')\n"
            "       if m in sys.modules]\n"
            "print(bad)\n"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


# --- 3a: consent ----------------------------------------------------------------


class ConsentTest(base.HomeTestCase):
    def test_the_consent_text_says_always_on_and_names_the_risks(self) -> None:
        for phrase in (
            "ALWAYS ON",
            "Everything said near this computer is analysed on this computer",
            "Nothing is recorded, transcribed, stored or sent anywhere before the wake word",
            "Guests, family, children",
            "False triggers will happen",
            "steel jarvis stop",
            "Push-to-talk consent (steel voice consent) does not count",
            "Type yes to agree.",
        ):
            self.assertIn(phrase, CONSENT_TEXT)

    def test_the_text_in_the_design_document_is_the_same_word_for_word(self) -> None:
        doc = (ROOT / "docs" / "JARVIS.md").read_text(encoding="utf-8")
        quoted = doc.split("### 3a.1", 1)[1].split("```", 2)[1].strip("\n")
        self.assertEqual(quoted.removeprefix("text\n"), CONSENT_TEXT)

    def test_its_own_file_and_the_push_to_talk_consent_does_not_count(self) -> None:
        give_consents(self.home, wake=False, voice=True)
        self.assertFalse(has_wake_consent(self.home))
        record_wake_consent(self.home)
        self.assertTrue(has_wake_consent(self.home))
        self.assertEqual(
            oct((self.home / "steel_wake_consent.json").stat().st_mode & 0o777), "0o600"
        )
        self.assertTrue(revoke_wake_consent(self.home))
        self.assertFalse(has_wake_consent(self.home))

    def test_a_consent_file_others_can_write_or_a_symlink_does_not_count(self) -> None:
        record_wake_consent(self.home)
        path = self.home / "steel_wake_consent.json"
        path.chmod(0o622)
        self.assertFalse(has_wake_consent(self.home))
        path.unlink()
        (self.home / "elsewhere.json").write_text(
            '{"consented_at": "2026-10-25T10:00:00+00:00"}'
        )
        path.symlink_to(self.home / "elsewhere.json")
        self.assertFalse(has_wake_consent(self.home))


# --- settings -------------------------------------------------------------------


class ConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        config, problems = jarvis_config({})
        self.assertEqual(config, JarvisConfig())
        self.assertEqual(
            (config.max_minutes, config.idle_minutes, config.max_wakes_per_hour),
            (60, 20, 30),
        )
        self.assertEqual(
            (config.threshold, config.confirm_seconds, config.stt_model),
            (0.5, 3, "base"),
        )
        self.assertEqual(problems, [])

    def test_every_setting_is_validated_and_a_bad_value_reported(self) -> None:
        good = {
            "STEEL_JARVIS_MAX_MINUTES": "240",
            "STEEL_JARVIS_IDLE_MINUTES": "5",
            "STEEL_JARVIS_MAX_WAKES_PER_HOUR": "10",
            "STEEL_JARVIS_THRESHOLD": "0.7",
            "STEEL_JARVIS_CONFIRM_SECONDS": "0",
            "STEEL_JARVIS_STT_MODEL": "small",
        }
        config, problems = jarvis_config(good)
        self.assertEqual(problems, [])
        self.assertEqual(
            (
                config.max_minutes,
                config.idle_minutes,
                config.max_wakes_per_hour,
                config.threshold,
                config.confirm_seconds,
                config.stt_model,
            ),
            (240, 5, 10, 0.7, 0, "small"),
        )
        bad = {
            "STEEL_JARVIS_MAX_MINUTES": "241",
            "STEEL_JARVIS_IDLE_MINUTES": "0",
            "STEEL_JARVIS_MAX_WAKES_PER_HOUR": "lots",
            "STEEL_JARVIS_THRESHOLD": "0.1",
            "STEEL_JARVIS_CONFIRM_SECONDS": "-1",
            "STEEL_JARVIS_STT_MODEL": "large-v3",
        }
        config, problems = jarvis_config(bad)
        self.assertEqual(config, JarvisConfig())
        self.assertEqual(len(problems), 6)
        self.assertTrue(any("241" in p and "60 is used" in p for p in problems))
        self.assertTrue(any("base is used" in p for p in problems))
        for nan in ("nan", "inf"):
            self.assertEqual(
                jarvis_config({"STEEL_JARVIS_THRESHOLD": nan})[0].threshold, 0.5
            )


# --- the model files ------------------------------------------------------------


class ModelsTest(base.HomeTestCase):
    def test_three_files_pinned_with_size_and_sha256_from_one_release(self) -> None:
        self.assertEqual(
            [f.name for f in WAKE_FILES],
            ["melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"],
        )
        for pinned in WAKE_FILES:
            self.assertRegex(pinned.sha256, r"^[0-9a-f]{64}$")
            self.assertTrue(
                pinned.url.startswith(
                    "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
                )
            )

    def test_a_file_counts_only_with_its_size_and_hash_and_not_as_a_link(self) -> None:
        directory = wake_dir(self.home)
        directory.mkdir(parents=True)
        pinned = WAKE_FILES[0]
        with open(directory / pinned.name, "wb") as handle:
            handle.truncate(pinned.size)  # right size, wrong bytes
        self.assertFalse(file_ok(directory / pinned.name, pinned))
        self.assertTrue(file_ok(directory / pinned.name, pinned, verify_hash=False))
        self.assertFalse(models_ready(self.home))
        target = self.home / "real.onnx"
        (directory / pinned.name).rename(target)
        (directory / pinned.name).symlink_to(target)
        self.assertFalse(file_ok(directory / pinned.name, pinned, verify_hash=False))


# --- the wake-word engine (fake ONNX sessions) ---------------------------------


class FakeSessions:
    """Shape-correct stand-ins for the three models; the wake model returns the
    mean of the embeddings, so the score follows the input."""

    def __init__(self) -> None:
        import importlib

        self.np = importlib.import_module("numpy")
        self.wake_inputs: list[Any] = []

    def mel(self, x: Any) -> list[Any]:
        frames = max(1, (x.shape[1] - 400) // 160 + 1)
        level = float(abs(x).mean())
        return [self.np.full((frames, 1, 1, 32), level / 100.0, dtype=self.np.float32)]

    def embedding(self, x: Any) -> list[Any]:
        value = float(x.mean())
        return [self.np.full((x.shape[0], 1, 1, 96), value, dtype=self.np.float32)]

    def wake(self, x: Any) -> list[Any]:
        self.wake_inputs.append(x.shape)
        return [self.np.array([[min(1.0, max(0.0, float(x.mean()) - 2.0))]])]

    def all(self) -> tuple[Any, Any, Any]:
        return (self.mel, self.embedding, self.wake)


class WakeEngineTest(unittest.TestCase):
    def engine(self) -> tuple[OpenWakeWord, FakeSessions]:
        sessions = FakeSessions()
        return OpenWakeWord(sessions=sessions.all()), sessions

    def test_buffers_never_grow_beyond_their_documented_sizes(self) -> None:
        engine, _ = self.engine()
        for _ in range(200):
            engine.score(QUIET)
            self.assertEqual(
                engine.buffer_sizes,
                {
                    "raw_samples": RAW_RING_SAMPLES,
                    "mel_frames": MEL_WINDOW,
                    "embeddings": EMBEDDINGS,
                },
            )
        self.assertEqual(RAW_RING_SAMPLES * 2, 3520)  # 110 ms of raw audio, no more

    def test_the_classifier_sees_16_embeddings_and_the_first_5_scores_are_zero(
        self,
    ) -> None:
        engine, sessions = self.engine()
        loud = b"\xff\x7f" * (FRAME_BYTES // 2)
        scores = [engine.score(loud) for _ in range(8)]
        self.assertEqual(scores[:5], [0.0] * 5)
        self.assertGreater(scores[7], 0.0)
        self.assertEqual(sessions.wake_inputs[-1], (1, EMBEDDINGS, 96))

    def test_only_whole_80_ms_frames_are_accepted(self) -> None:
        engine, _ = self.engine()
        with self.assertRaises(ValueError):
            engine.score(b"\x00" * (FRAME_BYTES - 2))

    def test_wipe_zeroes_every_buffer_and_reset_starts_afresh(self) -> None:
        engine, _ = self.engine()
        for _ in range(10):
            engine.score(b"\x10\x27" * (FRAME_BYTES // 2))
        engine.wipe()
        self.assertFalse(engine._raw.any())
        self.assertFalse(engine._mel.any())
        self.assertFalse(engine._features.any())
        engine.reset()
        self.assertFalse(engine._raw.any())
        self.assertEqual(engine.score(QUIET), 0.0)  # warm-up count starts again

    def test_onnxruntime_is_imported_only_after_telemetry_is_off(self) -> None:
        seen: list[str | None] = []

        def fake_import(name: str) -> Any:
            if name == "onnxruntime":
                seen.append(os.environ.get("ORT_DISABLE_TELEMETRY"))
                raise ImportError("stop here")
            return __import__(name)

        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch(
                "steel.jarvis.wakeword.importlib.import_module", side_effect=fake_import
            ),
        ):
            os.environ.pop("ORT_DISABLE_TELEMETRY", None)
            with self.assertRaises(ImportError):
                OpenWakeWord(Path("/nonexistent"))
        self.assertEqual(seen, ["1"])

    def test_importing_the_jarvis_package_loads_no_audio_or_model_library(self) -> None:
        code = (
            "import sys\n"
            "import steel.jarvis.commands, steel.jarvis.loop, steel.jarvis.listen\n"
            "import steel.jarvis.guard, steel.jarvis.wakeword, steel.jarvis.source\n"
            "bad = [m for m in ('onnxruntime', 'sounddevice', 'tkinter', 'faster_whisper',"
            " 'piper', 'openjarvis.speech') if m in sys.modules]\n"
            "print(','.join(bad))\n"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ},
        )
        self.assertEqual(out.stdout.strip(), "")


class SourceHygieneTest(unittest.TestCase):
    def test_no_jarvis_module_can_download_or_open_a_connection(self) -> None:
        for path in (ROOT / "steel" / "jarvis").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in (
                "urllib",
                "requests",
                "httpx",
                "import socket",
                "download_models",
                "sounddevice",
                "openjarvis.speech",
                "get_speech_backend",
            ):
                self.assertNotIn(banned, text, f"{path.name}: {banned}")

    def test_no_jarvis_module_writes_audio_or_a_file_except_the_pidfile(self) -> None:
        for path in (ROOT / "steel" / "jarvis").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in (
                "wave.open",
                ".wav",
                "NamedTemporaryFile",
                "mkstemp",
                "write_bytes",
                "tofile",
            ):
                self.assertNotIn(banned, text, f"{path.name}: {banned}")
            if path.name != "procs.py":
                self.assertNotIn("os.open(", text, path.name)

    def test_the_loop_never_reaches_slash_commands_or_the_camera(self) -> None:
        for name in ("loop.py", "listen.py", "commands.py"):
            text = (ROOT / "steel" / "jarvis" / name).read_text(encoding="utf-8")
            for banned in (
                "steel.slash",
                "SlashCommands",
                "steel.vision",
                "get_camera_tool",
                "CAMERA_TOOL",
                "memory_search",
                "add_tool_data",
            ):
                self.assertNotIn(banned, text, f"{name}: {banned}")


# --- indicators (3b) ------------------------------------------------------------


class IndicatorsTest(unittest.TestCase):
    def test_both_indicators_come_before_the_proof(self) -> None:
        guard = FakeGuard()
        screen = FakeTTY()
        proof = Indicators(guard, screen).show_listening()
        self.assertIsInstance(proof, ListeningShown)
        self.assertIn("LISTENING for 'hey jarvis'", screen.getvalue())
        self.assertEqual(guard.sent, ["SHOW LISTENING for 'hey jarvis'"])

    def test_no_terminal_or_no_window_means_no_proof(self) -> None:
        for stream, guard in (
            (NotATTY(), FakeGuard()),
            (BrokenTTY(), FakeGuard()),
            (FakeTTY(), FakeGuard(shows=False)),
        ):
            with self.assertRaises(IndicatorError):
                Indicators(guard, stream).show_listening()
        with self.assertRaises(TypeError):
            ListeningShown(object())  # the proof cannot be made by hand


# --- the microphone processes ---------------------------------------------------


class RecordingPopen:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": argv, **kwargs})
        process = mock.Mock()
        process.stdout = io.BytesIO(QUIET * 2 + QUIET[:100])
        process.poll.return_value = None
        process.pid = 4242
        return process


class SourceTest(unittest.TestCase):
    def proof(self) -> ListeningShown:
        return Indicators(FakeGuard(), FakeTTY()).show_listening()

    def test_the_listening_microphone_dies_with_steel_and_ends_by_itself(self) -> None:
        popen = RecordingPopen()
        source = WakeSource(
            "default", 3599.2, popen=popen, which={"setpriv": "/bin/setpriv"}.get
        )
        source.open(self.proof())
        argv = popen.calls[0]["argv"]
        self.assertEqual(argv[:4], ["/bin/setpriv", "--pdeathsig", "KILL", "--"])
        self.assertEqual(argv[4], "arecord")
        self.assertEqual(argv[-2:], ["-d", "3601"])
        self.assertNotIn(">", argv)
        self.assertEqual(popen.calls[0]["stdout"], subprocess.PIPE)
        self.assertEqual(source.read_frame(), QUIET)
        self.assertEqual(source.read_frame(), QUIET)
        self.assertEqual(source.read_frame(), b"")  # a partial frame is the end
        source.close()
        source.close()

    def test_without_the_proof_or_setpriv_the_microphone_never_opens(self) -> None:
        popen = RecordingPopen()
        with self.assertRaises(PermissionError):
            WakeSource(popen=popen, which=lambda _n: "/bin/x").open(object())  # type: ignore[arg-type]
        with self.assertRaises(MicUnavailable):
            WakeSource(popen=popen, which=lambda _n: None).open(self.proof())
        self.assertEqual(popen.calls, [])

    def test_the_request_microphone_also_dies_with_steel(self) -> None:
        source = RequestSource("default", 30, which={"setpriv": "/bin/setpriv"}.get)
        argv = source.command()
        self.assertEqual(
            argv[:5], ["/bin/setpriv", "--pdeathsig", "KILL", "--", "arecord"]
        )
        self.assertIn("31", argv)  # arecord -d: 30 s + 1
        with self.assertRaises(MicUnavailable):
            RequestSource(which=lambda _n: None).command()


# --- limits ---------------------------------------------------------------------


class LimitsTest(unittest.TestCase):
    def test_session_idle_and_wake_rate_limits(self) -> None:
        clock = FakeClock()
        limits = Limits(
            JarvisConfig(max_minutes=60, idle_minutes=20, max_wakes_per_hour=3), clock
        )
        self.assertIsNone(limits.check())
        clock.now += 19 * 60
        self.assertIsNone(limits.check())
        clock.now += 61
        self.assertEqual(limits.check(), "idle")
        self.assertIsNone(limits.wake())  # a wake word resets the idle time
        self.assertIsNone(limits.check())
        self.assertIsNone(limits.wake())
        self.assertIsNone(limits.wake())
        self.assertEqual(limits.wake(), "rate")  # the 4th in an hour
        clock.now = 1000.0 + 60 * 60
        self.assertEqual(limits.check(), "session")
        self.assertLessEqual(limits.session_left(), 0)

    def test_wakes_older_than_an_hour_no_longer_count(self) -> None:
        clock = FakeClock()
        limits = Limits(JarvisConfig(max_minutes=240, max_wakes_per_hour=2), clock)
        limits.wake()
        limits.wake()
        clock.now += 3601
        self.assertIsNone(limits.wake())


# --- echo (section 5) -----------------------------------------------------------


class FakePlayer:
    """aplay: exits when done() is polled after finish, or when stopped."""

    def __init__(self, *, exits: bool = True) -> None:
        self.exits = exits
        self.running = False
        self.finished = False

    def open(self, shown: Any, rate: int) -> None:
        self.running = True

    def write(self, data: memoryview) -> None:
        pass

    def finish(self) -> None:
        self.finished = True

    def done(self) -> bool:
        if self.finished and self.exits:
            self.running = False
        return not self.running

    def stop(self) -> None:
        if self.exits:
            self.running = False


class EchoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.slept: list[float] = []

        def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            if len(self.slept) > 1000:
                raise AssertionError("EchoGate slept too often: a loop is spinning")
            self.clock.now += seconds

        self.gate = EchoGate(clock=self.clock, sleep=sleep)

    def test_listening_waits_for_the_players_exit_plus_half_a_second(self) -> None:
        sink = self.gate.make_sink(FakePlayer())
        sink.open(None, 22050)
        sink.finish()
        self.clock.now += 3.0  # the player still plays what it holds
        self.assertTrue(sink.done())  # ... and has now exited
        exited = sink.exited_at
        self.assertEqual(exited, 1003.0)
        self.clock.now += 0.1  # the speak step returns a moment later
        ready = self.gate.wait(spoken_at=self.clock.now)
        self.assertEqual(ready, exited + ECHO_PAUSE)  # the exit is the signal
        self.assertGreaterEqual(self.clock.now, exited + ECHO_PAUSE)

    def test_an_interrupted_reply_counts_from_the_killed_players_exit(self) -> None:
        sink = self.gate.make_sink(FakePlayer())
        sink.open(None, 22050)
        self.clock.now += 1.0
        sink.stop()  # Enter, a typed line or Ctrl-C
        self.assertEqual(sink.exited_at, 1001.0)
        self.assertEqual(self.gate.wait(self.clock.now), 1001.5)

    def test_a_dead_engine_still_waits_for_the_player_to_drain_and_exit(self) -> None:
        # The engine died: play() ends the reply, tells the player the audio is
        # over (finish) and waits for it, then stops it.
        sink = self.gate.make_sink(FakePlayer())
        sink.open(None, 16000)
        sink.finish()
        self.clock.now += 0.7
        sink.done()
        sink.stop()
        self.assertEqual(sink.exited_at, 1000.7)
        self.assertEqual(self.gate.wait(1000.8), 1001.2)

    def test_with_no_player_the_pause_counts_from_the_end_of_speaking(self) -> None:
        self.assertEqual(self.gate.wait(spoken_at=1000.0), 1000.5)
        self.assertEqual(self.slept, [0.5])

    def test_a_player_that_will_not_exit_keeps_the_microphone_closed(self) -> None:
        sink = self.gate.make_sink(FakePlayer(exits=False))
        sink.open(None, 22050)
        sink.stop()
        self.assertIsNone(sink.exited_at)
        with self.assertRaises(PlayerStillRunning):
            self.gate.wait(self.clock.now)

    def test_the_tracked_sink_passes_everything_to_the_real_player(self) -> None:
        inner = mock.Mock()
        inner.done.return_value = False
        sink = TrackedSink(inner, self.clock)
        sink.open("shown", 22050)  # type: ignore[arg-type]
        sink.write(memoryview(b"ab"))
        sink.finish()
        inner.open.assert_called_once_with("shown", 22050)
        inner.write.assert_called_once()
        inner.finish.assert_called_once_with()


# --- the guard (3b, 3c) -----------------------------------------------------------


class FakeWindow:
    def __init__(self, *, works: bool = True) -> None:
        self.works = works
        self.visible = False
        self.raises: Exception | None = None  # Tk raising (TclError)
        self.texts: list[str] = []

    def show(self, text: str) -> bool:
        self.visible = self.works
        self.texts.append(text)
        return self.works

    def set_text(self, text: str) -> None:
        self.texts.append(text)

    def hide(self) -> None:
        self.visible = False

    def is_up(self) -> bool:
        if self.raises is not None:
            raise self.raises
        return self.visible


def mic(pid: int) -> procs.ProcInfo:
    return procs.ProcInfo(pid, "arecord", "S", 1, 100 + pid)


class GuardLogicTest(unittest.TestCase):
    def guard(
        self, window: FakeWindow, mics: list[procs.ProcInfo]
    ) -> tuple[Guard, list[int], FakeClock]:
        clock = FakeClock()
        killed: list[int] = []

        def kill(info: procs.ProcInfo) -> bool:
            killed.append(info.pid)
            if info in mics:
                mics.remove(info)
            return True

        guard = Guard(
            1,
            window,
            watchdog_seconds=10,
            clock=clock,
            children=lambda _p: list(mics),
            kill=kill,
        )
        return guard, killed, clock

    def test_shown_is_answered_only_when_the_window_is_up(self) -> None:
        guard, _, _ = self.guard(FakeWindow(), [])
        self.assertEqual(guard.command("SHOW LISTENING for 'hey jarvis'"), "SHOWN")
        guard, _, _ = self.guard(FakeWindow(works=False), [])
        self.assertEqual(guard.command("SHOW LISTENING"), "FAILED")

    def test_a_microphone_running_without_the_window_is_killed(self) -> None:
        mics = [mic(7)]
        guard, killed, _ = self.guard(FakeWindow(), mics)
        guard.tick()
        self.assertEqual(killed, [7])

    def test_the_window_goes_only_when_no_microphone_is_left(self) -> None:
        mics = [mic(7)]
        window = FakeWindow()
        guard, killed, _ = self.guard(window, mics)
        guard.command("SHOW LISTENING")
        guard.command("HIDE")
        guard.tick()
        self.assertTrue(window.visible)  # arecord still runs: the window stays
        mics.clear()
        guard.tick()
        self.assertFalse(window.visible)
        self.assertEqual(killed, [])

    def test_the_watchdog_kills_the_microphone_then_the_loop_when_beats_stop(
        self,
    ) -> None:
        mics = [mic(7)]
        window = FakeWindow()
        guard, killed, clock = self.guard(window, mics)
        guard.command("SHOW LISTENING")
        for _ in range(5):
            clock.now += 3
            guard.command("BEAT")
            guard.tick()
        self.assertEqual(killed, [])  # beats keep it alive
        clock.now += 10.5
        with mock.patch(
            "steel.jarvis.guard.proc_info",
            return_value=procs.ProcInfo(1, "steel", "S", 0, 5),
        ):
            guard.tick()
        self.assertEqual(killed[:2], [7, 1])  # the microphone first, then the loop
        self.assertEqual(set(killed), {7, 1})
        self.assertTrue(guard.finished)
        self.assertFalse(window.visible)

    def test_no_watchdog_while_recording_the_request_which_ends_by_itself(self) -> None:
        mics = [mic(8)]
        guard, killed, clock = self.guard(FakeWindow(), mics)
        guard.command("SHOW RECORDING your request")
        clock.now += 60
        guard.tick()
        self.assertEqual(killed, [])

    def test_when_the_loop_goes_every_microphone_seen_is_killed(self) -> None:
        mics = [mic(7), mic(9)]
        guard, killed, _ = self.guard(FakeWindow(), mics)
        guard.command("SHOW LISTENING")
        guard.tick()
        guard.end()
        self.assertEqual(sorted(killed), [7, 9])

    def test_a_window_that_disappears_kills_the_microphones_and_fails(self) -> None:
        mics = [mic(7)]
        window = FakeWindow()
        guard, killed, _ = self.guard(window, mics)
        guard.command("SHOW LISTENING")
        guard.tick()
        self.assertEqual(killed, [])
        window.visible = False  # destroyed, or unmapped by the desktop
        for _ in range(2):
            guard.tick()
        self.assertFalse(guard.finished)  # two ticks are not yet "lost"
        events = guard.tick()
        self.assertTrue(guard.finished and guard.failed)
        self.assertIn(7, killed)
        self.assertIn("window lost", " ".join(events))

    def test_tk_raising_counts_as_a_lost_window(self) -> None:
        mics = [mic(7)]
        window = FakeWindow()
        guard, killed, _ = self.guard(window, mics)
        guard.command("SHOW LISTENING")
        window.raises = RuntimeError("TclError: application has been destroyed")
        for _ in range(3):
            guard.tick()
        self.assertTrue(guard.finished and guard.failed)
        self.assertIn(7, killed)

    def test_a_brief_flicker_does_not_count(self) -> None:
        window = FakeWindow()
        guard, killed, _ = self.guard(window, [mic(7)])
        guard.command("SHOW LISTENING")
        for _ in range(10):
            window.visible = False
            guard.tick()
            guard.tick()
            window.visible = True
            guard.tick()
        self.assertFalse(guard.failed)
        self.assertEqual(killed, [])

    def test_no_window_expected_means_no_loss(self) -> None:
        guard, _, _ = self.guard(FakeWindow(), [])
        for _ in range(10):
            guard.tick()
        self.assertFalse(guard.failed)

    def test_main_stops_with_status_1_and_kills_the_microphone_when_tk_raises(
        self,
    ) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        process = subprocess.Popen([str(stand_in_arecord(Path(tmp.name))), "30"])
        self.addCleanup(lambda: (process.kill(), process.wait()))
        time.sleep(0.1)

        class TkBreaks(FakeWindow):
            def __init__(self) -> None:
                super().__init__()
                self.pumps = 0

            def pump(self) -> None:
                self.pumps += 1
                if self.pumps > 3:
                    raise RuntimeError("TclError: application has been destroyed")

        window = TkBreaks()
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)
        os.write(write_end, b"SHOW LISTENING for 'hey jarvis'\n")
        out = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", mock.Mock(fileno=lambda: read_end)),
            mock.patch.object(sys, "stdout", out),
        ):
            status = guard_main([str(os.getpid()), "10"], make_window=lambda: window)
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue().strip(), "SHOWN")
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(process.poll())  # the microphone was killed


# The real guard code in a real process, with a fake always-up window (there is
# no display): `-c` instead of `-m` so the window can be injected.
_GUARD_CODE = """
import sys
from steel.jarvis import guard

class Window:
    def show(self, text): return True
    def set_text(self, text): pass
    def hide(self): pass
    def is_up(self): return True
    def pump(self): pass

raise SystemExit(guard.main(sys.argv[1:], make_window=Window))
"""
# A microphone stand-in named arecord (its process name is what the guard and
# stop look for): 80 ms frames of silence on stdout for at most about 60 s.
_MIC_SCRIPT = """#!/bin/sh
i=0
while [ $i -lt 1500 ]; do head -c 2560 /dev/zero; sleep 0.04; i=$((i+1)); done
"""


@unittest.skipUnless(shutil.which("setpriv"), "util-linux setpriv is not installed")
class GuardDeathProcessTest(base.HomeTestCase):
    """The guard is killed with SIGKILL while the microphone runs: the loop's
    own check closes the microphone within 2 s (docs/JARVIS.md 3b)."""

    def setUp(self) -> None:
        super().setUp()
        give_consents(self.home)
        ready = mock.patch("steel.jarvis.listen.models_ready", return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.script = Path(tmp.name) / "arecord"
        self.script.write_text(_MIC_SCRIPT)
        self.script.chmod(0o700)

    def real_guard_client(self) -> Any:
        from steel.jarvis.indicators import GuardClient

        def popen(command: list[str], **kwargs: Any) -> Any:
            # Same arguments (loop pid, watchdog), the real guard main().
            return subprocess.Popen(
                [sys.executable, "-I", "-c", _GUARD_CODE, *command[-2:]], **kwargs
            )

        return GuardClient(popen=popen)

    def test_killing_the_real_guard_closes_the_microphone_within_two_seconds(
        self,
    ) -> None:
        from jarvis_helpers import FakeEngine, FakeKeys

        from steel.tools import get_wake_tool

        client = self.real_guard_client()
        client.start()
        self.addCleanup(client.close)
        engine = FakeEngine()
        limits = Limits(JarvisConfig())
        tool = get_wake_tool(
            self.home,
            config=JarvisConfig(),
            limits=limits,
            indicators=Indicators(client, FakeTTY()),
            keys=FakeKeys(),
            in_foreground=lambda: True,
            open_source=lambda seconds: WakeSource(
                "default", seconds, program=str(self.script)
            ),
            make_engine=lambda: engine,
        )
        results: list[Any] = []
        worker = threading.Thread(target=lambda: results.append(tool.execute()))
        worker.start()
        self.addCleanup(worker.join, 10)
        self.addCleanup(tool.cancel)

        def microphones() -> list[procs.ProcInfo]:
            return procs.mic_children(os.getpid())

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (
            microphones() and len(engine.frames) >= 3
        ):
            time.sleep(0.05)
        self.assertTrue(microphones(), "the stand-in microphone never started")
        self.assertGreaterEqual(len(engine.frames), 3, "no frames were read")
        mic_pids = [m.pid for m in microphones()]

        self.assertIsNotNone(client.pid)
        killed_at = time.monotonic()
        os.kill(int(client.pid or 0), signal.SIGKILL)  # the guard, hard
        worker.join(timeout=2.0)
        took = time.monotonic() - killed_at
        self.assertFalse(worker.is_alive(), "the tool still listens 2 s later")
        self.assertLess(took, 2.0)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].success)
        self.assertEqual(results[0].metadata["outcome"], "no_indicator")
        self.assertIn("the microphone is closed", results[0].content)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(
            procs.proc_info(pid) for pid in mic_pids
        ):
            time.sleep(0.05)
        self.assertFalse([pid for pid in mic_pids if procs.proc_info(pid)])


class GuardProcessTest(unittest.TestCase):
    """The guard's process code with a real stand-in microphone process."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.arecord = stand_in_arecord(Path(tmp.name))

    def start_mic(self) -> subprocess.Popen[bytes]:
        process = subprocess.Popen([str(self.arecord), "30"])
        self.addCleanup(lambda: (process.kill(), process.wait()))
        return process

    def wait_gone(self, process: subprocess.Popen[bytes]) -> bool:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(0.05)
        return False

    def test_mic_children_finds_the_real_stand_in_by_name_and_parent(self) -> None:
        process = self.start_mic()
        time.sleep(0.1)
        found = procs.mic_children(os.getpid())
        self.assertIn(process.pid, [p.pid for p in found])

    def test_the_watchdog_kills_a_real_microphone_when_beats_stop(self) -> None:
        process = self.start_mic()
        time.sleep(0.1)
        window = FakeWindow()
        guard = Guard(
            os.getpid(),
            window,
            watchdog_seconds=0.3,
            kill=lambda info: info.comm == "arecord" and procs.kill_exact(info),
        )
        guard.command("SHOW LISTENING")
        deadline = time.monotonic() + 5
        while not guard.finished and time.monotonic() < deadline:
            guard.tick()
            time.sleep(0.05)
        self.assertTrue(self.wait_gone(process))

    def test_the_real_guard_without_a_display_refuses_and_kills_the_microphone(
        self,
    ) -> None:
        # The real guard process, with no display, so no window can ever open:
        # SHOW must answer FAILED, a microphone running without the window is
        # killed, and the guard exits when its input ends (the loop has gone).
        mic = self.start_mic()
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")
        }
        guard = subprocess.Popen(
            [sys.executable, "-I", "-m", "steel.jarvis.guard", str(os.getpid()), "10"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self.addCleanup(lambda: (guard.kill(), guard.wait()))
        assert guard.stdin is not None and guard.stdout is not None
        self.assertTrue(self.wait_gone(mic), "the microphone without a window lives")
        guard.stdin.write(b"SHOW LISTENING for 'hey jarvis'\n")
        guard.stdin.flush()
        ready, _, _ = select.select([guard.stdout], [], [], 10)
        self.assertTrue(ready, "the guard did not answer")
        self.assertEqual(guard.stdout.readline().strip(), b"FAILED")
        guard.stdin.close()
        self.assertEqual(guard.wait(timeout=10), 0)
        guard.stdout.close()

    def test_a_reused_process_id_is_never_killed(self) -> None:
        process = self.start_mic()
        time.sleep(0.1)
        info = procs.proc_info(process.pid)
        assert info is not None
        stale = procs.ProcInfo(
            info.pid, info.comm, info.state, info.ppid, info.start + 1
        )
        self.assertFalse(procs.kill_exact(stale))
        self.assertIsNone(process.poll())


# --- the pidfile and steel jarvis stop (3c) --------------------------------------


@unittest.skipUnless(shutil.which("setpriv"), "util-linux setpriv is not installed")
class StopCommandTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.arecord = stand_in_arecord(Path(tmp.name))

    def hung_loop(self, extra: str = "") -> subprocess.Popen[str]:
        """A fake loop that records itself, starts a stand-in microphone through
        setpriv, ignores SIGTERM and SIGINT, and hangs."""
        code = (
            "import os, signal, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "from steel.jarvis.procs import write_pidfile\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            f"mic = subprocess.Popen(['setpriv', '--pdeathsig', 'KILL', '--', {str(self.arecord)!r}, '60'])\n"
            f"write_pidfile(Path({str(self.home)!r}), os.getpid(), None)\n"
            f"{extra}"
            "print(mic.pid, flush=True)\n"
            "for _ in range(60): time.sleep(1)\n"  # hangs, but never outlives a run
        )
        loop = subprocess.Popen(
            [sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(
            lambda: (loop.kill(), loop.wait(), loop.stdout and loop.stdout.close())
        )
        return loop

    def alive(self, pid: int) -> bool:
        return procs.proc_info(pid) is not None

    def gone(self, pid: int) -> bool:
        deadline = time.monotonic() + 5
        while self.alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        return not self.alive(pid)

    def test_stop_kills_a_hung_loop_and_its_microphone(self) -> None:
        loop = self.hung_loop()
        assert loop.stdout is not None
        mic_pid = int(loop.stdout.readline())
        self.assertTrue(self.alive(mic_pid))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            from steel.jarvis.commands import jarvis_stop

            self.assertEqual(jarvis_stop(self.home), 0)
        self.assertTrue(self.gone(mic_pid))
        self.assertTrue(self.gone(loop.pid))
        self.assertIn("killed microphone process", out.getvalue())
        self.assertIn("pkill -KILL -f 'steel jarvis'", out.getvalue())
        self.assertFalse(procs.pidfile_path(self.home).exists())

    def test_no_microphone_survives_a_sigkill_of_the_loop(self) -> None:
        loop = self.hung_loop()
        assert loop.stdout is not None
        mic_pid = int(loop.stdout.readline())
        os.kill(loop.pid, signal.SIGKILL)
        self.assertTrue(self.gone(mic_pid))

    def test_a_pidfile_naming_a_reused_process_id_kills_nothing(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (sleeper.kill(), sleeper.wait()))
        info = procs.proc_info(sleeper.pid)
        assert info is not None
        procs.pidfile_path(self.home).write_text(
            json.dumps({"loop": {"pid": sleeper.pid, "start": info.start + 7}})
        )
        lines = procs.stop(self.home)
        self.assertIsNone(sleeper.poll())
        self.assertIn("nothing was killed", lines[0])

    def test_the_pidfile_is_private(self) -> None:
        procs.write_pidfile(self.home, os.getpid(), None)
        mode = procs.pidfile_path(self.home).stat().st_mode & 0o777
        self.assertEqual(oct(mode), "0o600")
        self.assertEqual(procs.read_pidfile(self.home)["loop"].pid, os.getpid())
        procs.remove_pidfile(self.home)
        self.assertEqual(procs.read_pidfile(self.home), {})


if __name__ == "__main__":
    unittest.main()
