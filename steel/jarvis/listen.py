"""wake_listen: the always-on microphone as a policy-gated tool (docs/JARVIS.md).

Declares exactly one capability, ``sensor:microphone_always_on``, so it runs
only through ``build_executor`` and only if the owner's grants file names this
exact tool (a sensor: never by wildcard). Before a single sample is read it
needs the always-on consent (the push-to-talk one does not count), the model
files with their pinned SHA-256, and both indicators on screen.

One call is one listening period: it opens the microphone, gives every 80 ms
frame to the wake-word engine and nothing else, and closes the microphone when
the wake word is heard, when the owner mutes or quits, when a limit is reached,
or on any error. It returns a status word and a score. Never audio, never text.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from steel.jarvis.config import JarvisConfig
from steel.jarvis.consent import has_wake_consent
from steel.jarvis.indicators import IndicatorError, Indicators, ListeningShown
from steel.jarvis.limits import Limits
from steel.jarvis.models import models_ready
from steel.jarvis.wakeword import WakeEngine

WAKE_TOOL = "wake_listen"
WAKE_CAPABILITY = "sensor:microphone_always_on"
BEAT_EVERY = 1.0  # seconds between heartbeats to the guard's watchdog
FOREGROUND_EVERY = 1.0


class FrameSource(Protocol):
    def open(self, shown: ListeningShown) -> None: ...

    def read_frame(self) -> bytes: ...

    def close(self) -> None: ...


class Keys(Protocol):
    def poll(self, timeout: float = 0.0) -> str | None: ...


class WakeListen(BaseTool):
    tool_id = WAKE_TOOL
    is_local = True

    def __init__(
        self,
        *,
        home: Path,
        config: JarvisConfig,
        limits: Limits,
        indicators: Indicators,
        open_source: Callable[[float], FrameSource],
        make_engine: Callable[[], WakeEngine],
        keys: Keys,
        in_foreground: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._home = home
        self._config = config
        self._limits = limits
        self._indicators = indicators
        self._open_source = open_source
        self._make_engine = make_engine
        self._engine: WakeEngine | None = None
        self._keys = keys
        self._in_foreground = in_foreground
        self._clock = clock
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._live: FrameSource | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=WAKE_TOOL,
            description=(
                "Listen continuously for the wake word with the microphone open "
                "and both indicators shown. Returns a status, never audio or text."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            category="sensor",
            # The listening itself ends at the session limit, far before this.
            timeout_seconds=self._config.max_minutes * 60.0 + 120.0,
            required_capabilities=[WAKE_CAPABILITY],
        )

    def cancel(self) -> None:
        """Ctrl-C: stop listening. Closing the source also ends a blocked read."""
        self._cancel.set()
        live = self._live
        if live is not None:
            live.close()

    def wait_idle(self, timeout: float) -> bool:
        if self._busy.acquire(timeout=timeout):
            self._busy.release()
            return True
        return False

    def wipe(self) -> None:
        """Zero the engine's buffers (end of the loop)."""
        if self._engine is not None:
            self._engine.wipe()

    def execute(self, **params: Any) -> ToolResult:
        if params:
            return self._result("error", "Unexpected argument.", success=False)
        if not has_wake_consent(self._home):
            return self._result(
                "no_consent",
                "No always-on consent is recorded, so the microphone stays closed. "
                "Run: steel jarvis consent",
                success=False,
            )
        if not models_ready(self._home):
            return self._result(
                "no_model",
                "The wake-word model files are missing or do not match their "
                "SHA-256. Run: python scripts/wake_setup.py",
                success=False,
            )
        if not self._busy.acquire(blocking=False):
            return self._result(
                "busy", "The microphone is already in use.", success=False
            )
        self._cancel.clear()
        try:
            return self._listen()
        finally:
            self._busy.release()

    def _listen(self) -> ToolResult:
        try:
            if self._engine is None:
                self._engine = self._make_engine()
        except Exception:  # noqa: BLE001 - nothing opened yet; say so plainly
            return self._result("engine_error", "The wake-word engine could not start.")
        engine = self._engine
        if not self._in_foreground():
            return self._result(
                "background", "steel jarvis listens only in the foreground."
            )
        limit = self._limits.check()
        if limit:
            return self._result(f"limit:{limit}", limit)
        try:
            shown = self._indicators.show_listening()  # 1. visible first ...
        except IndicatorError as exc:
            # A refusal like a missing consent: the loop prints it and ends.
            return self._result("no_indicator", f"Not listening: {exc}.", success=False)
        source = self._live = self._open_source(self._limits.session_left())
        best = 0.0
        outcome = "mic_ended"
        try:
            source.open(shown)  # 2. ... then the microphone
            last_beat = last_fg = self._clock()
            while True:
                frame = source.read_frame()
                if self._cancel.is_set():
                    outcome = "cancelled"
                    break
                if not frame:
                    outcome = "mic_ended"  # the device or arecord stopped
                    break
                now = self._clock()
                if now - last_beat >= BEAT_EVERY:
                    # From this loop itself: if it hangs, the beats stop.
                    self._indicators.guard.beat()
                    last_beat = now
                if now - last_fg >= FOREGROUND_EVERY:
                    last_fg = now
                    if not self._in_foreground():
                        outcome = "background"
                        break
                key = self._keys.poll(0)
                if key in ("m", "q"):
                    outcome = "muted" if key == "m" else "quit"
                    break
                limit = self._limits.check()
                if limit:
                    outcome = f"limit:{limit}"
                    break
                score = engine.score(frame)
                best = max(best, score)
                if score >= self._config.threshold:
                    rate = self._limits.wake()
                    outcome = "limit:rate" if rate else "woke"
                    break
        except OSError:
            outcome = "mic_error"
        except Exception:  # noqa: BLE001 - an engine failure ends listening cleanly
            outcome = "engine_error"
        finally:
            source.close()  # the microphone process ends here, on every path
            self._live = None
            engine.reset()  # nothing heard before the trigger is kept
            self._indicators.closed()
        return self._result(outcome, outcome, score=best)

    def _result(
        self, outcome: str, content: str, *, success: bool = True, score: float = 0.0
    ) -> ToolResult:
        return ToolResult(
            tool_name=WAKE_TOOL,
            content=content,
            success=success,
            metadata={"outcome": outcome, "score": round(score, 3)},
        )
