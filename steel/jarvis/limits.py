"""Session, idle and wake-rate limits (docs/JARVIS.md section 4)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from steel.jarvis.config import JarvisConfig

HOUR = 3600.0


class Limits:
    def __init__(
        self, config: JarvisConfig, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._config = config
        self._clock = clock
        self.started = clock()
        self.last_activity = self.started  # the start, or the last wake word
        self._wakes: deque[float] = deque()

    def session_left(self) -> float:
        """Seconds until the session limit (negative once it has passed)."""
        return self._config.max_minutes * 60.0 - (self._clock() - self.started)

    def check(self) -> str | None:
        """A limit that ends the session now, or None."""
        now = self._clock()
        if now - self.started >= self._config.max_minutes * 60.0:
            return "session"
        if now - self.last_activity >= self._config.idle_minutes * 60.0:
            return "idle"
        return None

    def wake(self) -> str | None:
        """Count a wake word. "rate" if that was one too many this hour."""
        now = self._clock()
        self.last_activity = now
        self._wakes.append(now)
        while self._wakes and now - self._wakes[0] > HOUR:
            self._wakes.popleft()
        if len(self._wakes) > self._config.max_wakes_per_hour:
            return "rate"
        return None

    def unmuted(self) -> None:
        """Muted time does not count as idle: the owner chose the silence."""
        self.last_activity = self._clock()


MESSAGES = {
    "session": "Session limit reached; the microphone is closed. Start steel jarvis "
    "again to keep listening.",
    "idle": "No wake word for a long time; the microphone is closed and the loop "
    "has ended.",
    "rate": "Too many wake words in the last hour (a noisy room or a television?); "
    "the microphone is closed and the loop has ended.",
}
