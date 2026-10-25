"""``steel speak check|say`` and the spoken replies of ``steel chat --speak``.

``check`` is read only: it plays nothing, opens no audio device and starts no
program (the engine's version is read from its metadata, the grant is asked
without logging). Speaking runs the ``speak`` tool through ``build_executor``
as the agent ``voice`` (docs/SPEAK.md section 6).
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjarvis.core.types import ToolCall

from steel.home import POLICY_FILE
from steel.security import PolicyError, SteelPolicy, policy_or_deny_all
from steel.security.executor import build_executor
from steel.speak.config import SAY_MAX_CHARS, speak_config
from steel.speak.keys import TypedLineStop
from steel.speak.language import pick_language
from steel.speak.synth import PIPER_VERSION, engine_dir, engine_python, engine_version
from steel.speak.voices import (
    LANGUAGE_NAMES,
    VOICES,
    installed,
    voice_dir,
    voices_root,
)
from steel.timings import SpeechTiming
from steel.tools import SPEAK_TOOL, get_speak_tool

SPEAK_AGENT = "voice"
CANCEL_WAIT_SECONDS = 3.0
REMINDER = (
    "Replies are read aloud through your speakers; anyone nearby can hear them. "
    "Enter, a typed line or Ctrl-C stops a reply."
)
EXAMPLE_GRANT = """{"agent_id": "voice",
 "grants": [{"capability": "audio:play", "pattern": "speak"}]}"""


def _policy(home: Path) -> SteelPolicy:
    policy, problem = policy_or_deny_all(home)
    if problem is not None:
        print(
            f"steel: grants file invalid ({problem}); nothing is granted.",
            file=sys.stderr,
        )
    return policy


def _executor(home: Path, tool: Any, policy: SteelPolicy | None = None) -> Any:
    policy = policy or _policy(home)
    return build_executor([tool], policy=policy, agent_id=SPEAK_AGENT)


def _call(text: str) -> ToolCall:
    return ToolCall(id="speak", name=SPEAK_TOOL, arguments=json.dumps({"text": text}))


class ChatSpeaker:
    """Speaks each reply of one chat session. Silent for good after a denial."""

    def __init__(self, home: Path, *, tool: Any = None, keys: Any = None) -> None:
        self.keys = keys or TypedLineStop()
        self.tool = tool or get_speak_tool(home, keys=self.keys)
        policy = _policy(home)
        self._executor = _executor(home, self.tool, policy)
        # Read only (no decision is logged): the engine is started early only
        # if speaking would be allowed. Speaking itself goes through the executor.
        self._may_prepare = policy.preview(SPEAK_AGENT, "audio:play", SPEAK_TOOL)[0]
        self.silenced = False
        self.last_timing: SpeechTiming | None = None  # for --timings

    def prepare(self, message: str) -> None:
        """Before the model is asked: start the engine for the voice the reply
        will probably need (the language of the owner's message), so it loads
        while the model writes. Only the voice choice depends on the message;
        the message itself never reaches the engine."""
        if self.silenced or not self._may_prepare:
            return
        self.tool.prepare(pick_language(message))

    def close(self) -> None:
        """End of the chat: no engine is left waiting."""
        self.tool.close()

    def say(self, reply: str) -> str | None:
        """Speak *reply*; return the line the owner typed to stop it, if any."""
        self.last_timing = None
        if self.silenced or not reply.strip():
            return None
        try:
            result = self._executor.execute(_call(reply))
        except KeyboardInterrupt:
            # Ctrl-C stops this reply only; the chat goes on.
            self.tool.cancel()
            self.tool.wait_idle(CANCEL_WAIT_SECONDS)
            return None
        if result.success:
            first = result.metadata.get("first_audio_seconds")
            self.last_timing = SpeechTiming(
                first_audio=first if isinstance(first, (int, float)) else None,
                played=float(result.metadata.get("seconds", 0.0)),
            )
            return self.keys.take_line()
        if result.content.startswith("Capability '"):
            self.silenced = True  # asked once, logged once
            print(
                f"Not spoken: '{SPEAK_TOOL}' is not granted to agent '{SPEAK_AGENT}', "
                "so replies stay silent in this chat. Grant it by hand in "
                f"{POLICY_FILE} (docs/SPEAK.md section 6)."
            )
        elif result.metadata.get("refused") != "empty":
            print(f"({result.content})")
        return None


def _grant_line(home: Path) -> tuple[bool, str]:
    try:
        policy = SteelPolicy.from_file(home / POLICY_FILE)  # no audit: read only
    except PolicyError as exc:
        return False, f"grants file INVALID, everything is denied ({exc})"
    allowed, reason = policy.preview(SPEAK_AGENT, "audio:play", SPEAK_TOOL)
    if allowed:
        return True, f"agent '{SPEAK_AGENT}' may use {SPEAK_TOOL} (audio:play)"
    return False, f"NOT granted to agent '{SPEAK_AGENT}' ({reason})"


def speak_check(
    home: Path, *, which: Callable[[str], str | None] = shutil.which
) -> int:
    """What speaking has and lacks. Plays nothing, opens no device, runs nothing."""
    print("STEEL speak check (read only: nothing is played, no program is started)")
    ready = True
    directory = engine_dir()
    version = engine_version(directory) if engine_python(directory).is_file() else None
    if version:
        print(
            f"engine: piper-tts {version} in {directory} "
            "(GPL-3.0-or-later; run as a separate program, never imported)"
        )
        if version != PIPER_VERSION:
            print(f"  note: STEEL was tested with {PIPER_VERSION}")
    else:
        ready = False
        print(
            f"engine: NOT installed in {directory} (run: python scripts/speak_setup.py)"
        )
    unshare = which("unshare")
    print(
        f"network isolation: {unshare} --net (the engine runs with no network)"
        if unshare
        else "network isolation: unshare NOT found, so the engine is never started"
    )
    ready &= unshare is not None
    player = which("aplay")
    print(f"player: {player or 'aplay NOT found (install alsa-utils)'}")
    ready &= player is not None
    print(f"voices in {voices_root(home)}:")
    for language, name in LANGUAGE_NAMES.items():
        found = [v for v in VOICES if v.language == language and installed(home, v)]
        if not found:
            # English is the fallback for every reply; the others are optional.
            ready &= language != "en"
            print(f"  {name}: none installed ({name} replies are not spoken)")
        for voice in found:
            ok = installed(home, voice, verify_hash=True)
            size = sum(
                (voice_dir(home, voice) / f.name).stat().st_size for f in voice.files()
            )
            print(
                f"  {name}: {voice.name}, {size / 2**20:.0f} MiB, "
                f"SHA-256 {'OK' if ok else 'MISMATCH (run: python scripts/speak_setup.py)'}; "
                f"recordings: {voice.licence}"
            )
            ready &= ok
    allowed, grant = _grant_line(home)
    print(f"grant: {grant}")
    if not allowed:
        ready = False
        print(f"  to grant it, add to {home / POLICY_FILE} by hand (mode 600):")
        print("  " + EXAMPLE_GRANT.replace("\n", "\n  "))
    print(f"spoken length: at most {speak_config().max_chars} characters per reply")
    print("ready: " + ("yes" if ready else "no"))
    return 0 if ready else 1


def speak_say(home: Path, text: str, *, tool: Any = None) -> int:
    """``steel speak say``: one short sentence through the executor."""
    if not text.strip() or len(text) > SAY_MAX_CHARS:
        print(f"steel: say takes 1 to {SAY_MAX_CHARS} characters.", file=sys.stderr)
        return 2
    tool = tool or get_speak_tool(home)
    try:
        result = _executor(home, tool).execute(_call(text))
    except KeyboardInterrupt:
        tool.cancel()
        tool.wait_idle(CANCEL_WAIT_SECONDS)
        return 130
    if not result.success:
        print(
            f"Not run: {result.content}"
            if result.content.startswith("Capability '")
            else result.content,
            file=sys.stderr,
        )
        return 1
    print(result.content)
    return 0
