"""What the recorder needs from a microphone, and the audio format STEEL uses.

Tests use a fake ``AudioSource``; the real one is ``steel.voice.mic``.
"""

from __future__ import annotations

from typing import Protocol

from steel.voice.indicator import IndicatorShown

SAMPLE_RATE = 16000  # what Whisper expects
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono
CHUNK_BYTES = BYTES_PER_SECOND // 10  # about 100 ms per read


class AudioSource(Protocol):
    """Raw 16-bit little-endian mono PCM at 16 kHz, in RAM only."""

    def open(self, shown: IndicatorShown) -> None:
        """Open the microphone. *shown* is the proof the REC line is on screen."""

    def read(self) -> bytes:
        """The next chunk (about 100 ms), blocking. ``b""`` means the stream ended."""

    def close(self) -> None:
        """Close the microphone. Safe to call more than once."""
