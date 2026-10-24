"""Vision core: frames, the CAMERA OPEN line, one capture, consent, the real source.

No camera, vision model or network: fake sources, fake processes, a temporary home.
"""

from __future__ import annotations

import io
import re
import subprocess
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from vision_helpers import FakeFrameSource, decode_png, make_frame
from voice_helpers import BrokenTTY, FakeTTY, NotATTY

from steel.vision.camera import FfmpegCameraSource
from steel.vision.capture import capture_one
from steel.vision.config import DEFAULT_DEVICE, DEFAULT_MODEL, vision_config
from steel.vision.consent import (
    CONSENT_TEXT,
    consent_path,
    has_consent,
    read_consent,
    record_consent,
    revoke_consent,
)
from steel.vision.frame import (
    Frame,
    downscale,
    encode_for_model,
    target_size,
    to_png,
)
from steel.vision.indicator import (
    CameraIndicator,
    CameraIndicatorError,
    CameraShown,
)
from steel.voice.consent import consent_path as voice_consent_path
from steel.voice.consent import has_consent as voice_has_consent
from steel.voice.consent import record_consent as record_voice_consent
from steel.voice.consent import revoke_consent as revoke_voice_consent

DOCS = Path(__file__).resolve().parent.parent / "docs" / "VISION.md"


class FrameTest(unittest.TestCase):
    def test_a_frame_must_match_its_size(self) -> None:
        with self.assertRaises(ValueError):
            Frame(bytearray(10), 4, 4)
        with self.assertRaises(ValueError):
            Frame(bytearray(0), 0, 1)

    def test_the_longest_side_is_capped_and_a_small_frame_is_never_enlarged(
        self,
    ) -> None:
        self.assertEqual(target_size(640, 480, 448), (448, 336))
        self.assertEqual(target_size(1280, 720, 448), (448, 252))
        self.assertEqual(target_size(480, 640, 448), (336, 448))
        self.assertEqual(target_size(320, 240, 448), (320, 240))
        self.assertEqual(target_size(448, 448, 448), (448, 448))

    def test_downscale_averages_the_pixels_it_covers(self) -> None:
        # Four columns 0, 100, 200, 40 shrink to two: (0+100)/2 and (200+40)/2.
        row = bytes(v for value in (0, 100, 200, 40) for v in (value,) * 3)
        small = downscale(Frame(bytearray(row * 2), 4, 2), 2)
        self.assertEqual((small.width, small.height), (2, 1))
        self.assertEqual(list(small.rgb), [50, 50, 50, 120, 120, 120])

    def test_downscale_keeps_the_picture_and_leaves_the_original_alone(self) -> None:
        frame = make_frame(640, 480)
        before = bytes(frame.rgb)
        small = downscale(frame, 448)
        self.assertEqual((small.width, small.height), (448, 336))
        self.assertEqual(bytes(frame.rgb), before)
        self.assertEqual(tuple(small.rgb[0:3]), (200, 0, 0))  # left edge stays red
        self.assertEqual(tuple(small.rgb[-3:]), (0, 0, 200))  # right edge stays blue

    def test_a_flat_colour_stays_flat_for_a_non_integer_ratio(self) -> None:
        frame = Frame(bytearray(bytes((90, 120, 30)) * 640 * 480), 640, 480)
        small = downscale(frame, 448)
        self.assertEqual(set(small.rgb[0::3]), {90})
        self.assertEqual(set(small.rgb[1::3]), {120})
        self.assertEqual(set(small.rgb[2::3]), {30})

    def test_wipe_overwrites_the_pixels_with_zeros(self) -> None:
        frame = make_frame(8, 8)
        frame.wipe()
        self.assertEqual(set(frame.rgb), {0})
        self.assertEqual(len(frame.rgb), 8 * 8 * 3)

    def test_the_png_is_a_valid_picture_of_the_same_pixels(self) -> None:
        import base64

        frame = make_frame(12, 6)
        width, height, pixels = decode_png(base64.b64encode(to_png(frame)).decode())
        self.assertEqual((width, height), (12, 6))
        self.assertEqual(pixels, bytes(frame.rgb))

    def test_the_scaled_copy_is_wiped_even_though_the_caller_keeps_its_frame(
        self,
    ) -> None:
        import steel.vision.frame as frame_module

        made: list[Frame] = []
        real = frame_module.downscale

        def spy(frame: Frame, max_side: int) -> Frame:
            made.append(real(frame, max_side))
            return made[-1]

        with mock.patch.object(frame_module, "downscale", spy):
            encode_for_model(make_frame(640, 480), 448)
        (small,) = made
        self.assertEqual(len(small.rgb), 448 * 336 * 3)
        self.assertEqual(set(small.rgb), {0})

    def test_encode_for_model_reports_the_size_it_sent_and_wipes_its_copy(
        self,
    ) -> None:
        frame = make_frame(1280, 720)
        image, size = encode_for_model(frame, 448)
        self.assertEqual(size, (448, 252))
        width, height, _ = decode_png(image)
        self.assertEqual((width, height), (448, 252))
        self.assertNotEqual(set(frame.rgb), {0})  # the caller's frame is its own


class IndicatorTest(unittest.TestCase):
    def test_show_prints_the_line_and_returns_proof(self) -> None:
        screen = FakeTTY()
        proof = CameraIndicator(screen).show()
        self.assertIsInstance(proof, CameraShown)
        self.assertTrue(screen.getvalue().startswith("● CAMERA OPEN"))

    def test_no_terminal_means_no_proof(self) -> None:
        screen = NotATTY()
        with self.assertRaises(CameraIndicatorError):
            CameraIndicator(screen).show()
        self.assertEqual(screen.getvalue(), "")

    def test_a_failing_write_means_no_proof(self) -> None:
        with self.assertRaises(CameraIndicatorError):
            CameraIndicator(BrokenTTY()).show()

    def test_the_proof_cannot_be_made_by_hand(self) -> None:
        with self.assertRaises(TypeError):
            CameraShown("● CAMERA OPEN", object())

    def test_a_microphone_proof_does_not_open_the_camera(self) -> None:
        from steel.voice.indicator import Indicator

        mic_proof = Indicator(FakeTTY()).show(5)
        source = FfmpegCameraSource(popen=_Spawn())
        with self.assertRaises(PermissionError):
            source.open(mic_proof)  # type: ignore[arg-type]

    def test_closed_and_failed_lines(self) -> None:
        screen = FakeTTY()
        indicator = CameraIndicator(screen)
        indicator.closed(0.84)
        indicator.failed()
        self.assertIn("■ camera closed after 0.8 s", screen.getvalue())
        self.assertIn("no picture was taken", screen.getvalue())


class CaptureTest(unittest.TestCase):
    def test_the_line_is_printed_before_the_source_is_opened(self) -> None:
        screen = FakeTTY()
        source = FakeFrameSource(screen=screen)
        capture_one(source, CameraIndicator(screen))
        assert source.screen_at_open is not None
        self.assertIn("● CAMERA OPEN", source.screen_at_open)

    def test_without_a_terminal_the_source_is_never_opened(self) -> None:
        source = FakeFrameSource()
        with self.assertRaises(CameraIndicatorError):
            capture_one(source, CameraIndicator(NotATTY()))
        self.assertEqual((source.opened, source.reads, source.closed), (0, 0, 0))

    def test_exactly_one_frame_is_read_and_the_camera_is_closed_after(self) -> None:
        source = FakeFrameSource()
        result = capture_one(source, CameraIndicator(FakeTTY()))
        self.assertEqual(source.events, ["open", "close"])
        self.assertEqual(source.reads, 1)
        self.assertIs(result.frame, source.frame)

    def test_a_failed_read_still_closes_the_camera_and_says_so(self) -> None:
        screen = FakeTTY()
        source = FakeFrameSource(screen=screen, fail_read=OSError("no picture"))
        with self.assertRaises(OSError):
            capture_one(source, CameraIndicator(screen))
        self.assertEqual(source.closed, 1)
        self.assertIn("camera closed", screen.getvalue())

    def test_a_source_that_fails_to_open_is_reported_and_not_read(self) -> None:
        screen = FakeTTY()

        class Broken(FakeFrameSource):
            def open(self, shown: CameraShown) -> None:
                raise OSError("busy")

        source = Broken()
        with self.assertRaises(OSError):
            capture_one(source, CameraIndicator(screen))
        self.assertEqual(source.reads, 0)
        self.assertIn("did not open", screen.getvalue())


class ConsentTest(base.HomeTestCase):
    def test_no_file_means_no_consent(self) -> None:
        self.assertFalse(has_consent(self.home))

    def test_recording_consent_writes_only_a_timestamp_with_mode_600(self) -> None:
        stamp = record_consent(self.home)
        path = consent_path(self.home)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(read_consent(self.home), stamp)
        self.assertEqual(
            set(__import__("json").loads(path.read_text())), {"consented_at"}
        )
        self.assertEqual(path.name, "steel_camera_consent.json")

    def test_revoking_deletes_the_file(self) -> None:
        record_consent(self.home)
        self.assertTrue(revoke_consent(self.home))
        self.assertFalse(consent_path(self.home).exists())
        self.assertFalse(revoke_consent(self.home))  # nothing left to withdraw

    def test_a_malformed_file_is_not_consent(self) -> None:
        path = consent_path(self.home)
        for text in (
            "",
            "yes",
            "[]",
            '{"consented_at": "x"}',
            '{"consented_at": "2026-01-01T00:00:00+00:00", "extra": 1}',
        ):
            path.write_text(text)
            path.chmod(0o600)
            self.assertFalse(has_consent(self.home), text)

    def test_a_symlink_is_not_consent(self) -> None:
        record_consent(self.home)
        real = self.home / "elsewhere.json"
        consent_path(self.home).rename(real)
        consent_path(self.home).symlink_to(real)
        self.assertFalse(has_consent(self.home))

    def test_a_file_others_can_write_is_not_consent(self) -> None:
        record_consent(self.home)
        consent_path(self.home).chmod(0o666)
        self.assertFalse(has_consent(self.home))

    def test_consent_does_not_write_through_a_symlink(self) -> None:
        victim = self.home / "victim.txt"
        victim.write_text("untouched")
        consent_path(self.home).symlink_to(victim)
        with self.assertRaises(OSError):
            record_consent(self.home)
        self.assertEqual(victim.read_text(), "untouched")

    def test_camera_and_voice_consent_are_independent(self) -> None:
        record_consent(self.home)
        self.assertFalse(voice_has_consent(self.home))  # camera alone is not voice
        record_voice_consent(self.home)
        self.assertTrue(has_consent(self.home) and voice_has_consent(self.home))
        revoke_consent(self.home)  # withdrawing the camera ...
        self.assertTrue(voice_has_consent(self.home))  # ... leaves the microphone
        record_consent(self.home)
        revoke_voice_consent(self.home)  # and the other way round
        self.assertTrue(has_consent(self.home))
        self.assertNotEqual(consent_path(self.home), voice_consent_path(self.home))

    def test_the_consent_text_is_the_one_in_the_design_document(self) -> None:
        quoted = [
            re.sub(r"^> ?", "", line).replace("**", "")
            for line in DOCS.read_text(encoding="utf-8").splitlines()
            if line.startswith(">")
        ]
        self.assertEqual("\n".join(quoted).strip(), CONSENT_TEXT.strip())

    def test_the_consent_text_says_the_things_the_owner_must_know(self) -> None:
        for phrase in (
            "no always-on watching and no motion detection",
            "No picture file is written",
            "nothing is sent over the network",
            "Be careful with guests",
            "STEEL does not identify people",
            "steel vision revoke",
            "grant the camera tool by hand",
        ):
            self.assertIn(phrase, CONSENT_TEXT, phrase)


class ConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        config = vision_config({})
        self.assertEqual((config.device, config.model), (DEFAULT_DEVICE, DEFAULT_MODEL))
        self.assertEqual(config.max_side, 448)

    def test_a_valid_device_and_model_are_used(self) -> None:
        config = vision_config(
            {"STEEL_CAMERA_DEVICE": "/dev/video2", "STEEL_VISION_MODEL": "qwen3.5:2b"}
        )
        self.assertEqual((config.device, config.model), ("/dev/video2", "qwen3.5:2b"))

    def test_a_device_that_is_not_a_video_node_falls_back(self) -> None:
        for bad in (
            "/etc/passwd",
            "video0",
            "/dev/video",
            "/dev/video0; rm",
            "-i x",
            "/dev/video123",
            "/dev/video0/..",
        ):
            self.assertEqual(
                vision_config({"STEEL_CAMERA_DEVICE": bad}).device, DEFAULT_DEVICE, bad
            )

    def test_a_model_name_with_paths_or_options_falls_back(self) -> None:
        for bad in ("../x", "-x", "a b", "a;b", "", "x" * 80, "a/../b"):
            self.assertEqual(
                vision_config({"STEEL_VISION_MODEL": bad}).model, DEFAULT_MODEL, bad
            )


class _Spawn:
    """A popen stand-in that records its calls."""

    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.process: _FakeProcess | None = None

    def __call__(self, command: list[str], **kwargs: Any) -> _FakeProcess:
        self.calls.append((command, kwargs))
        self.process = _FakeProcess(self.data)
        return self.process


class _FakeProcess:
    def __init__(
        self, data: bytes = b"", stall: bool = False, hang: bool = False
    ) -> None:
        self.stdout: Any = _Stdout(data, stall)
        self.terminated = False
        self.killed = False
        self.waited = 0
        self._hang = hang

    def terminate(self) -> None:
        self.terminated = True
        self.stdout.release()

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited += 1
        if self._hang and not self.killed and timeout is not None:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return 0


class _Stdout(io.BytesIO):
    """A pipe that can stall until the process is terminated."""

    def __init__(self, data: bytes, stall: bool = False) -> None:
        super().__init__(data)
        self._stall = threading.Event() if stall else None

    def readinto(self, buffer: Any) -> int:  # type: ignore[override]
        if self._stall is not None:
            self._stall.wait(5)
            return 0
        return super().readinto(buffer)

    def release(self) -> None:
        if self._stall is not None:
            self._stall.set()


def proof() -> CameraShown:
    return CameraIndicator(FakeTTY()).show()


class FfmpegCameraSourceTest(unittest.TestCase):
    def test_opening_without_the_proof_starts_nothing(self) -> None:
        spawn = _Spawn()
        source = FfmpegCameraSource(popen=spawn)
        for bad in (None, "shown", object()):
            with self.assertRaises(PermissionError):
                source.open(bad)  # type: ignore[arg-type]
        self.assertEqual(spawn.calls, [])

    def test_the_command_is_a_list_with_no_file_and_one_frame(self) -> None:
        spawn = _Spawn()
        source = FfmpegCameraSource("/dev/video0", (640, 480), 5, popen=spawn)
        source.open(proof())
        command, kwargs = spawn.calls[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "ffmpeg")
        self.assertEqual(command[-1], "pipe:1")  # standard output, no file name
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertEqual(command[command.index("-i") + 1], "/dev/video0")
        self.assertEqual(command[command.index("-video_size") + 1], "640x480")
        self.assertIn("select=eq(n\\,5)", command)  # the sixth frame, after warm-up
        self.assertNotIn("shell", kwargs)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        # Nothing in the command can name an output file.
        self.assertEqual(
            [c for c in command if c.startswith("/") and c != "/dev/video0"], []
        )

    def test_the_device_must_be_a_video_node(self) -> None:
        for bad in ("/etc/passwd", "-f", "/dev/video0 -y", "", "/dev/sda"):
            with self.assertRaises(ValueError, msg=bad):
                FfmpegCameraSource(bad)

    def test_it_cannot_be_opened_twice(self) -> None:
        source = FfmpegCameraSource(popen=_Spawn())
        source.open(proof())
        with self.assertRaises(RuntimeError):
            source.open(proof())

    def test_it_reads_one_whole_frame(self) -> None:
        pixels = bytes(range(256)) * (8 * 4 * 3 // 256 + 1)
        pixels = pixels[: 8 * 4 * 3]
        source = FfmpegCameraSource(size=(8, 4), popen=_Spawn(pixels))
        source.open(proof())
        frame = source.read_frame()
        self.assertEqual((frame.width, frame.height, bytes(frame.rgb)), (8, 4, pixels))

    def test_a_short_read_is_an_error(self) -> None:
        source = FfmpegCameraSource(size=(8, 4), popen=_Spawn(b"\x07" * 10))
        source.open(proof())
        with self.assertRaises(OSError):
            source.read_frame()

    def test_the_pixel_buffer_is_wiped_when_the_frame_is_short(self) -> None:
        seen: list[Any] = []

        class Recording(_Stdout):
            def readinto(self, buffer: Any) -> int:  # type: ignore[override]
                seen.append(buffer)
                return super().readinto(buffer)

        process = _FakeProcess()
        process.stdout = Recording(b"\x07" * 10)
        source = FfmpegCameraSource(size=(8, 4), popen=lambda *a, **k: process)
        source.open(proof())
        with self.assertRaises(OSError):
            source.read_frame()
        self.assertEqual(bytes(seen[0][:10]), bytes(10))  # the 10 bytes it did get

    def test_a_stalled_camera_is_ended_by_the_watchdog(self) -> None:
        process = _FakeProcess(stall=True)
        source = FfmpegCameraSource(
            size=(8, 4), timeout=0.1, popen=lambda *a, **k: process
        )
        source.open(proof())
        with self.assertRaises(OSError):
            source.read_frame()
        self.assertTrue(process.terminated)

    def test_closing_terminates_the_process_and_is_idempotent(self) -> None:
        spawn = _Spawn()
        source = FfmpegCameraSource(popen=spawn)
        source.open(proof())
        process = spawn.process
        assert process is not None
        source.close()
        source.close()
        self.assertTrue(process.terminated)
        self.assertEqual(process.waited, 1)

    def test_a_process_that_will_not_stop_is_killed(self) -> None:
        process = _FakeProcess(hang=True)
        source = FfmpegCameraSource(popen=lambda *a, **k: process)
        source.open(proof())
        source.close()
        self.assertTrue(process.killed)

    def test_reading_before_opening_is_an_error(self) -> None:
        with self.assertRaises(OSError):
            FfmpegCameraSource(popen=_Spawn()).read_frame()


class RealCommandOutputTest(unittest.TestCase):
    def test_the_filter_chain_gives_exactly_one_frame_from_a_synthetic_source(
        self,
    ) -> None:
        """The output side of the command, with ffmpeg's test pattern in place of the camera."""
        import shutil

        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is not installed")
        command = FfmpegCameraSource("/dev/video0", (64, 48), 5).command()
        # Swap only the input; every other argument is the real one. No camera is opened.
        start = command.index("-f")
        end = command.index("-i") + 2
        command[start:end] = ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=30"]
        self.assertNotIn("/dev/video0", command)
        self.assertNotIn("v4l2", command)  # the camera input is gone
        out = subprocess.run(command, capture_output=True, check=True, timeout=30)
        self.assertEqual(len(out.stdout), 64 * 48 * 3)


if __name__ == "__main__":
    unittest.main()
