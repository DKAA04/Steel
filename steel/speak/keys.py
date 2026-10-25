"""Stopping a spoken reply from the keyboard (docs/SPEAK.md section 6).

The terminal stays in its normal line mode, so what the owner types is echoed
and editable; a line becomes readable when they press Enter. That Enter stops
playback, and a non-empty line is kept so the chat can use it as the next
message. Keys typed before the reply started are thrown away, so an earlier
Enter cannot stop it.
"""

from __future__ import annotations

import os
import select
import termios


class TypedLineStop:
    def __init__(self, fd: int = 0) -> None:
        self._fd = fd
        self._line: str | None = None

    def begin(self) -> None:
        """Forget earlier typing, before a reply is spoken."""
        self._line = None
        if os.isatty(self._fd):
            termios.tcflush(self._fd, termios.TCIFLUSH)

    def stop_requested(self) -> bool:
        ready, _, _ = select.select([self._fd], [], [], 0)
        if not ready:
            return False
        data = os.read(self._fd, 4096)  # b"" at end of input also stops
        self._line = data.decode("utf-8", errors="replace").strip()
        return True

    def take_line(self) -> str | None:
        """The line typed to stop playback (empty for a bare Enter), once."""
        line, self._line = self._line, None
        return line or None
