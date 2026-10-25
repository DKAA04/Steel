"""speak: the speakers as a policy-gated tool (docs/SPEAK.md section 6).

Declares exactly one capability, ``audio:play``, so it runs only through
``build_executor`` and only if the owner's grants file names this exact tool
(``*`` is refused for it, as for the sensors). It takes the assistant's reply,
decides what of it may be spoken (``steel.speak.text``), picks the voice for
its language, and plays it with the SPEAKING line on screen. The result never
contains the text, and no text reaches the decision log.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from steel.speak.config import SpeakConfig
from steel.speak.indicator import SpeakingIndicator, SpeakingIndicatorError
from steel.speak.playback import play
from steel.speak.player import AudioSink, PlayerUnavailable
from steel.speak.synth import EngineUnavailable, Synthesizer
from steel.speak.text import prepare_speech
from steel.speak.voices import LANGUAGE_NAMES, voice_dir, voice_for

SPEAK_TOOL = "speak"
MAX_INPUT_CHARS = 8000  # longer replies are cut to the spoken limit anyway
_MARGIN = 60.0  # engine start and voice load, on top of the audio limit
_NOT_SPOKEN = {
    "tool data": "it contains a tool result or camera text, which is never read aloud",
    "flagged": "it looks like it contains instructions (or could not be checked)",
    "unredacted": "it could not be checked for secrets",
    "empty": "there is nothing to say",
}


class StopKeys(Protocol):
    def begin(self) -> None: ...

    def stop_requested(self) -> bool: ...


class Speak(BaseTool):
    tool_id = SPEAK_TOOL
    is_local = True

    def __init__(
        self,
        *,
        home: Path,
        config: SpeakConfig,
        engine: Synthesizer,
        open_sink: Callable[[], AudioSink],
        indicator: SpeakingIndicator,
        keys: StopKeys,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._home = home
        self._config = config
        self._engine = engine
        self._open_sink = open_sink
        self._indicator = indicator
        self._keys = keys
        self._cancel = threading.Event()
        self._busy = threading.Lock()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=SPEAK_TOOL,
            description=(
                "Read the assistant's reply aloud through the speakers with a local "
                "engine, with a visible SPEAKING line. Tool results, camera text and "
                "flagged text are never spoken. Returns no text."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": MAX_INPUT_CHARS}
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            category="audio",
            timeout_seconds=self._config.max_audio_seconds + _MARGIN,
            required_capabilities=["audio:play"],
        )

    def prepare(self, language: str) -> bool:
        """Start the engine for this language's voice early (docs/LATENCY.md 8).

        Plays nothing and opens no device: the engine only loads the voice and
        waits for text on its stdin. The text arrives later, through
        ``execute``, after the policy allowed it. Whether an engine is waiting.
        """
        voice = voice_for(self._home, language)
        if voice is None:
            return False
        return self._engine.prepare(voice_dir(self._home, voice) / voice.model.name)

    def close(self) -> None:
        """Stop a waiting engine (end of the chat)."""
        self._engine.close()

    def cancel(self) -> None:
        """Stop a reply being spoken (Ctrl-C)."""
        self._cancel.set()

    def wait_idle(self, timeout: float) -> bool:
        if self._busy.acquire(timeout=timeout):
            self._busy.release()
            return True
        return False

    def execute(self, **params: Any) -> ToolResult:
        started = self._clock()
        text = params.get("text")
        if set(params) != {"text"} or not isinstance(text, str):
            return self._failed("Unexpected arguments.")
        if not text.strip() or len(text) > MAX_INPUT_CHARS:
            return self._failed("The text is empty or too long.")
        speech = prepare_speech(text, max_chars=self._config.max_chars)
        if speech.refused is not None:
            return self._failed(
                f"Not spoken: {_NOT_SPOKEN[speech.refused]}.", speech.refused
            )
        voice = voice_for(self._home, speech.language)
        if voice is None:
            name = LANGUAGE_NAMES[speech.language]
            return self._failed(
                f"Not spoken: no {name} voice is installed "
                "(run: python scripts/speak_setup.py)."
            )
        if not self._busy.acquire(blocking=False):
            return self._failed("The speaker is already in use.")
        self._cancel.clear()
        try:
            self._keys.begin()
            try:
                run = self._engine.start(
                    speech.text, voice_dir(self._home, voice) / voice.model.name
                )
            except EngineUnavailable as exc:
                return self._failed(f"Not spoken: {exc}.")
            try:
                done = play(
                    run,
                    self._open_sink(),
                    self._indicator,
                    rate=voice.sample_rate,
                    max_seconds=self._config.max_audio_seconds,
                    stop_requested=self._keys.stop_requested,
                    cancelled=self._cancel.is_set,
                    clock=self._clock,
                )
            except SpeakingIndicatorError as exc:
                return self._failed(f"Not spoken: {exc}.")
            except (PlayerUnavailable, OSError):
                return self._failed("Not spoken: the speaker could not be used.")
        finally:
            self._busy.release()
        if done.reason == "no_audio":
            return self._failed("Not spoken: the speech engine produced no sound.")
        died = bool(getattr(run, "died", False))
        engine_seconds = getattr(run, "engine_seconds", None)
        words = {
            "finished": "Spoken",
            "key": "Stopped",
            "cancelled": "Stopped",
            "limit": "Stopped at the time limit",
        }
        content = f"{words[done.reason]} ({voice.language}, {done.seconds:.1f} s)."
        if died:
            # One line; the engine is replaced and loads while the owner reads.
            content = f"Stopped ({voice.language}, {done.seconds:.1f} s): the speech engine stopped; it restarts."
        return ToolResult(
            tool_name=SPEAK_TOOL,
            content=content,
            metadata={
                "language": voice.language,
                "voice": voice.name,
                "seconds": round(done.seconds, 1),
                "stopped_by": "engine" if died else done.reason,
                "cut": speech.cut,
                # For --timings: a number only, never the text.
                "engine_seconds": (
                    round(engine_seconds, 3)
                    if isinstance(engine_seconds, (int, float))
                    else None
                ),
                "first_audio_seconds": (
                    None
                    if done.first_audio_at is None
                    else round(done.first_audio_at - started, 3)
                ),
            },
        )

    def _failed(self, message: str, refused: str | None = None) -> ToolResult:
        return ToolResult(
            tool_name=SPEAK_TOOL,
            content=message,
            success=False,
            metadata={"refused": refused} if refused else {},
        )
