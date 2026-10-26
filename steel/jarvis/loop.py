"""The Jarvis loop: listen, hear the wake word, record, confirm, answer, speak.

docs/JARVIS.md sections 4 to 8. Every microphone goes through a policy-gated
tool run by the executor as agent "jarvis": ``wake_listen`` (always-on) and
``microphone_listen`` (the request). The transcript is plain text for the
model and nothing else: no slash command, no camera, no tool is ever run from
it, and without --remember-voice it never reaches the fact extractor.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, TextIO

from openjarvis.core.types import ToolCall

from steel.chat import ChatSession, report_warmup
from steel.jarvis.echo import EchoGate, PlayerStillRunning
from steel.jarvis.limits import MESSAGES, Limits
from steel.jarvis.listen import WAKE_TOOL
from steel.timings import timing_line
from steel.tools import MICROPHONE_TOOL

CANCEL_WAIT_SECONDS = 3.0
_WORD = re.compile(r"\w")

ENDINGS = {
    "quit": "Stopped. The microphone is closed.",
    "cancelled": "Stopped. The microphone is closed.",
    "background": "steel jarvis listens only in the foreground of its terminal; "
    "the microphone is closed and the loop has ended.",
    "mic_ended": "The microphone stopped delivering audio (unplugged, or taken by "
    "another program?); the loop has ended.",
    "mic_error": "The microphone could not be opened or read; the loop has ended.",
    "engine_error": "The wake-word engine failed; the microphone is closed and the "
    "loop has ended.",
}


class Countdown:
    """Shows the transcript and sends it after a few seconds unless Esc."""

    def __init__(
        self,
        keys: Any,
        seconds: int,
        *,
        out: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._keys = keys
        self._seconds = seconds
        self._out = out if out is not None else sys.stdout
        self._clock = clock

    def confirm(self, text: str) -> bool:
        self._out.write(f'You said: "{text}"\n')
        if self._seconds <= 0:
            return True
        deadline = self._clock() + self._seconds
        shown: int | None = None
        while (left := deadline - self._clock()) > 0:
            # Redrawn only when the number changes: if the input is closed,
            # poll() returns at once and this loop must not flood the terminal.
            if math.ceil(left) != shown:
                shown = math.ceil(left)
                self._out.write(
                    f"\rSending in {shown} s. Enter sends now, Esc cancels. "
                )
                self._out.flush()
            key = self._keys.poll(min(left, 0.2))
            if key == "esc":
                self._out.write("\n")
                return False
            if key == "enter":
                break
        self._out.write("\n")
        return True


class WakeTrigger:
    """The request recording's start key is the wake word itself; Enter (or
    silence, or the length limit) still ends it. So does the loss of the
    indicator window: the recording must not go on with nothing to see."""

    def __init__(self, terminal: Any, window_ok: Callable[[], bool] | None = None):
        self._terminal = terminal
        self._window_ok = window_ok

    def wait_for_start(self, timeout: float) -> bool:
        return True

    def stop_pressed(self) -> bool:
        if self._window_ok is not None and not self._window_ok():
            return True
        return bool(self._terminal.stop_pressed())


class JarvisLoop:
    def __init__(
        self,
        *,
        session: ChatSession,
        executor: Any,
        wake_tool: Any,
        mic_tool: Any,
        indicators: Any,
        limits: Limits,
        keys: AbstractContextManager[Any],
        countdown: Countdown,
        echo: EchoGate,
        speaker: Any = None,
        remember: bool = False,
        timings: bool = False,
        out: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.executor = executor
        self.wake_tool = wake_tool
        self.mic_tool = mic_tool
        self.indicators = indicators
        self.limits = limits
        self.keys = keys
        self.countdown = countdown
        self.echo = echo
        self.speaker = speaker
        self.remember = remember
        self.timings = timings
        self.out = out if out is not None else sys.stdout
        self.clock = clock

    def say(self, text: str) -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def run(self) -> int:
        try:
            while True:
                outcome = self._listen()
                if outcome == "woke":
                    if not self._turn():
                        return 1
                elif outcome == "muted":
                    if not self._muted():
                        return 0
                else:
                    return self._end(outcome)
        except KeyboardInterrupt:
            self._cancel_tools()
            self.say("\nStopped. The microphone is closed.")
            return 0

    def _cancel_tools(self) -> None:
        for tool in (self.wake_tool, self.mic_tool):
            tool.cancel()
            tool.wait_idle(CANCEL_WAIT_SECONDS)

    def _run_tool(self, name: str) -> Any:
        return self.executor.execute(
            ToolCall(id=name, name=name, arguments=json.dumps({}))
        )

    def _listen(self) -> str:
        with self.keys:
            result = self._run_tool(WAKE_TOOL)
        outcome = result.metadata.get("outcome")
        if not isinstance(outcome, str):  # denied by the policy, or no answer
            self.say(f"Not listening: {result.content}")
            return "denied"
        if not result.success:
            self.say(result.content)
        return outcome

    def _end(self, outcome: str) -> int:
        if outcome.startswith("limit:"):
            self.say(
                MESSAGES.get(outcome[6:], "A limit was reached; the loop has ended.")
            )
            return 0
        if outcome in ENDINGS:
            self.say(ENDINGS[outcome])
            return 0 if outcome in ("quit", "cancelled") else 1
        return 1  # denied, no consent, no model, no indicator: already said

    def _muted(self) -> bool:
        self.indicators.line(
            "■ MUTED: the microphone is closed. m = listen again, q = quit"
        )
        with self.keys as keys:
            while True:
                if self.limits.session_left() <= 0:
                    self.say(MESSAGES["session"])
                    return False
                key = keys.poll(0.5)
                if key == "m":
                    self.limits.unmuted()
                    return True
                if key == "q":
                    self.say(ENDINGS["quit"])
                    return False

    def _turn(self) -> bool:
        """One request. False ends the loop (a safety rule could not be kept)."""
        woke_at = self.clock()
        self.indicators.line("◆ HEARD WAKE WORD")
        if not self.indicators.show_recording():
            self.say(
                "The window outside the terminal could not be shown, so nothing is "
                "recorded; the loop has ended."
            )
            return False
        try:
            recording = self._run_tool(MICROPHONE_TOOL)
        finally:
            self.indicators.closed()
        if not recording.success:
            self.say(f"({recording.content})")
            # A denial or a missing consent will not change: stop asking.
            return not recording.content.startswith(
                ("Capability '", "No voice consent")
            )
        text = recording.content.strip()
        if not _WORD.search(text):
            self.say("Nothing was heard; cancelled.")
            return True
        with self.keys:  # the countdown reads the same keys, without Enter
            sent = self.countdown.confirm(text)
        if not sent:
            self.say("Cancelled; nothing was sent or kept.")
            return True
        self.indicators.line("… THINKING")
        if self.speaker is not None:
            self.speaker.prepare(text)
        if self.timings:
            report_warmup(self.session)
        # The same session as typed lines; plain text, never a command.
        reply = self.session.ask(text, remember=self.remember)
        self.say(f"steel> {reply}")
        said_at = self.clock()
        if self.speaker is not None:
            self.speaker.say(reply)
        spoken_at = self.clock()
        try:
            self.echo.wait(spoken_at)  # the player has exited, plus 0.5 s
        except PlayerStillRunning as exc:
            self.say(f"{exc}; the loop has ended.")
            return False
        if self.timings:
            self._print_timings(woke_at, said_at)
        return True

    def _print_timings(self, woke_at: float, said_at: float) -> None:
        if self.session.last_timing is None:
            return
        speech = self.speaker.last_timing if self.speaker is not None else None
        line = timing_line(self.session.last_timing, speech)
        if speech is not None and speech.first_audio is not None:
            wake_to_audio = said_at - woke_at + speech.first_audio
            line += f" | wake to first audio {wake_to_audio:.1f} s"
        else:
            line += " | wake to first audio n/a"
        self.say(line)
