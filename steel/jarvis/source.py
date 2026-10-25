"""The microphone for the Jarvis loop: ``arecord`` on a pipe, killed with STEEL.

Both microphones the loop uses run through ``setpriv --pdeathsig KILL``, so the
kernel ends them if the thread that started them dies, even if STEEL is killed
with SIGKILL (docs/JARVIS.md section 3c). Without ``setpriv`` they are not
started at all. No output file: raw audio goes into this process only.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from collections.abc import Callable
from typing import IO, Any

from steel.jarvis.indicators import ListeningShown
from steel.jarvis.wakeword import FRAME_BYTES
from steel.voice.mic import ArecordSource
from steel.voice.source import SAMPLE_RATE

DEATH_SIGNAL = ("--pdeathsig", "KILL", "--")


class MicUnavailable(RuntimeError):
    pass


def _setpriv(which: Callable[[str], str | None]) -> str:
    path = which("setpriv")
    if path is None:
        raise MicUnavailable(
            "setpriv (util-linux) was not found, so a microphone could outlive "
            "STEEL; the microphone is not opened"
        )
    return path


class WakeSource:
    """The listening microphone: 80 ms frames, only for the wake-word engine."""

    def __init__(
        self,
        device: str = "default",
        max_seconds: float = 3600.0,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        which: Callable[[str], str | None] = shutil.which,
        program: str = "arecord",
    ) -> None:
        if not device or device.startswith("-"):
            raise ValueError("the ALSA device name must not look like an option")
        self._device = device
        self._max_seconds = max_seconds
        self._popen = popen
        self._which = which
        self._program = program
        self._process: Any = None

    def command(self) -> list[str]:
        return [
            _setpriv(self._which),
            *DEATH_SIGNAL,
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
            # It also ends by itself one second after the session limit.
            "-d",
            str(math.ceil(self._max_seconds) + 1),
        ]

    def open(self, shown: ListeningShown) -> None:
        if not isinstance(shown, ListeningShown):
            raise PermissionError("the microphone needs both indicators shown first")
        if self._process is not None:
            raise RuntimeError("the microphone is already open")
        self._process = self._popen(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

    @property
    def pid(self) -> int | None:
        return None if self._process is None else int(self._process.pid)

    def read_frame(self) -> bytes:
        """One whole 80 ms frame, or b"" when the microphone stopped."""
        stream: IO[bytes] | None = self._process.stdout if self._process else None
        if stream is None:
            return b""
        frame = b""
        while len(frame) < FRAME_BYTES:
            chunk = stream.read(FRAME_BYTES - len(frame))
            if not chunk:
                return b""
            frame += chunk
        return frame

    def close(self) -> None:
        """Kill the microphone process and wait for it. Safe to call twice."""
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.kill()  # nothing to save: ending at once is the point
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if process.stdout is not None:
            process.stdout.close()


class RequestSource(ArecordSource):
    """The push-to-talk source for the request after the wake word, also killed
    with STEEL (steel.voice.mic.ArecordSource plus ``setpriv``)."""

    def __init__(
        self,
        device: str = "default",
        max_seconds: float = 30.0,
        *,
        which: Callable[[str], str | None] = shutil.which,
        **kwargs: Any,
    ) -> None:
        super().__init__(device, max_seconds, **kwargs)
        self._which = which

    def command(self) -> list[str]:
        return [_setpriv(self._which), *DEATH_SIGNAL, *super().command()]
