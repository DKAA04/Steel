"""Echo: the listening microphone stays closed until the reply's player has
exited, plus 0.5 s (docs/JARVIS.md section 5). The signal is the player
process's own exit, recorded by ``TrackedSink``, never bytes divided by the
sample rate."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from steel.speak.indicator import SpeakingShown

ECHO_PAUSE = 0.5


class PlayerStillRunning(RuntimeError):
    """The player's exit could not be confirmed: do not listen."""


class TrackedSink:
    """Wraps the speaker (AplaySink) and records when its process has exited."""

    def __init__(self, inner: Any, clock: Callable[[], float] = time.monotonic) -> None:
        self._inner = inner
        self._clock = clock
        self.exited_at: float | None = None

    def open(self, shown: SpeakingShown, rate: int) -> None:
        self._inner.open(shown, rate)

    def write(self, data: memoryview) -> None:
        self._inner.write(data)

    def finish(self) -> None:
        self._inner.finish()

    def done(self) -> bool:
        finished = bool(self._inner.done())
        if finished and self.exited_at is None:
            self.exited_at = self._clock()
        return finished

    def stop(self) -> None:
        self._inner.stop()  # kills and waits (at most 2 s)
        if self.exited_at is None and self._inner.done():
            self.exited_at = self._clock()


class EchoGate:
    """Collects the players of one reply and waits for the last one's exit."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self.sinks: list[TrackedSink] = []

    def make_sink(self, inner: Any) -> TrackedSink:
        sink = TrackedSink(inner, self._clock)
        self.sinks.append(sink)
        return sink

    def wait(self, spoken_at: float) -> float:
        """Block until every player of this reply has exited plus ECHO_PAUSE.

        *spoken_at* is when the speak step returned; it counts when no player
        was started at all. Returns the time the microphone may open again.
        Raises PlayerStillRunning if a player's exit cannot be confirmed.
        """
        exits: list[float] = []
        for sink in self.sinks:
            if sink.exited_at is None:
                sink.stop()  # one more try: kill and wait
            if sink.exited_at is None:
                raise PlayerStillRunning(
                    "the speaker program did not exit; the microphone stays closed"
                )
            exits.append(sink.exited_at)
        self.sinks.clear()
        # The players' own exit is the signal; with none, the end of speaking.
        last = max(exits) if exits else spoken_at
        ready = last + ECHO_PAUSE
        while (now := self._clock()) < ready:
            self._sleep(ready - now)
        return ready
