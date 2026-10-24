"""The real microphone: ``arecord`` writing raw audio to a pipe (docs/VOICE.md 6.1).

Kept in its own small module so the rest can be tested with a fake. Properties:

- no output file: the audio goes from ``arecord`` straight into this process;
- ``open`` needs the proof that the REC line was printed (``IndicatorShown``);
- ``arecord -d`` is a second, independent stop (max length + 1 s), so it ends
  by itself even if this process were killed; closing the pipe ends it too;
- ``arecord`` shows up in ``ps`` for exactly as long as the microphone is open.

Nothing here imports ``sounddevice``: that library initialises PortAudio on import.
"""

from __future__ import annotations

import math
import subprocess
from typing import IO, Any

from steel.voice.indicator import IndicatorShown
from steel.voice.source import CHUNK_BYTES, SAMPLE_RATE


class ArecordSource:
    def __init__(
        self,
        device: str = "default",
        max_seconds: float = 30.0,
        *,
        popen: Any = subprocess.Popen,
        program: str = "arecord",
    ) -> None:
        if not device or device.startswith("-"):
            raise ValueError("the ALSA device name must not look like an option")
        self._device = device
        self._max_seconds = max_seconds
        self._popen = popen
        self._program = program
        self._process: Any = None

    def command(self) -> list[str]:
        """The exact command line (a list: no shell is involved)."""
        return [
            self._program,
            "-q",
            "-D",
            self._device,
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            "1",
            "-t",
            "raw",
            "-d",
            str(math.ceil(self._max_seconds) + 1),
        ]  # no file name: raw audio goes to standard output

    def open(self, shown: IndicatorShown) -> None:
        if not isinstance(shown, IndicatorShown):
            raise PermissionError("the microphone needs the proof that REC is shown")
        if self._process is not None:
            raise RuntimeError("the microphone is already open")
        self._process = self._popen(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read(self) -> bytes:
        stream: IO[bytes] | None = self._process.stdout if self._process else None
        if stream is None:
            return b""
        return stream.read(CHUNK_BYTES)  # blocks until a chunk or the end

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
