"""Push-to-talk keys on the real terminal (docs/VOICE.md 6.2).

A terminal reports a key press but never a release, so this is a toggle:
Enter starts a recording and Enter stops it. Confirming a transcript is a single
key: Enter sends, Esc discards. Everything reads the file descriptor it is given
(stdin by default), so tests drive it with a pseudo-terminal.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from typing import TextIO

# An Esc followed by more bytes within this time is an arrow key or similar.
_ESC_WAIT = 0.05
# A key press at the chat prompt stays valid as "start" for this long.
_ARM_SECONDS = 10.0


class Terminal:
    """Start, stop and confirm keys. Refuses to work without a real terminal."""

    def __init__(self, fd: int = 0, out: TextIO | None = None) -> None:
        self._fd = fd
        self._out = out if out is not None else sys.stderr
        self._armed_at: float | None = None

    def _usable(self) -> bool:
        return os.isatty(self._fd)

    def _flush_typeahead(self) -> None:
        # Keys typed earlier must not start or stop a recording.
        termios.tcflush(self._fd, termios.TCIFLUSH)

    def arm(self) -> None:
        """Note that the owner just pressed Enter at a prompt (the chat's)."""
        self._armed_at = time.monotonic()

    def wait_for_start(self, timeout: float) -> bool:
        """Block until the owner presses Enter. False on timeout or no terminal."""
        if not self._usable():
            return False
        armed, self._armed_at = self._armed_at, None
        if armed is not None and time.monotonic() - armed <= _ARM_SECONDS:
            self._flush_typeahead()
            return True
        self._flush_typeahead()
        self._out.write("Press Enter to start recording (Ctrl-C cancels). ")
        self._out.flush()
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            self._out.write("\nNo key pressed; the microphone was not opened.\n")
            self._out.flush()
            return False
        os.read(self._fd, 4096)  # the line that was typed
        return True

    def stop_pressed(self) -> bool:
        """True once the owner has pressed Enter since the recording started."""
        ready, _, _ = select.select([self._fd], [], [], 0)
        if not ready:
            return False
        os.read(self._fd, 4096)  # b"" at end of input also counts as a stop
        return True

    def _skip_escape_sequence(self) -> None:
        """Consume the rest of an arrow key (Esc [ A) or similar, and nothing more."""
        if os.read(self._fd, 1) not in (b"[", b"O"):
            return  # Esc plus one key (Alt-x): ignored
        while True:
            byte = os.read(self._fd, 1)
            if not byte or 0x40 <= byte[0] <= 0x7E:  # the final byte ends it
                return

    def confirm(self) -> bool:
        """One key: Enter = True (send), Esc = False (discard). Others are ignored."""
        if not self._usable():
            return False  # without a terminal, nothing is ever confirmed
        saved = termios.tcgetattr(self._fd)
        try:
            tty.setcbreak(self._fd)
            self._flush_typeahead()
            while True:
                try:
                    key = os.read(self._fd, 1)
                except KeyboardInterrupt:
                    return False
                if key in (b"\n", b"\r"):
                    return True
                if key == b"":
                    return False  # input closed
                if key == b"\x1b":
                    more, _, _ = select.select([self._fd], [], [], _ESC_WAIT)
                    if not more:
                        return False  # a lone Esc
                    self._skip_escape_sequence()
        finally:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, saved)
