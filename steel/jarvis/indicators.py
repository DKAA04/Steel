"""What the owner sees while the Jarvis loop runs (docs/JARVIS.md section 3b):
one status line in the terminal and the guard's window outside it.

The listening microphone opens only with a ``ListeningShown`` proof, and only
``Indicators.show_listening`` makes one: after the terminal line is written to
a terminal AND the guard has answered SHOWN. Like the REC line, this stops
mistakes; code in this process could still forge it (docs/VOICE.md section 8).
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from steel.jarvis.guard import SHOW_TIMEOUT
from steel.jarvis.models import WAKE_PHRASE

_TOKEN = object()
WATCHDOG_SECONDS = 10.0
LISTENING = f"LISTENING for '{WAKE_PHRASE}'"


class IndicatorError(RuntimeError):
    """The state cannot be shown, so the microphone must not open."""


class ListeningShown:
    """Proof that both indicators are up. Only show_listening() makes one."""

    __slots__ = ()

    def __init__(self, token: object) -> None:
        if token is not _TOKEN:
            raise TypeError("only Indicators.show_listening() can make this proof")


class GuardClient:
    """The loop's side of the guard process (steel/jarvis/guard.py)."""

    def __init__(
        self,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        python: str = sys.executable,
        watchdog_seconds: float = WATCHDOG_SECONDS,
    ) -> None:
        self._popen = popen
        self._python = python
        self._watchdog = watchdog_seconds
        self._process: Any = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._process = self._popen(
            [
                self._python,
                "-I",
                "-m",
                "steel.jarvis.guard",
                str(os.getpid()),
                str(self._watchdog),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            # Its own session: Ctrl-C and Ctrl-Z in the terminal do not reach
            # it, so the watchdog keeps working while the loop is stopped.
            start_new_session=True,
        )

    @property
    def pid(self) -> int | None:
        return None if self._process is None else int(self._process.pid)

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def send(self, line: str) -> bool:
        with self._lock:
            if not self.alive():
                return False
            try:
                self._process.stdin.write((line + "\n").encode("ascii", "replace"))
                self._process.stdin.flush()
            except (OSError, ValueError):
                return False
            return True

    def show(self, state: str) -> bool:
        """True only if the guard answered SHOWN within the timeout."""
        if not self.send(f"SHOW {state}"):
            return False
        stdout = self._process.stdout
        ready, _, _ = select.select([stdout], [], [], SHOW_TIMEOUT)
        if not ready:
            return False
        return bool(stdout.readline().strip() == b"SHOWN")

    def state(self, state: str) -> None:
        self.send(f"STATE {state}")

    def hide(self) -> None:
        self.send("HIDE")

    def beat(self) -> None:
        self.send("BEAT")

    def close(self) -> None:
        """End of the loop: the guard's input ends, it cleans up and exits."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()


class Indicators:
    """The terminal status line and the guard, together."""

    def __init__(self, guard: Any, stream: TextIO | None = None) -> None:
        self.guard = guard
        self._stream = stream if stream is not None else sys.stderr
        self._width = 0

    def line(self, text: str, *, newline: bool = True) -> None:
        padded = text.ljust(self._width)
        self._width = 0 if newline else len(text)
        self._stream.write("\r" + padded + ("\n" if newline else ""))
        self._stream.flush()

    def show_listening(self) -> ListeningShown:
        """Both indicators up, or IndicatorError and no microphone."""
        try:
            if not self._stream.isatty():
                raise IndicatorError(
                    "the status line needs a terminal; the microphone was not opened"
                )
            self.line(f"● {LISTENING}  (m = mute, q = quit)", newline=False)
        except IndicatorError:
            raise
        except Exception as exc:
            raise IndicatorError(
                "the status line could not be printed; the microphone was not opened"
            ) from exc
        if not self.guard.show(LISTENING):
            self.line(
                "■ the window outside the terminal could not be shown; "
                "the microphone was not opened"
            )
            raise IndicatorError(
                "the window outside the terminal could not be shown; "
                "the microphone was not opened"
            )
        return ListeningShown(_TOKEN)

    def show_recording(self) -> bool:
        """The guard window says RECORDING before the request is recorded."""
        return bool(self.guard.show("RECORDING your request"))

    def closed(self) -> None:
        """The microphone was closed: the window may go once the guard sees it."""
        self.guard.hide()
