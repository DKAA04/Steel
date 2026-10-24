"""The REC line. The microphone cannot be opened without it (docs/VOICE.md 6.3).

``Indicator.show`` prints the first line and returns an ``IndicatorShown``.
``AudioSource.open`` demands one, and only ``show`` can make one, so code that
reaches the microphone without printing the line fails before any sound exists.
If the stream is not a terminal, or the write fails, there is no proof and the
microphone stays closed.

This stops mistakes. It is not a sandbox: code in this process can still forge
a proof (docs/VOICE.md section 8).
"""

from __future__ import annotations

import sys
from typing import TextIO

_TOKEN = object()


class IndicatorError(RuntimeError):
    """The REC line could not be shown, so the microphone must not open."""


class IndicatorShown:
    """Proof that the REC line was written to a terminal. Only show() makes one."""

    __slots__ = ("text",)

    def __init__(self, text: str, token: object) -> None:
        if token is not _TOKEN:
            raise TypeError("only Indicator.show() can make this proof")
        self.text = text


def rec_line(seconds: float, max_seconds: float) -> str:
    whole = int(seconds)
    return (
        f"● REC {whole // 60:02d}:{whole % 60:02d}  Enter = stop, {max_seconds:g} s max"
    )


class Indicator:
    """Draws the recording state on a terminal stream (stderr by default)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._width = 0

    def _draw(self, text: str, *, newline: bool = False) -> None:
        # Overwrite the previous line completely, then flush so it is on screen
        # before anything else happens.
        padded = text.ljust(self._width)
        self._width = len(text)
        self._stream.write("\r" + padded + ("\n" if newline else ""))
        self._stream.flush()

    def show(self, max_seconds: float) -> IndicatorShown:
        """Print the first REC line and return the proof. Raises IndicatorError."""
        try:
            if not self._stream.isatty():
                raise IndicatorError(
                    "the REC line needs a terminal (stderr is not one); "
                    "the microphone was not opened"
                )
            text = rec_line(0, max_seconds)
            self._draw(text)
        except IndicatorError:
            raise
        except Exception as exc:  # any failure to print means no microphone
            raise IndicatorError(
                "the REC line could not be printed; the microphone was not opened"
            ) from exc
        return IndicatorShown(text, _TOKEN)

    def update(self, seconds: float, max_seconds: float) -> None:
        self._draw(rec_line(seconds, max_seconds))

    def stopped(self, seconds: float) -> None:
        self._draw(f"■ stopped after {seconds:.1f} s, microphone closed", newline=True)

    def failed(self) -> None:
        self._draw("■ the microphone did not open; nothing was recorded", newline=True)
