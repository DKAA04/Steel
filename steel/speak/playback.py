"""One spoken reply: the only code that opens the speaker (docs/SPEAK.md 6).

Order, every time: show the SPEAKING line, then open the player, then pass the
engine's audio to it in 4 KB chunks until the end or a stop, then stop both
programs (in a ``finally``) and say so. The chunk buffer is overwritten with
zeros after each write and at the end (best effort).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from steel.speak.indicator import SpeakingIndicator
from steel.speak.player import AudioSink
from steel.speak.synth import EngineRun

CHUNK_BYTES = 4096
_POLL = 0.05
_REDRAW_EVERY = 0.5
# Wall-clock slack on top of the audio limit, for a slow or stuck engine.
_WALL_SLACK = 30.0


@dataclass(frozen=True)
class Playback:
    seconds: float  # audio passed to the player
    reason: str  # finished, key, cancelled, limit, no_audio


def play(
    run: EngineRun,
    sink: AudioSink,
    indicator: SpeakingIndicator,
    *,
    rate: int,
    max_seconds: float,
    stop_requested: Callable[[], bool] = lambda: False,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Playback:
    """Play what the engine produces. Raises SpeakingIndicatorError (nothing
    plays) or the player's error; the engine is stopped in every case."""
    try:
        shown = indicator.show()  # 1. visible first ...
        try:
            sink.open(shown, rate)  # 2. ... then the speaker
        except BaseException:
            indicator.failed()
            raise
    except BaseException:
        run.stop()
        raise
    buffer = bytearray(CHUNK_BYTES)
    view = memoryview(buffer)
    played = 0
    started = clock()
    last_drawn = 0.0
    reason = "finished"

    def interrupted() -> str | None:
        if cancelled():
            return "cancelled"
        if stop_requested():
            return "key"
        if clock() - started > max_seconds + _WALL_SLACK:
            return "limit"
        return None

    try:
        while True:
            stop = interrupted()
            if stop:
                reason = stop
                break
            if not run.readable(_POLL):
                continue
            count = run.read_into(view)
            if not count:
                break  # the engine is done
            sink.write(view[:count])
            buffer[:count] = bytes(count)
            played += count
            if played / (2 * rate) >= max_seconds:
                reason = "limit"
                break
            elapsed = clock() - started
            if elapsed - last_drawn >= _REDRAW_EVERY:
                indicator.update(elapsed)
                last_drawn = elapsed
        if reason == "finished":
            sink.finish()  # let the player play what it holds, then exit
            while not sink.done():
                stop = interrupted()
                if stop:
                    reason = stop
                    break
                sleep(_POLL)
            if played == 0:
                reason = "no_audio"
    finally:
        run.stop()
        sink.stop()
        buffer[:] = bytes(len(buffer))
        seconds = played / (2 * rate)
        if reason in ("finished", "no_audio"):
            indicator.finished(seconds)
        else:
            indicator.stopped(seconds)
    return Playback(seconds=seconds, reason=reason)
