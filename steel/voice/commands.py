"""``steel voice ...`` and ``steel chat --voice`` (docs/VOICE.md).

``check`` is read only and opens no microphone: it lists capture devices from
``/proc/asound/pcm`` (a plain file read, no device is opened) and never starts a
subprocess. The others need the consent file; ``listen`` and ``chat --voice``
also need the owner's grant, because they run the microphone tool through
``build_executor`` as the agent ``voice``.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjarvis.core.types import ToolCall

from steel.chat import LEAVE_HINT, ChatSession, finish, is_leave_line
from steel.home import MODELS_DIR, POLICY_FILE
from steel.security import PolicyError, SteelPolicy, policy_or_deny_all
from steel.security.executor import build_executor
from steel.slash import SlashCommands, banner, build_slash
from steel.tools import MICROPHONE_TOOL, get_microphone_tool
from steel.voice.config import voice_config
from steel.voice.consent import (
    CONSENT_TEXT,
    consent_path,
    read_consent,
    record_consent,
    revoke_consent,
)
from steel.voice.stt import model_dir
from steel.voice.terminal import Terminal

VOICE_AGENT = "voice"
PCM_LIST = Path("/proc/asound/pcm")
CANCEL_WAIT_SECONDS = 3.0

EXAMPLE_GRANT = """{
  "agents": [
    {"agent_id": "voice",
     "grants": [{"capability": "sensor:microphone", "pattern": "microphone_listen"}]}
  ]
}"""


def capture_devices(pcm_list: Path | None = None) -> list[str] | None:
    """Names of the capture devices, from the kernel's list. None if unreadable.

    Reading this file opens no audio device. The path is looked up at call time
    so tests can point it at a fake list.
    """
    try:
        lines = (pcm_list or PCM_LIST).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = []
    for line in lines:
        # "01-00: ALC257 Analog : ALC257 Analog : playback 1 : capture 1"
        ident, _, rest = line.partition(":")
        if "capture" in rest:
            found.append(
                f"card {ident.split('-')[0]} device {ident.split('-')[-1]}:"
                f" {rest.split(':')[0].strip()}"
            )
    return found


def _directory_size_mib(path: Path) -> int:
    return round(sum(p.stat().st_size for p in path.iterdir() if p.is_file()) / 2**20)


def voice_check(home: Path, *, pcm_list: Path | None = None) -> int:
    """Report what voice input has and lacks. Read only; no microphone is opened."""
    config = voice_config()
    print("STEEL voice check (read only: the microphone is not opened)")
    ready = True

    devices = capture_devices(pcm_list)
    if devices is None:
        print("capture devices: cannot be listed on this system")
    elif devices:
        print("capture devices: " + "; ".join(devices))
    else:
        ready = False
        print("capture devices: none found")

    arecord = shutil.which("arecord")
    print(f"capture program: {arecord or 'arecord NOT found (install alsa-utils)'}")
    ready &= arecord is not None

    try:
        version = importlib.metadata.version("faster-whisper")
        print(f"speech backend: faster-whisper {version} installed (local; no cloud)")
    except importlib.metadata.PackageNotFoundError:
        ready = False
        print("speech backend: faster-whisper NOT installed (docs/VOICE.md section 3)")

    directory = model_dir(home, config.model)
    if directory.is_dir():
        print(
            f"speech model: {config.model}, {_directory_size_mib(directory)} MiB "
            f"in {directory}"
        )
    else:
        ready = False
        print(f"speech model: {config.model} MISSING (expected in {home / MODELS_DIR})")
    languages = ", ".join(config.languages) or "auto (all languages)"
    print(f"languages: {languages}; at most {config.max_seconds:g} s per recording")
    print(f'vocabulary hint: "{config.hint}" (set STEEL_VOICE_HINT to add names)')

    print("text to speech: spoken replies are separate, see: steel speak check")
    for name in ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY"):
        if os.environ.get(name):
            print(
                f"note: {name} is set in this environment; STEEL's voice code "
                "never reads it and has no cloud backend"
            )

    stamp = read_consent(home)
    if stamp:
        print(f"consent: present (recorded {stamp}); withdraw with: steel voice revoke")
    else:
        ready = False
        print("consent: MISSING (run: steel voice consent)")

    try:
        policy = SteelPolicy.from_file(home / POLICY_FILE)  # no audit: read only
        allowed, reason = policy.preview(
            VOICE_AGENT, "sensor:microphone", MICROPHONE_TOOL
        )
        grant = (
            f"agent '{VOICE_AGENT}' may use {MICROPHONE_TOOL}"
            if allowed
            else f"NOT granted to agent '{VOICE_AGENT}' ({reason})"
        )
    except PolicyError as exc:
        allowed, grant = False, f"grants file INVALID, everything is denied ({exc})"
    print(f"grant: {grant}")
    if not allowed:
        ready = False
        print(f"  to grant it, write {home / POLICY_FILE} by hand (mode 600):")
        print("  " + EXAMPLE_GRANT.replace("\n", "\n  "))

    print("ready: " + ("yes" if ready else "no"))
    return 0 if ready else 1


def voice_consent(
    home: Path,
    *,
    input_fn: Callable[[str], str] | None = None,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
) -> int:
    """Show the consent text; record consent only on an explicit typed ``yes``."""
    print(CONSENT_TEXT)
    existing = read_consent(home)
    if existing:
        print(f"\n(Consent was already recorded at {existing}.)")
    if not is_terminal():
        # Not at a keyboard: a script or pipe must not consent for the owner.
        print(
            "\nConsent must be given at a terminal. Nothing was recorded.",
            file=sys.stderr,
        )
        return 1
    try:
        answer = (input_fn or input)("> ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        print("Nothing was recorded.")
        return 1
    stamp = record_consent(home)
    print(f"Consent recorded at {stamp} in {consent_path(home)}.")
    print("Withdraw it with: steel voice revoke")
    print(
        "The microphone also needs your grant in steel_policy.json (see: steel voice check)."
    )
    return 0


def voice_revoke(home: Path) -> int:
    if revoke_consent(home):
        print(
            f"Consent withdrawn ({consent_path(home).name} deleted). "
            "The microphone will refuse until you consent again."
        )
    else:
        print("No consent was recorded; nothing to withdraw.")
    return 0


def no_consent() -> int:
    print(
        "steel: no voice consent is recorded, so the microphone stays closed.\n"
        "Read and agree with: steel voice consent",
        file=sys.stderr,
    )
    return 1


def _executor(home: Path, tool: Any) -> Any:
    policy, problem = policy_or_deny_all(home)
    if problem is not None:
        print(
            f"steel: grants file invalid ({problem}); nothing is granted.",
            file=sys.stderr,
        )
    return build_executor([tool], policy=policy, agent_id=VOICE_AGENT)


def listen_once(executor: Any, tool: Any) -> Any:
    """One tool call. Ctrl-C asks the recording to stop and waits for it to end."""
    call = ToolCall(id="voice", name=MICROPHONE_TOOL, arguments="{}")
    try:
        return executor.execute(call)
    except KeyboardInterrupt:
        tool.cancel()
        tool.wait_idle(CANCEL_WAIT_SECONDS)
        raise


def voice_listen(home: Path, *, tool: Any = None) -> int:
    """One push-to-talk capture through the executor; prints the transcript."""
    if read_consent(home) is None:
        return no_consent()
    tool = tool or get_microphone_tool(home)
    executor = _executor(home, tool)
    try:
        result = listen_once(executor, tool)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    if not result.success:
        print(f"Not run: {result.content}", file=sys.stderr)
        return 1
    print(result.content if result.content else "Nothing heard.")
    return 0


def run_voice_repl(
    session: ChatSession,
    home: Path,
    *,
    speak: bool = False,
    terminal: Terminal | None = None,
    tool: Any = None,
    speaker: Any = None,
    slash: SlashCommands | None = None,
) -> int:
    """``steel chat --voice``. An empty line means "speak"; typing still works.

    The transcript is shown first. Enter sends it through ``session.ask`` (the
    same path as typed text: guardrails, user-only fact extraction); Esc discards
    it, and a discarded transcript is never passed on. A TYPED line that starts
    with "/" is a slash command (docs/INJECTION.md); a transcript never is. With
    ``speak`` each reply is read aloud (docs/SPEAK.md), never while recording.
    """
    terminal = terminal or Terminal()
    slash = slash or build_slash(home, session, terminal)
    tool = tool or get_microphone_tool(home, push_to_talk=terminal)
    executor = _executor(home, tool)
    if not speak:
        speaker = None  # replies are spoken only when --speak was passed
    elif speaker is None:
        from steel.speak.commands import ChatSpeaker

        speaker = ChatSpeaker(home)
    print(f"STEEL chat (voice) - model {session.model}. The model cannot run tools.")
    print("Press Enter on an empty line to speak, or type a message.")
    print(LEAVE_HINT)
    print(banner(slash))
    if speaker is not None:
        from steel.speak.commands import REMINDER

        print(REMINDER)
    if session.service is None:
        print("Automatic memory is OFF ([memory] enabled = false).")
    pending: str | None = None  # typed while a reply was being spoken
    try:
        while True:
            if pending is not None:
                line, pending = pending.strip(), None
                print(f"you> {line}")
            else:
                try:
                    line = input("you> ").strip()
                except EOFError:
                    break
            if is_leave_line(line):  # the typed line only, never a transcript
                break
            if line.startswith("/"):
                slash.handle(line)  # typed by the owner; never a transcript
                continue
            if line:
                text = line
            else:
                terminal.arm()  # this Enter is the start key
                try:
                    result = listen_once(executor, tool)
                except KeyboardInterrupt:
                    print("\nCancelled.")
                    continue
                if not result.success:
                    print(f"Not run: {result.content}")
                    continue
                text = result.content.strip()
                if not text:
                    print("Nothing heard.")
                    continue
                print(f'You said: "{text}"')
                print("Enter = send, Esc = discard")
                if not terminal.confirm():
                    print("Discarded. Nothing was sent or remembered.")
                    continue
            reply = session.ask(text)
            print("steel>", reply)
            if speaker is not None:
                pending = speaker.say(reply)  # after the recording, never during
    except KeyboardInterrupt:
        print()
    finally:
        finish(session)
    return 0
