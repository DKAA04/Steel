"""Fakes for the vision tests. No real camera, vision model or network."""

from __future__ import annotations

import base64
import io
import struct
import zlib
from typing import Any

from steel.vision.frame import Frame
from steel.vision.indicator import CameraShown


def make_frame(
    width: int = 640, height: int = 480, *, left=(200, 0, 0), right=(0, 0, 200)
) -> Frame:
    """A frame whose left half is one colour and right half another."""
    row = bytearray()
    for x in range(width):
        row += bytes(left if x < width // 2 else right)
    return Frame(bytearray(bytes(row) * height), width, height)


def decode_png(b64: str) -> tuple[int, int, bytes]:
    """Width, height and RGB bytes of a base64 PNG made by steel.vision.frame."""
    data = base64.b64decode(b64)
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + body), "bad chunk checksum"
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = 1 + width * 3
    rows = [raw[i * stride + 1 : (i + 1) * stride] for i in range(height)]
    assert all(raw[i * stride] == 0 for i in range(height)), "filter byte not 0"
    return width, height, b"".join(rows)


class FakeFrameSource:
    """Hands out one scripted frame and notes when it was opened and closed.

    ``screen`` (a FakeTTY, optional) is read at the moment of ``open`` so a test
    can see what the owner could see before the camera started.
    """

    def __init__(
        self,
        frame: Frame | None = None,
        *,
        screen: io.StringIO | None = None,
        fail_read: Exception | None = None,
    ) -> None:
        self.frame = frame if frame is not None else make_frame()
        self.fail_read = fail_read
        self.screen = screen
        self.screen_at_open: str | None = None
        self.opened = 0
        self.closed = 0
        self.reads = 0
        self.events: list[str] = []
        self.given: Frame | None = None

    def open(self, shown: CameraShown) -> None:
        assert isinstance(shown, CameraShown), "opened without the CAMERA OPEN proof"
        self.opened += 1
        self.events.append("open")
        if self.screen is not None:
            self.screen_at_open = self.screen.getvalue()

    def read_frame(self) -> Frame:
        self.reads += 1
        if self.fail_read is not None:
            raise self.fail_read
        self.given = self.frame
        return self.frame

    def close(self) -> None:
        self.closed += 1
        self.events.append("close")


class FakeDescriber:
    """Records what it was sent and answers with scripted text."""

    def __init__(self, text: str = "A desk with a mug and a book.") -> None:
        self.text = text
        self.fail: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def describe(self, image_b64: str, question: str) -> str:
        self.calls.append((image_b64, question))
        if self.fail is not None:
            raise self.fail
        return self.text


class FakeConfirm:
    """Stands in for the Enter/Esc key: answers True or False and counts asks."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked = 0

    def confirm(self) -> bool:
        self.asked += 1
        return self.answer


def no_args(*_: Any, **__: Any) -> None:
    return None
