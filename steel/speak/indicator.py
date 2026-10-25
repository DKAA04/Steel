"""The SPEAKING line. The speaker cannot be opened without it (docs/SPEAK.md 6).

Like the REC and CAMERA OPEN lines: ``show`` prints the first line and returns a
``SpeakingShown``, the player demands one, and only ``show`` can make one. If
the stream is not a terminal, or the write fails, nothing plays. This stops
mistakes; it is not a sandbox.
"""

from __future__ import annotations

import sys
from typing import TextIO

_TOKEN = object()
STOP_HINT = "Enter or a typed line stops; Ctrl-C stops"


class SpeakingIndicatorError(RuntimeError):
    """The SPEAKING line could not be shown, so nothing may play."""


class SpeakingShown:
    """Proof that the SPEAKING line was written to a terminal."""

    __slots__ = ("text",)

    def __init__(self, text: str, token: object) -> None:
        if token is not _TOKEN:
            raise TypeError("only SpeakingIndicator.show() can make this proof")
        self.text = text


def speaking_line(seconds: float) -> str:
    whole = int(seconds)
    return f"▶ SPEAKING {whole // 60:02d}:{whole % 60:02d}  {STOP_HINT}"


class SpeakingIndicator:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._width = 0

    def _draw(self, text: str, *, newline: bool = False) -> None:
        padded = text.ljust(self._width)
        self._width = len(text)
        self._stream.write("\r" + padded + ("\n" if newline else ""))
        self._stream.flush()

    def show(self) -> SpeakingShown:
        """Print the first line and return the proof. Raises SpeakingIndicatorError."""
        try:
            if not self._stream.isatty():
                raise SpeakingIndicatorError(
                    "the SPEAKING line needs a terminal; nothing was played"
                )
            text = speaking_line(0)
            self._draw(text)
        except SpeakingIndicatorError:
            raise
        except Exception as exc:  # any failure to print means no sound
            raise SpeakingIndicatorError(
                "the SPEAKING line could not be printed; nothing was played"
            ) from exc
        return SpeakingShown(text, _TOKEN)

    def update(self, seconds: float) -> None:
        self._draw(speaking_line(seconds))

    def finished(self, seconds: float) -> None:
        self._draw(f"■ finished speaking ({seconds:.1f} s)", newline=True)

    def stopped(self, seconds: float) -> None:
        self._draw(f"■ stopped speaking ({seconds:.1f} s)", newline=True)

    def failed(self) -> None:
        self._draw("■ the speaker did not open; nothing was played", newline=True)
