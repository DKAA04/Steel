"""scripts/jarvis_check.py, run with fakes only. The live run with the real
microphone is the owner's; no test opens a microphone, a window or a model."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from jarvis_helpers import (
    QUIET,
    WAKE,
    FakeEngine,
    FakeGuard,
    FakeKeys,
    FakeWakeSource,
    give_consents,
    write_grants,
)
from voice_helpers import FakeTTY

from steel.home import WAKE_CONSENT_FILE
from steel.jarvis.procs import pidfile_path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "jarvis_check.py"


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("jarvis_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JarvisCheckScriptTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.script = load_script()
        give_consents(self.home)
        write_grants(self.home)
        # Files with the pinned SHA-256 cannot be faked; both lookups are patched.
        for target in (
            "steel.jarvis.models.models_ready",
            "steel.jarvis.listen.models_ready",
        ):
            ready = mock.patch(target, return_value=True)
            ready.start()
            self.addCleanup(ready.stop)

    def run_main(self, argv: list[str] | None = None, **kw: Any) -> tuple[int, str]:
        out = io.StringIO()
        options: dict[str, Any] = {
            "home": self.home,
            "foreground": lambda: True,
            "which": lambda name: f"/usr/bin/{name}",
        }
        options.update(kw)
        with (
            contextlib.redirect_stdout(out),
            mock.patch("subprocess.Popen", side_effect=AssertionError("started")),
        ):
            status = self.script.main(argv or ["--minutes", "1"], **options)
        return status, out.getvalue()

    def test_no_always_on_consent_means_nothing_is_opened(self) -> None:
        (self.home / WAKE_CONSENT_FILE).unlink()  # the voice consent stays
        status, out = self.run_main()
        self.assertEqual(status, 1)
        self.assertIn("Not listening; nothing was opened", out)
        self.assertIn("No always-on consent", out)

    def test_no_grant_for_wake_listen_means_nothing_is_opened(self) -> None:
        write_grants(self.home, {"agents": []})
        status, out = self.run_main()
        self.assertEqual(status, 1)
        self.assertIn("wake_listen (sensor:microphone_always_on) is not granted", out)

    def test_not_in_the_foreground_means_nothing_is_opened(self) -> None:
        status, out = self.run_main(foreground=lambda: False)
        self.assertEqual(status, 1)
        self.assertIn("foreground", out)

    def test_the_period_must_be_from_1_to_240_minutes(self) -> None:
        for bad in ("0", "241"):
            with (
                self.subTest(minutes=bad),
                self.assertRaises(SystemExit),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.script.main(["--minutes", bad], home=self.home)

    def test_the_engine_is_loaded_only_after_telemetry_is_off(self) -> None:
        order: list[str] = []
        with (
            mock.patch(
                "steel.offline.telemetry_off", side_effect=lambda: order.append("off")
            ),
            mock.patch(
                "steel.jarvis.wakeword.OpenWakeWord",
                side_effect=lambda _d: order.append("engine") or FakeEngine(),
            ),
        ):
            engine = self.script.load_engine(self.home)
        self.assertEqual(order, ["off", "engine"])
        self.assertGreaterEqual(engine.started_in, 0)

    def test_the_measured_engine_counts_frames_and_cpu(self) -> None:
        engine = self.script.MeasuredEngine(FakeEngine(), 0.5)
        self.assertEqual(engine.score(QUIET), 0.0)
        self.assertEqual(engine.score(WAKE), 1.0)
        self.assertEqual(engine.frames, 2)
        self.assertGreaterEqual(engine.cpu, 0.0)

    def fake_run(
        self, *extra: Any, ask: str = "n"
    ) -> tuple[int, str, FakeGuard, list[FakeWakeSource], list[int]]:
        """main() through the listening part with fakes for every device.

        Returns the status, the output, the guard, the sources and, for each
        source opened, the pidfile's mode at that moment (-1 if absent)."""
        guard = FakeGuard()
        scripts = [[QUIET, WAKE], [QUIET]]  # a wake, then the microphone ends
        sources: list[FakeWakeSource] = []
        pid_modes: list[int] = []

        def wake_source(device: str, seconds: float) -> FakeWakeSource:
            path = pidfile_path(self.home)
            pid_modes.append(path.stat().st_mode & 0o777 if path.exists() else -1)
            source = FakeWakeSource(scripts.pop(0) if scripts else [])
            sources.append(source)
            return source

        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            for patch in (
                mock.patch("steel.jarvis.indicators.GuardClient", return_value=guard),
                mock.patch("steel.jarvis.keys.KeyPoll", return_value=FakeKeys()),
                mock.patch("steel.jarvis.keys.owns_terminal", return_value=True),
                mock.patch("steel.jarvis.source.WakeSource", side_effect=wake_source),
                mock.patch.object(
                    self.script,
                    "load_engine",
                    return_value=self.script.MeasuredEngine(FakeEngine(), 0.2),
                ),
                mock.patch.object(sys, "stderr", FakeTTY()),
                mock.patch("subprocess.Popen", side_effect=AssertionError("started")),
                *extra,
            ):
                stack.enter_context(patch)
            status = self.script.main(
                ["--minutes", "1"],
                home=self.home,
                foreground=lambda: True,
                which=lambda name: f"/usr/bin/{name}",
                ask=lambda _prompt: ask,  # "did you say it?" no: a false trigger
            )
        return status, out.getvalue(), guard, sources, pid_modes

    def test_a_whole_fake_run_counts_a_false_trigger_and_cleans_up(self) -> None:
        home_before = {p.name for p in self.home.rglob("*")}
        status, text, guard, sources, _ = self.fake_run()
        self.assertEqual(status, 0)
        self.assertIn("false triggers: 1", text)
        self.assertIn("wake words you confirmed: 0", text)
        self.assertIn("engine CPU:", text)
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(s.closed == 1 for s in sources))  # closed every time
        self.assertTrue(guard.started and guard.closed)
        self.assertIn("SHOW LISTENING for 'hey jarvis'", guard.sent)
        new_files = {p.name for p in self.home.rglob("*")} - home_before
        self.assertLessEqual(new_files, {"steel_policy_audit.jsonl"})  # names only

    def test_the_pidfile_is_private_while_it_runs_and_removed_at_the_end(
        self,
    ) -> None:
        status, _, _, _, pid_modes = self.fake_run()
        self.assertEqual(status, 0)
        self.assertEqual(pid_modes, [0o600, 0o600])  # there, mode 600, each time
        self.assertFalse(pidfile_path(self.home).exists())

    def test_the_pidfile_is_removed_when_starting_up_fails(self) -> None:
        broken = mock.patch(
            "steel.tools.get_wake_tool", side_effect=RuntimeError("broken")
        )
        with self.assertRaises(RuntimeError):
            self.fake_run(broken)
        self.assertFalse(pidfile_path(self.home).exists())

    def test_the_pidfile_is_removed_when_an_error_happens_while_listening(
        self,
    ) -> None:
        # After a wake word the question to the owner fails (the terminal went
        # away): the error propagates, the pidfile and the guard are cleaned up.
        broken = mock.patch.object(
            self.script, "_ask", side_effect=RuntimeError("terminal gone")
        )
        with self.assertRaises(RuntimeError):
            self.fake_run(broken)
        self.assertFalse(pidfile_path(self.home).exists())

    def test_a_tool_error_ends_the_check_cleanly(self) -> None:
        # The executor turns an exception inside the tool into an error result.
        broken = mock.patch(
            "steel.jarvis.listen.WakeListen.execute",
            side_effect=RuntimeError("engine gone"),
        )
        status, text, guard, _, _ = self.fake_run(broken)
        self.assertEqual(status, 1)  # a failure is not a success
        self.assertIn("ended:", text)
        self.assertTrue(guard.closed)
        self.assertFalse(pidfile_path(self.home).exists())

    def test_the_pidfile_is_removed_after_ctrl_c_and_the_report_is_printed(
        self,
    ) -> None:
        ctrl_c = mock.patch(
            "steel.security.executor.build_executor",
            return_value=mock.Mock(execute=mock.Mock(side_effect=KeyboardInterrupt)),
        )
        status, text, guard, _, _ = self.fake_run(ctrl_c)
        self.assertEqual(status, 0)
        self.assertIn("ended: Ctrl-C", text)
        self.assertTrue(guard.closed)
        self.assertFalse(pidfile_path(self.home).exists())

    def test_a_guard_that_dies_while_listening_closes_the_microphone_and_exits_1(
        self,
    ) -> None:
        guard = FakeGuard()
        reads: list[int] = []

        class Dying(FakeWakeSource):
            def read_frame(self) -> bytes:
                frame = super().read_frame()
                reads.append(self.read)
                if self.read == 3:
                    guard.dead = True  # the guard process is gone
                return frame

        sources: list[FakeWakeSource] = []

        def wake_source(device: str, seconds: float) -> FakeWakeSource:
            source = Dying([QUIET] * 50)
            sources.append(source)
            return source

        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            for patch in (
                mock.patch("steel.jarvis.indicators.GuardClient", return_value=guard),
                mock.patch("steel.jarvis.keys.KeyPoll", return_value=FakeKeys()),
                mock.patch("steel.jarvis.keys.owns_terminal", return_value=True),
                mock.patch("steel.jarvis.source.WakeSource", side_effect=wake_source),
                mock.patch.object(
                    self.script,
                    "load_engine",
                    return_value=self.script.MeasuredEngine(FakeEngine(), 0.2),
                ),
                mock.patch.object(sys, "stderr", FakeTTY()),
                mock.patch("subprocess.Popen", side_effect=AssertionError("started")),
            ):
                stack.enter_context(patch)
            status = self.script.main(
                ["--minutes", "1"],
                home=self.home,
                foreground=lambda: True,
                which=lambda name: f"/usr/bin/{name}",
                ask=lambda _prompt: "n",
            )
        text = out.getvalue()
        self.assertEqual(status, 1)
        self.assertIn(
            "ended: the indicator window went away; the microphone is closed", text
        )
        (source,) = sources  # not opened again
        self.assertEqual(source.closed, 1)
        self.assertEqual(reads, [1, 2, 3])  # no read after the guard died
        self.assertEqual(guard.starts, 1)  # never restarted
        self.assertTrue(guard.closed)
        self.assertFalse(pidfile_path(self.home).exists())

    def test_it_turns_telemetry_off_and_uses_no_forbidden_audio_library(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("telemetry_off()", source)
        self.assertNotIn("sounddevice", source)
        self.assertNotIn("openjarvis.speech", source)


if __name__ == "__main__":
    unittest.main()
