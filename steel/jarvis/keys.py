"""Single keys while listening (m, q, Esc, Enter), without waiting for Enter,
and the check that the loop is still in the foreground of its terminal."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from typing import Any, Self


class KeyPoll:
    """Reads one key at a time from a terminal in cbreak mode."""

    def __init__(self, fd: int = 0) -> None:
        self._fd = fd
        self._saved: list[Any] | None = None

    def __enter__(self) -> Self:
        if os.isatty(self._fd):
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            termios.tcflush(self._fd, termios.TCIFLUSH)  # earlier keys do not count
        return self

    def __exit__(self, *_: object) -> None:
        if self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None

    def poll(self, timeout: float = 0.0) -> str | None:
        """One key: "m", "q", "esc", "enter", another character, or None."""
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self._fd, 1)
        if not data:
            return "q"  # input closed: stop listening
        if data == b"\x1b":
            more, _, _ = select.select([self._fd], [], [], 0.05)
            if more:
                os.read(self._fd, 16)  # an arrow key or similar: ignored
                return None
            return "esc"
        if data in (b"\n", b"\r"):
            return "enter"
        return data.decode("ascii", "replace").lower()


def in_foreground(fd: int = 0) -> bool:
    """True if this process group owns the terminal (not run with & or bg)."""
    try:
        return os.isatty(fd) and os.tcgetpgrp(fd) == os.getpgrp()
    except OSError:
        return False


def owns_terminal() -> bool:
    """stdin and stderr are a terminal and this process group owns it: the
    loop runs in the foreground of a terminal the owner started."""
    try:
        return (
            os.isatty(sys.stdin.fileno())
            and sys.stderr.isatty()
            and in_foreground(sys.stdin.fileno())
        )
    except (OSError, ValueError):  # no stdin at all, or a closed one
        return False
