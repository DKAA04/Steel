"""Fakes for the voice tests. No real microphone, speaker, model or network."""

from __future__ import annotations

import io
import struct
import time

from steel.voice.indicator import IndicatorShown
from steel.voice.source import BYTES_PER_SECOND, CHUNK_BYTES


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


class NotATTY(io.StringIO):
    def isatty(self) -> bool:
        return False


class BrokenTTY(FakeTTY):
    """A terminal whose write fails (closed pipe, full disk, ...)."""

    def write(self, text: str) -> int:
        raise OSError("cannot write")


def tone(seconds: float, level: int = 3000) -> list[bytes]:
    """100 ms chunks of a square wave whose RMS is exactly *level*."""
    samples = CHUNK_BYTES // 2
    chunk = struct.pack(f"<{samples}h", *([level, -level] * (samples // 2)))
    return [chunk] * round(seconds * BYTES_PER_SECOND / CHUNK_BYTES)


def quiet(seconds: float) -> list[bytes]:
    return tone(seconds, level=0)


class FakeSource:
    """Plays back scripted chunks and notes when it was opened and closed.

    ``screen`` (a FakeTTY, optional) is read at the moment of ``open`` so a test
    can see what the owner could see before the microphone started.
    """

    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        screen: io.StringIO | None = None,
        forever: bytes | None = None,
        fail_read: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.chunks = list(chunks or [])
        self.forever = forever
        self.fail_read = fail_read
        self.delay = delay  # seconds of real time per read, for tests with threads
        self.screen = screen
        self.screen_at_open: str | None = None
        self.opened = 0
        self.closed = 0
        self.reads = 0
        self.events: list[str] = []

    def open(self, shown: IndicatorShown) -> None:
        assert isinstance(shown, IndicatorShown), "opened without the REC proof"
        self.opened += 1
        self.events.append("open")
        if self.screen is not None:
            self.screen_at_open = self.screen.getvalue()

    def read(self) -> bytes:
        self.reads += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail_read is not None:
            raise self.fail_read
        if self.chunks:
            return self.chunks.pop(0)
        return self.forever if self.forever is not None else b""

    def close(self) -> None:
        self.closed += 1
        self.events.append("close")
