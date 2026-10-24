"""The real camera: ``ffmpeg`` writing ONE raw frame to a pipe (docs/VISION.md 5.1).

Kept in its own small module so the rest can be tested with a fake. Properties:

- no output file: the frame goes from ``ffmpeg`` straight into this process;
- ``open`` needs the proof that the CAMERA OPEN line was printed (``CameraShown``);
- ``ffmpeg`` reads a few frames and passes only the last to the pipe (the first
  frames of a webcam are often dark while the exposure settles), then exits;
- a watchdog kills ``ffmpeg`` after ``timeout`` seconds, and ``close`` always does;
- the command is a list (no shell) and the device must be ``/dev/videoN``.

Nothing here imports OpenCV or an imaging library. This module has never run
against a real camera in the test suite: ``scripts/vision_check.py`` is the live check.
"""

from __future__ import annotations

import re
import subprocess
import threading
from typing import Any

from steel.vision.frame import Frame
from steel.vision.indicator import CameraShown

_DEVICE_RE = re.compile(r"/dev/video[0-9]{1,2}")


class FfmpegCameraSource:
    def __init__(
        self,
        device: str = "/dev/video0",
        size: tuple[int, int] = (640, 480),
        warmup_frames: int = 5,
        timeout: float = 10.0,
        *,
        popen: Any = subprocess.Popen,
        program: str = "ffmpeg",
    ) -> None:
        if not _DEVICE_RE.fullmatch(device):
            raise ValueError(
                "the camera device must be /dev/video followed by a number"
            )
        if not 0 <= warmup_frames <= 30:
            raise ValueError("warmup_frames must be between 0 and 30")
        self._device = device
        self._width, self._height = size
        self._warmup = warmup_frames
        self._timeout = timeout
        self._popen = popen
        self._program = program
        self._process: Any = None

    def command(self) -> list[str]:
        """The exact command line (a list: no shell is involved)."""
        return [
            self._program,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-video_size",
            f"{self._width}x{self._height}",
            "-i",
            self._device,
            "-vf",
            f"select=eq(n\\,{self._warmup})",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]  # no file name: the frame goes to standard output

    def open(self, shown: CameraShown) -> None:
        if not isinstance(shown, CameraShown):
            raise PermissionError("the camera needs the proof that the line is shown")
        if self._process is not None:
            raise RuntimeError("the camera is already open")
        self._process = self._popen(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read_frame(self) -> Frame:
        process = self._process
        if process is None or process.stdout is None:
            raise OSError("the camera is not open")
        wanted = self._width * self._height * 3
        pixels = bytearray(wanted)
        view = memoryview(pixels)
        got = 0
        # If the camera stalls, the read below would never return: the watchdog
        # ends ffmpeg, which ends the pipe.
        watchdog = threading.Timer(self._timeout, self._stop_process, args=(process,))
        watchdog.start()
        try:
            while got < wanted:
                count = process.stdout.readinto(view[got:])
                if not count:
                    break
                got += count
        finally:
            watchdog.cancel()
            view.release()
        if got < wanted:
            pixels[:] = bytes(len(pixels))
            raise OSError(
                "the camera gave no picture (busy, missing or unsupported size)"
            )
        return Frame(pixels, self._width, self._height)

    @staticmethod
    def _stop_process(process: Any) -> None:
        try:
            process.terminate()
        except OSError:
            pass  # already gone

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()
