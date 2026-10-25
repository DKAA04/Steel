"""steel doctor warns about onnxruntime's telemetry queue (Phase 5c clean-up).

A temporary $HOME only; the real ~/.cache is never looked at.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
from pathlib import Path
from unittest import mock

import test_steel as base

from steel.cli import main
from steel.offline import ONNXRUNTIME_QUEUE, telemetry_queue_lines


class TelemetryQueueTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.user_home = Path(tmp.name)

    def doctor(self) -> str:
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"HOME": str(self.user_home)}),
            contextlib.redirect_stdout(out),
        ):
            main(["doctor"])
        return out.getvalue()

    def test_no_queue_means_no_warning(self) -> None:
        self.assertEqual(
            telemetry_queue_lines(self.user_home),
            ["onnxruntime telemetry queue: none"],
        )
        self.assertIn("onnxruntime telemetry queue: none", self.doctor())

    def test_a_queue_is_reported_with_the_one_command_to_delete_it(self) -> None:
        queue = self.user_home / ONNXRUNTIME_QUEUE
        queue.mkdir(parents=True)
        (queue / "onnxruntime.db").write_bytes(b"x")
        out = self.doctor()
        self.assertIn("onnxruntime telemetry queue: WARNING", out)
        self.assertIn(f"  rm -r {queue}", out)
        self.assertTrue((queue / "onnxruntime.db").exists())  # read only: kept

    def test_the_doctor_never_deletes_or_reads_the_queue(self) -> None:
        queue = self.user_home / ONNXRUNTIME_QUEUE
        queue.mkdir(parents=True)
        (queue / "deviceid").write_text("private")
        before = sorted(p.name for p in queue.iterdir())
        with mock.patch("builtins.open", side_effect=AssertionError("read")):
            lines = telemetry_queue_lines(self.user_home)
        self.assertIn("WARNING", lines[0])
        self.assertEqual(sorted(p.name for p in queue.iterdir()), before)
