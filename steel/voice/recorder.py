"""One push-to-talk recording: the only code that opens an AudioSource.

Order, every time: show the REC line, then open the microphone, then read in
~100 ms chunks until a stop reason, then close it (in a ``finally``) and say so.
Audio exists only as a ``bytearray`` in RAM; the caller wipes it after use.
"""

from __future__ import annotations

import sys
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass

from steel.voice.config import VoiceConfig
from steel.voice.indicator import Indicator
from steel.voice.source import BYTES_PER_SECOND, AudioSource

# How often the REC line is redrawn, in seconds of audio.
_REDRAW_EVERY = 0.5
# Wall-clock slack on top of max_seconds, for a source that delivers audio slowly.
_WALL_SLACK = 5.0


@dataclass
class Recording:
    audio: bytearray  # 16-bit mono PCM; the caller must wipe() it
    seconds: float
    reason: str  # stopped, max_length, silence, no_speech, cancelled, ended
    heard_speech: bool
    peak_rms: int


def wipe(buffer: bytearray) -> None:
    """Overwrite audio with zeros (best effort: Python may keep other copies)."""
    buffer[:] = bytes(len(buffer))


def rms(chunk: bytes) -> int:
    """Root-mean-square level of 16-bit little-endian PCM (0 to 32768)."""
    samples = array("h", chunk[: len(chunk) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0
    return int((sum(s * s for s in samples) / len(samples)) ** 0.5)


def record(
    source: AudioSource,
    indicator: Indicator,
    config: VoiceConfig,
    *,
    stop_requested: Callable[[], bool] = lambda: False,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> Recording:
    """Show the REC line, open the microphone, record, close it. Raises on failure.

    ``stop_requested`` is the owner's second key press; ``cancelled`` is Ctrl-C
    from another thread. If the REC line cannot be shown, IndicatorError is raised
    and the microphone is never opened.
    """
    shown = indicator.show(config.max_seconds)  # 1. visible first ...
    try:
        source.open(shown)  # 2. ... then open
    except BaseException:
        indicator.failed()
        raise
    audio = bytearray()
    started = clock()
    reason = "ended"
    heard_speech = False
    quiet_bytes = 0  # quiet since speech (or since the start); bytes, not seconds,
    # so adding up many small chunks has no rounding error
    peak = 0
    last_drawn = 0.0
    try:
        while True:
            chunk = source.read()
            if not chunk:
                break  # the stream ended on its own
            audio += chunk
            seconds = len(audio) / BYTES_PER_SECOND
            level = rms(chunk)
            peak = max(peak, level)
            if level > config.silence_rms:
                heard_speech, quiet_bytes = True, 0
            else:
                quiet_bytes += len(chunk)
            if seconds - last_drawn >= _REDRAW_EVERY:
                indicator.update(seconds, config.max_seconds)
                last_drawn = seconds
            # Strongest reasons first.
            if cancelled():
                reason = "cancelled"
            elif stop_requested():
                reason = "stopped"
            elif seconds >= config.max_seconds:
                reason = "max_length"
            elif clock() - started >= config.max_seconds + _WALL_SLACK:
                reason = "max_length"  # audio arrived too slowly to count the time
            elif (
                heard_speech
                and quiet_bytes >= config.silence_seconds * BYTES_PER_SECOND
            ):
                reason = "silence"
            elif not heard_speech and quiet_bytes >= (
                config.startup_silence_seconds * BYTES_PER_SECOND
            ):
                reason = "no_speech"
            else:
                continue
            break
    except BaseException:
        wipe(audio)  # nothing may be left behind by a failed recording
        raise
    finally:
        source.close()
        indicator.stopped(len(audio) / BYTES_PER_SECOND)
    return Recording(
        audio=audio,
        seconds=len(audio) / BYTES_PER_SECOND,
        reason=reason,
        heard_speech=heard_speech,
        peak_rms=peak,
    )
