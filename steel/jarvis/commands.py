"""``steel jarvis check|consent|revoke|stop`` and ``steel jarvis`` (the loop).

``check`` is read only: it opens no microphone, starts no program, imports no
model and writes no log. ``stop`` needs nothing from a running loop.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from steel import meminfo as meminfo_module
from steel.jarvis.config import jarvis_config
from steel.jarvis.consent import (
    CONSENT_TEXT,
    has_wake_consent,
    read_wake_consent,
    record_wake_consent,
    revoke_wake_consent,
)
from steel.jarvis.listen import WAKE_CAPABILITY, WAKE_TOOL
from steel.jarvis.models import LICENCE, WAKE_FILES, file_ok, models_ready, wake_dir
from steel.jarvis.procs import FALLBACK, read_pidfile, stop
from steel.meminfo import read_meminfo
from steel.voice.consent import has_consent as has_voice_consent

AGENT = "jarvis"
ASOUND_CARDS = Path("/proc/asound/cards")
# The owner's thresholds for starting the loop (docs/JARVIS.md section 10).
GATE_AVAILABLE_KB = 2 * 1024 * 1024
GATE_SWAP_PERCENT = 70.0
GRANTS = (
    (WAKE_CAPABILITY, WAKE_TOOL),
    ("sensor:microphone", "microphone_listen"),
    ("audio:play", "speak"),
)
EXAMPLE_GRANT = """{"agent_id": "jarvis", "grants": [
  {"capability": "sensor:microphone_always_on", "pattern": "wake_listen"},
  {"capability": "sensor:microphone", "pattern": "microphone_listen"},
  {"capability": "audio:play", "pattern": "speak"}
]}"""


def memory_problem(meminfo_text: str) -> str | None:
    """A plain warning if memory is too tight to start, else None."""
    values = read_meminfo(meminfo_text)
    available = values.get("MemAvailable")
    total, free = values.get("SwapTotal", 0), values.get("SwapFree", 0)
    problems = []
    if available is not None and available < GATE_AVAILABLE_KB:
        problems.append(
            f"only {available / 1024 / 1024:.1f} GiB of memory is available"
        )
    if total > 0 and (total - free) / total * 100 > GATE_SWAP_PERCENT:
        problems.append(f"{(total - free) / total * 100:.0f}% of the swap is in use")
    if not problems:
        return None
    return (
        "Memory WARNING: "
        + " and ".join(problems)
        + ". The wake-word engine, Whisper, the chat model and the voices together "
        "may push the computer into swap and make everything slow."
    )


def _read_meminfo() -> str:
    try:  # looked up at call time, so tests can point it at a fake file
        return meminfo_module.MEMINFO.read_text(encoding="ascii", errors="replace")
    except OSError:
        return ""


def _policy(home: Path) -> tuple[Any, str | None]:
    from steel.security import policy_or_deny_all

    policy, problem = policy_or_deny_all(home)
    return policy, (str(problem) if problem else None)


def jarvis_consent(home: Path, *, ask: Callable[[str], str] = input) -> int:
    print(CONSENT_TEXT)
    if ask("> ").strip().lower() != "yes":
        print("Not recorded. The always-on microphone stays off.")
        return 1
    stamp = record_wake_consent(home)
    print(
        f"Always-on consent recorded ({stamp}). Withdraw it with: steel jarvis revoke"
    )
    return 0


def jarvis_revoke(home: Path) -> int:
    if revoke_wake_consent(home):
        print("Always-on consent withdrawn. steel jarvis will not listen any more.")
    else:
        print("There was no always-on consent.")
    return 0


def jarvis_stop(home: Path) -> int:
    for line in stop(home):
        print(line)
    return 0


def jarvis_check(
    home: Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    meminfo: Callable[[], str] = _read_meminfo,
) -> int:
    """What the loop has and lacks. Opens nothing, runs nothing, logs nothing."""
    print("STEEL jarvis check (read only: no microphone is opened, nothing is started)")
    ready = True
    config, problems = jarvis_config()
    for problem in problems:
        print(f"setting: {problem}")
    try:
        text = ASOUND_CARDS.read_text(encoding="ascii", errors="replace")
        listed = [x.strip() for x in text.splitlines() if "]:" in x]
    except OSError:
        listed = []
    print("sound cards: " + ("; ".join(listed) if listed else "none listed"))
    for program, why in (
        ("arecord", "the microphone"),
        ("setpriv", "so the microphone dies with STEEL"),
    ):
        path = which(program)
        print(f"{program}: {path or 'NOT found'} ({why})")
        ready &= path is not None
    engine = find_spec("onnxruntime") is not None and find_spec("numpy") is not None
    print(
        f"engine: onnxruntime {'found' if engine else 'NOT found'} (not imported here)"
    )
    ready &= engine
    window = find_spec("tkinter") is not None and bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )
    print(
        f"window outside the terminal: {'possible (Tk, a display)' if window else 'NOT possible'}"
    )
    ready &= window
    directory = wake_dir(home)
    print(f"wake-word models ({LICENCE}) in {directory}:")
    for pinned in WAKE_FILES:
        ok = file_ok(directory / pinned.name, pinned)
        print(f"  {pinned.name}: {'SHA-256 OK' if ok else 'missing or wrong'}")
        ready &= ok
    if not models_ready(home):
        print("  fetch them with: python scripts/wake_setup.py")
    stamp = read_wake_consent(home)
    print(f"always-on consent: {stamp or 'NONE (run: steel jarvis consent)'}")
    ready &= stamp is not None
    voice = has_voice_consent(home)
    print(
        f"push-to-talk consent (for the request): {'yes' if voice else 'NONE (run: steel voice consent)'}"
    )
    ready &= voice
    policy, invalid = _policy(home)
    if invalid:
        print(f"grants: file INVALID, everything is denied ({invalid})")
        ready = False
    for capability, tool in GRANTS:
        allowed, _reason = policy.preview(AGENT, capability, tool)
        optional = (
            " (optional: answers are only printed without it)"
            if tool == "speak"
            else ""
        )
        print(
            f"grant {tool} ({capability}) for agent {AGENT}: {'yes' if allowed else 'NO'}{optional}"
        )
        if not allowed and tool != "speak":
            ready = False
    if not all(policy.preview(AGENT, c, t)[0] for c, t in GRANTS[:2]):
        print(f"  write by hand in {home / 'steel_policy.json'} (mode 600):")
        print("  " + EXAMPLE_GRANT.replace("\n", "\n  "))
    print(
        f"limits: session {config.max_minutes} min, idle {config.idle_minutes} min, "
        f"{config.max_wakes_per_hour} wake words per hour, threshold {config.threshold:g}, "
        f"countdown {config.confirm_seconds} s, speech model {config.stt_model}"
    )
    warning = memory_problem(meminfo())
    print(warning or "memory: enough to start")
    running = read_pidfile(home)
    print(
        f"running now: {'yes, pid ' + str(running['loop'].pid) if 'loop' in running else 'no'}"
    )
    print("to stop a running loop from another terminal: steel jarvis stop")
    print(FALLBACK)
    print("ready: " + ("yes" if ready else "no"))
    return 0 if ready else 1


@dataclass
class Parts:
    """Everything the loop touches in the real world; tests replace them."""

    which: Callable[[str], str | None] = shutil.which
    meminfo: Callable[[], str] = _read_meminfo
    ask: Callable[[str], str] = input
    foreground: Callable[[], bool] = field(default=lambda: _foreground())
    guard: Any = None  # a GuardClient
    wake_source: Any = None  # (seconds) -> WakeSource
    wake_engine: Any = None  # () -> WakeEngine
    request_source: Any = None  # () -> AudioSource
    transcriber: Any = None
    keys: Any = None  # a KeyPoll
    terminal: Any = None  # steel.voice.terminal.Terminal
    sink: Any = None  # () -> AudioSink
    speak_engine: Any = None
    speak_keys: Any = None  # stops a reply (steel.speak.keys.TypedLineStop)
    screen: Any = None  # the terminal stream for every status line (stderr)
    clock: Callable[[], float] | None = None
    sleep: Callable[[float], None] | None = None


def _foreground() -> bool:
    from steel.jarvis.keys import owns_terminal

    return owns_terminal()


def preflight(home: Path, parts: Parts) -> list[str]:
    """Why the loop cannot start, before anything is opened; [] if it can."""
    missing = []
    if not parts.foreground():
        missing.append(
            "steel jarvis runs only in the foreground of a terminal you started "
            "(not in the background, a pipe or a script)."
        )
    if not has_wake_consent(home):
        missing.append("No always-on consent. Run: steel jarvis consent")
    if not has_voice_consent(home):
        missing.append(
            "No push-to-talk consent (needed for the request). Run: steel voice consent"
        )
    if not models_ready(home):
        missing.append(
            "Wake-word models missing or wrong. Run: python scripts/wake_setup.py"
        )
    for program in ("arecord", "setpriv"):
        if parts.which(program) is None:
            missing.append(f"{program} was not found.")
    policy, problem = _policy(home)
    if problem:
        missing.append(f"The grants file is invalid ({problem}).")
    for capability, tool in GRANTS[:2]:
        if not policy.preview(AGENT, capability, tool)[0]:
            missing.append(
                f"{tool} is not granted to agent {AGENT} (see steel jarvis check)."
            )
    if "loop" in read_pidfile(home):
        missing.append("A Jarvis loop is already running (steel jarvis stop ends it).")
    return missing


def run_jarvis(
    home: Path,
    jarvis_cfg: Any,
    open_chat: Callable[[], Any],
    *,
    remember: bool = False,
    speak: bool = True,
    timings: bool = False,
    parts: Parts | None = None,
) -> int:
    """``steel jarvis``. Returns the exit status."""
    import time

    from steel.chat import finish
    from steel.jarvis.echo import EchoGate
    from steel.jarvis.indicators import GuardClient, Indicators
    from steel.jarvis.keys import KeyPoll
    from steel.jarvis.limits import Limits
    from steel.jarvis.loop import Countdown, JarvisLoop, WakeTrigger
    from steel.jarvis.procs import remove_pidfile, write_pidfile
    from steel.jarvis.source import RequestSource
    from steel.security.executor import build_executor
    from steel.tools import get_microphone_tool, get_speak_tool, get_wake_tool
    from steel.voice.config import voice_config
    from steel.voice.indicator import Indicator

    parts = parts or Parts()
    config, problems = jarvis_cfg
    for problem in problems:
        print(f"steel: {problem}", file=sys.stderr)
    missing = preflight(home, parts)
    if missing:
        print("Not listening; nothing was opened:")
        for line in missing:
            print(f"  - {line}")
        return 1
    warning = memory_problem(parts.meminfo())
    if warning:
        print(warning)
        if parts.ask("Type yes to start anyway: ").strip().lower() != "yes":
            print("Not started. Nothing was opened.")
            return 1
    clock = parts.clock or time.monotonic
    voice = voice_config()
    guard = parts.guard or GuardClient()
    guard.start()
    write_pidfile(home, os.getpid(), guard.pid)
    session = None
    speaker = None
    wake_tool = None
    try:
        session = open_chat()
        echo = EchoGate(clock=clock, sleep=parts.sleep or time.sleep)
        keys = parts.keys or KeyPoll()
        indicators = Indicators(guard, parts.screen)
        limits = Limits(config, clock)
        terminal = parts.terminal
        if terminal is None:
            from steel.voice.terminal import Terminal

            terminal = Terminal()
        wake_tool = get_wake_tool(
            home,
            config=config,
            limits=limits,
            indicators=indicators,
            keys=keys,
            in_foreground=parts.foreground,
            open_source=parts.wake_source,
            make_engine=parts.wake_engine,
            device=voice.device,
            clock=clock,
        )
        mic_tool = get_microphone_tool(
            home,
            config=replace(voice, model=config.stt_model),
            open_source=parts.request_source
            or (lambda: RequestSource(voice.device, voice.max_seconds)),
            transcriber=parts.transcriber,
            push_to_talk=WakeTrigger(terminal, indicators.window_ok),
            **({"indicator": Indicator(parts.screen)} if parts.screen else {}),
        )
        policy, _problem = _policy(home)
        executor = build_executor([wake_tool, mic_tool], policy=policy, agent_id=AGENT)
        if speak and policy.preview(AGENT, "audio:play", "speak")[0]:
            from steel.speak.commands import REMINDER, ChatSpeaker
            from steel.speak.indicator import SpeakingIndicator
            from steel.speak.player import AplaySink

            sink = parts.sink or AplaySink
            options: dict[str, Any] = {}
            if parts.speak_engine is not None:
                options["engine"] = parts.speak_engine
            if parts.speak_keys is not None:
                options["keys"] = parts.speak_keys
            if parts.screen is not None:
                options["indicator"] = SpeakingIndicator(parts.screen)
            speak_tool = get_speak_tool(
                home, open_sink=lambda: echo.make_sink(sink()), **options
            )
            speaker = ChatSpeaker(
                home, tool=speak_tool, agent=AGENT, keys=parts.speak_keys
            )
            print(REMINDER)
        elif speak:
            print("Answers are printed only: speak is not granted to agent jarvis.")
        print(
            "STEEL Jarvis loop. Say 'hey jarvis', then your request. m = mute, "
            "q = quit; from another terminal: steel jarvis stop."
        )
        loop = JarvisLoop(
            session=session,
            executor=executor,
            wake_tool=wake_tool,
            mic_tool=mic_tool,
            indicators=indicators,
            limits=limits,
            keys=keys,
            countdown=Countdown(keys, config.confirm_seconds, clock=clock),
            echo=echo,
            speaker=speaker,
            remember=remember,
            timings=timings,
            clock=clock,
        )
        return loop.run()
    finally:
        if speaker is not None:
            speaker.close()
        if wake_tool is not None:
            wake_tool.wipe()
        guard.close()  # its input ends: it kills any microphone it has seen
        remove_pidfile(home)
        if session is not None:
            finish(session)
