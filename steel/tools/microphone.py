"""microphone_listen: the microphone as a policy-gated tool (docs/VOICE.md).

Declares exactly one capability, ``sensor:microphone``, so it runs only through
``build_executor`` and only if the owner's grants file names this exact tool
(the policy refuses a sensor grant with ``*`` or a wildcard). Before a single
sample is read it also needs the consent file, a terminal, and the owner's
start key; the REC line is printed first (``record`` does that).

It returns the transcript text and nothing else. The audio is wiped after the
transcription, whatever happens. No transcript, path or audio reaches the
decision log, and no event bus is attached by the commands that run it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from steel.voice.config import VoiceConfig
from steel.voice.consent import has_consent
from steel.voice.indicator import Indicator, IndicatorError
from steel.voice.recorder import record, wipe
from steel.voice.source import AudioSource
from steel.voice.stt import SpeechModelMissing, Transcript

MICROPHONE_TOOL = "microphone_listen"
# On top of waiting for the start key and recording: model load and transcription.
_PROCESSING_MARGIN = 90.0


class PushToTalk(Protocol):
    def wait_for_start(self, timeout: float) -> bool: ...

    def stop_pressed(self) -> bool: ...


class Transcriber(Protocol):
    def transcribe(self, audio: bytes | bytearray) -> Transcript: ...


class MicrophoneListen(BaseTool):
    tool_id = MICROPHONE_TOOL
    is_local = True

    def __init__(
        self,
        *,
        home: Path,
        config: VoiceConfig,
        open_source: Callable[[], AudioSource],
        transcriber: Transcriber,
        push_to_talk: PushToTalk,
        indicator: Indicator,
    ) -> None:
        self._home = home
        self._config = config
        self._open_source = open_source
        self._transcriber = transcriber
        self._push_to_talk = push_to_talk
        self._indicator = indicator
        self._cancel = threading.Event()
        self._busy = threading.Lock()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=MICROPHONE_TOOL,
            description=(
                "Record one push-to-talk utterance from the microphone and return "
                "the transcript text. Needs the owner's key press, consent and a "
                "visible REC line. The audio is never returned or kept."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            category="sensor",
            # The executor stops waiting after this; the recording itself is
            # bounded far below it, so the microphone is closed long before.
            timeout_seconds=self._config.start_wait_seconds
            + self._config.max_seconds
            + _PROCESSING_MARGIN,
            required_capabilities=["sensor:microphone"],
        )

    def cancel(self) -> None:
        """Ask a recording in progress to stop (Ctrl-C from the command line)."""
        self._cancel.set()

    def execute(self, **params: Any) -> ToolResult:
        # Messages never contain transcript text.
        if params:
            return self._failed("Unexpected argument.")
        if not has_consent(self._home):
            return self._failed(
                "No voice consent is recorded, so the microphone stays closed. "
                "Run: steel voice consent"
            )
        if not self._busy.acquire(blocking=False):
            return self._failed("The microphone is already in use.")
        self._cancel.clear()
        try:
            return self._listen()
        finally:
            self._busy.release()

    def _listen(self) -> ToolResult:
        if not self._push_to_talk.wait_for_start(self._config.start_wait_seconds):
            return self._failed(
                "No start key was pressed; the microphone stayed closed."
            )
        try:
            recording = record(
                self._open_source(),
                self._indicator,
                self._config,
                stop_requested=self._push_to_talk.stop_pressed,
                cancelled=self._cancel.is_set,
            )
        except IndicatorError as exc:
            return self._failed(f"Not recorded: {exc}.")
        except OSError:
            return self._failed("The microphone could not be opened or read.")
        try:
            if recording.reason == "cancelled":
                return self._failed("Cancelled.")
            if recording.seconds == 0:
                return self._failed(
                    "No audio arrived. Is the microphone muted or in use by "
                    "another program?"
                )
            try:
                transcript = self._transcriber.transcribe(recording.audio)
            except SpeechModelMissing as exc:
                return self._failed(str(exc))
            except Exception:  # noqa: BLE001 - never put audio-derived text in a message
                return self._failed("The speech model failed; nothing was transcribed.")
            return ToolResult(
                tool_name=MICROPHONE_TOOL,
                content=transcript.text,
                metadata={
                    "language": transcript.language,
                    "seconds": round(recording.seconds, 1),
                    "stopped_by": recording.reason,
                },
            )
        finally:
            wipe(recording.audio)  # also on every early return above

    def _failed(self, message: str) -> ToolResult:
        return ToolResult(tool_name=MICROPHONE_TOOL, content=message, success=False)
