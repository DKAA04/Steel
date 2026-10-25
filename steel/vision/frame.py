"""One camera frame in RAM: scaling it down and encoding it for the model.

Nothing here touches a file. The frame is raw RGB in a ``bytearray`` so it can be
overwritten with zeros (best effort: Python may keep other copies, and the PNG
and base64 text below are immutable). NumPy is imported by name because its type
stubs need Python 3.12 while mypy checks 3.11.
"""

from __future__ import annotations

import base64
import importlib
import struct
import zlib
from dataclasses import dataclass
from typing import Any


@dataclass
class Frame:
    """Raw RGB, 3 bytes per pixel, row by row."""

    rgb: bytearray
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("a frame needs a positive size")
        if len(self.rgb) != self.width * self.height * 3:
            raise ValueError("the pixel data does not match the frame size")

    def wipe(self) -> None:
        """Overwrite the pixels with zeros."""
        self.rgb[:] = bytes(len(self.rgb))


def mean_brightness(frame: Frame) -> float:
    """Average of every red, green and blue byte, from 0 (black) to 255 (white)."""
    np = importlib.import_module("numpy")
    # frombuffer shares the frame's memory: no copy of the picture is made.
    return float(np.frombuffer(frame.rgb, dtype=np.uint8).mean())


def target_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """The size with the longest side at most *max_side*; never enlarged."""
    longest = max(width, height)
    if longest <= max_side:
        return width, height
    scale = max_side / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _area_weights(np: Any, old: int, new: int) -> Any:
    """Matrix (new x old): each new pixel averages the old pixels it covers."""
    step = old / new
    edges = np.arange(new + 1) * step
    low, high = edges[:-1, None], edges[1:, None]
    pixel = np.arange(old)[None, :]
    overlap = np.clip(np.minimum(high, pixel + 1) - np.maximum(low, pixel), 0, None)
    return (overlap / step).astype(np.float32)


def downscale(frame: Frame, max_side: int) -> Frame:
    """A new frame whose longest side is at most *max_side* (area averaging).

    The original is not changed; the caller wipes both. A frame that is already
    small enough comes back as a copy at the same size.
    """
    width, height = target_size(frame.width, frame.height, max_side)
    if (width, height) == (frame.width, frame.height):
        return Frame(bytearray(frame.rgb), width, height)
    np = importlib.import_module("numpy")
    pixels = np.frombuffer(frame.rgb, dtype=np.uint8).astype(np.float32)
    pixels = pixels.reshape(frame.height, frame.width * 3)
    weights_y = _area_weights(np, frame.height, height)
    weights_x = _area_weights(np, frame.width, width)
    rows = (weights_y @ pixels).reshape(height, frame.width, 3)
    # matmul runs on the fast BLAS path; the same sum written with einsum is slow.
    small = (rows.transpose(0, 2, 1) @ weights_x.T).transpose(0, 2, 1)
    out = bytearray(np.rint(small).clip(0, 255).astype(np.uint8).tobytes())
    for array in (pixels, rows, small):
        array[...] = 0  # best effort: the float copies of the picture
    return Frame(out, width, height)


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def to_png(frame: Frame) -> bytes:
    """A PNG built in memory (standard library only; no imaging library)."""
    np = importlib.import_module("numpy")
    rows = np.zeros((frame.height, 1 + frame.width * 3), dtype=np.uint8)
    rows[:, 1:] = np.frombuffer(frame.rgb, dtype=np.uint8).reshape(frame.height, -1)
    compressed = zlib.compress(memoryview(rows), 1)  # filter byte 0 on every row
    rows[...] = 0
    header = struct.pack(">IIBBBBB", frame.width, frame.height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def encode_for_model(frame: Frame, max_side: int) -> tuple[str, tuple[int, int]]:
    """Downscale, encode as PNG and base64; return the text and the size sent.

    The scaled copy is wiped before returning. The caller wipes *frame*.
    """
    small = downscale(frame, max_side)
    try:
        png = to_png(small)
        return base64.b64encode(png).decode("ascii"), (small.width, small.height)
    finally:
        small.wipe()
