"""The speaker: ``aplay`` reading raw PCM from a pipe (docs/SPEAK.md section 1).

Only ``steel.speak.playback.play`` opens it, and ``open`` demands the proof
that the SPEAKING line is on screen. No file is involved: ``aplay`` reads
standard input (``-``) and is given no output file.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Any, Protocol

from steel.speak.indicator import SpeakingShown

PLAYER = "aplay"
RATES = frozenset({16000, 22050})
_KILL_WAIT = 2.0


class PlayerUnavailable(RuntimeError):
    pass


class AudioSink(Protocol):
    def open(self, shown: SpeakingShown, rate: int) -> None: ...

    def write(self, data: memoryview) -> None: ...

    def finish(self) -> None: ...

    def done(self) -> bool: ...

    def stop(self) -> None: ...


def player_argv(program: str, rate: int) -> list[str]:
    return [program, "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-"]


class AplaySink:
    def __init__(
        self,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._popen = popen
        self._which = which
        self._process: Any = None

    def open(self, shown: SpeakingShown, rate: int) -> None:
        if not isinstance(shown, SpeakingShown):
            raise TypeError("the speaker opens only after the SPEAKING line")
        if rate not in RATES:
            raise ValueError("unsupported sample rate")
        program = self._which(PLAYER)
        if program is None:
            raise PlayerUnavailable("aplay was not found (install alsa-utils)")
        try:
            # The player keeps the desktop's environment: PipeWire needs it.
            self._process = self._popen(
                player_argv(program, rate),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                bufsize=0,  # no copy of the audio waits in a Python buffer
            )
        except OSError as exc:
            raise PlayerUnavailable("aplay could not be started") from exc

    def write(self, data: memoryview) -> None:
        self._process.stdin.write(data)

    def finish(self) -> None:
        """End of audio: aplay plays what it has and exits."""
        try:
            self._process.stdin.close()
        except OSError:
            pass

    def done(self) -> bool:
        return self._process is None or self._process.poll() is not None

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.kill()
        try:
            self._process.wait(timeout=_KILL_WAIT)
        except subprocess.TimeoutExpired:
            pass
        try:
            self._process.stdin.close()
        except (OSError, ValueError):
            pass
