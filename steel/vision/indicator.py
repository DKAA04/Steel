"""The CAMERA OPEN line. The camera cannot be opened without it (docs/VISION.md 5.2).

``CameraIndicator.show`` prints the line and returns a ``CameraShown``.
``FrameSource.open`` demands one, and only ``show`` can make one, so code that
reaches the camera without printing the line fails before any frame exists. If
the stream is not a terminal, or the write fails, there is no proof and the
camera stays closed. A microphone proof is a different class and opens nothing here.

This stops mistakes. It is not a sandbox: code in this process can still forge a
proof (docs/VISION.md section 8).
"""

from __future__ import annotations

import sys
from typing import TextIO

_TOKEN = object()

OPEN_LINE = "● CAMERA OPEN  one picture, then it closes"


class CameraIndicatorError(RuntimeError):
    """The line could not be shown, so the camera must not open."""


class CameraShown:
    """Proof that the CAMERA OPEN line was written to a terminal."""

    __slots__ = ("text",)

    def __init__(self, text: str, token: object) -> None:
        if token is not _TOKEN:
            raise TypeError("only CameraIndicator.show() can make this proof")
        self.text = text


class CameraIndicator:
    """Writes the camera state on a terminal stream (stderr by default)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr

    def _line(self, text: str) -> None:
        self._stream.write(text + "\n")
        self._stream.flush()  # on screen before anything else happens

    def show(self) -> CameraShown:
        """Print the line and return the proof. Raises CameraIndicatorError."""
        try:
            if not self._stream.isatty():
                raise CameraIndicatorError(
                    "the CAMERA OPEN line needs a terminal (stderr is not one); "
                    "the camera was not opened"
                )
            self._line(OPEN_LINE)
        except CameraIndicatorError:
            raise
        except Exception as exc:  # any failure to print means no camera
            raise CameraIndicatorError(
                "the CAMERA OPEN line could not be printed; the camera was not opened"
            ) from exc
        return CameraShown(OPEN_LINE, _TOKEN)

    def closed(self, seconds: float) -> None:
        self._line(f"■ camera closed after {seconds:.1f} s")

    def failed(self) -> None:
        self._line("■ the camera did not open; no picture was taken")
