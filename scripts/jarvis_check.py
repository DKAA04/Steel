"""Live Jarvis check: listen for "hey jarvis" with the REAL microphone and count.

    python scripts/jarvis_check.py --minutes 10

For the owner to run, in a terminal. It is not part of pytest, and no assistant
runs it (tests/test_jarvis_check_script.py runs it only with fakes, up to the
point where it would open anything).

What it does:
  1. checks, before anything is opened, everything the loop checks: the
     foreground terminal, your always-on consent (steel jarvis consent; the
     push-to-talk consent does not count), the wake-word model files with their
     pinned SHA-256, arecord and setpriv, and your hand-written grant of
     wake_listen (sensor:microphone_always_on) to agent "jarvis";
  2. starts the guard (the red window outside the terminal and the watchdog)
     and writes the pidfile, so "steel jarvis stop" works from another terminal;
  3. listens through the real parts: get_wake_tool, the policy executor as agent
     "jarvis", both indicators shown before the microphone opens, the session,
     idle and wake-rate limits (STEEL_JARVIS_* settings, docs/JARVIS.md 4);
  4. after each wake word the microphone is CLOSED and you are asked whether you
     said "hey jarvis": yes counts a wake, no counts a FALSE trigger; nothing
     after the wake word is recorded (this check never records a request);
  5. at the end prints: minutes listened, wakes, false triggers per hour, CPU
     time per second of audio for the wake-word engine, resident memory, and the
     engine's start-up time.

It keeps no audio: frames go only to the engine's fixed-size buffers and are
zeroed. It writes no results, transcripts or logs. Two files are touched, as by
the loop: while it runs, a private pidfile (~/.openjarvis/steel_jarvis.pid,
mode 600: the process ids and start times of this check and its guard, nothing
else) so that "steel jarvis stop" works from another terminal; it is removed
on every exit (normal end, an error, Ctrl-C). And the policy's names-only audit
lines. telemetry_off() runs before the engine loads onnxruntime.

Keys while listening: m = mute (microphone closed), q = quit, Ctrl-C = quit.
The period you choose is capped by the session limit (STEEL_JARVIS_MAX_MINUTES,
at most 240); the idle limit (STEEL_JARVIS_IDLE_MINUTES, default 20) and the
wake-rate limit (STEEL_JARVIS_MAX_WAKES_PER_HOUR, default 30) still end it.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from steel.pin import PinError, check_openjarvis_pin

MIN_MINUTES, MAX_MINUTES = 1, 240
AGENT = "jarvis"
WAKE_TOOL = "wake_listen"
WAKE_CAPABILITY = "sensor:microphone_always_on"


class MeasuredEngine:
    """Wraps the wake-word engine: counts frames and the CPU time spent scoring."""

    def __init__(self, engine: Any, started_in: float) -> None:
        self.engine = engine
        self.started_in = started_in  # seconds to load the three models
        self.frames = 0
        self.cpu = 0.0

    def score(self, frame: bytes) -> float:
        before = time.process_time()
        try:
            return float(self.engine.score(frame))
        finally:
            self.cpu += time.process_time() - before
            self.frames += 1

    def reset(self) -> None:
        self.engine.reset()

    def wipe(self) -> None:
        self.engine.wipe()


def load_engine(home: Path) -> MeasuredEngine:
    """The real engine, timed. Telemetry is off before onnxruntime can load."""
    from steel.offline import telemetry_off

    telemetry_off()
    from steel.jarvis.models import wake_dir
    from steel.jarvis.wakeword import OpenWakeWord

    started = time.monotonic()
    engine = OpenWakeWord(wake_dir(home))
    return MeasuredEngine(engine, time.monotonic() - started)


def memory_mib() -> tuple[float, float]:
    """(resident now, peak resident) of this process in MiB, from /proc."""
    values = {"VmRSS": 0.0, "VmHWM": 0.0}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in values:
                values[key] = int(rest.split()[0]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return values["VmRSS"], values["VmHWM"]


def problems_before_start(
    home: Path,
    *,
    foreground: Callable[[], bool],
    which: Callable[[str], str | None],
) -> list[str]:
    """Why the check cannot start, before anything is opened; [] if it can."""
    from steel.jarvis.consent import has_wake_consent
    from steel.jarvis.models import models_ready
    from steel.jarvis.procs import read_pidfile
    from steel.security import policy_or_deny_all

    missing = []
    if not foreground():
        missing.append("Run it in the foreground of a terminal you started.")
    if not has_wake_consent(home):
        missing.append("No always-on consent. Run: steel jarvis consent")
    if not models_ready(home):
        missing.append(
            "Wake-word models missing or wrong: python scripts/wake_setup.py"
        )
    for program in ("arecord", "setpriv"):
        if which(program) is None:
            missing.append(f"{program} was not found.")
    policy, problem = policy_or_deny_all(home)
    if problem:
        missing.append(f"The grants file is invalid ({problem}).")
    elif not policy.preview(AGENT, WAKE_CAPABILITY, WAKE_TOOL)[0]:
        missing.append(
            f"{WAKE_TOOL} ({WAKE_CAPABILITY}) is not granted to agent {AGENT}; "
            "see: steel jarvis check"
        )
    if "loop" in read_pidfile(home):
        missing.append("A Jarvis loop is already running (steel jarvis stop ends it).")
    return missing


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live Jarvis check (REAL microphone, always on while it runs)."
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=10,
        help=f"how long to listen, {MIN_MINUTES} to {MAX_MINUTES} (default 10)",
    )
    args = parser.parse_args(argv)
    if not MIN_MINUTES <= args.minutes <= MAX_MINUTES:
        parser.error(f"--minutes must be from {MIN_MINUTES} to {MAX_MINUTES}")
    return args


def _ask(prompt: str, ask: Callable[[str], str]) -> bool:
    try:
        return ask(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def report(
    out: Any,
    *,
    listened: float,
    wakes: int,
    false_triggers: int,
    engine: MeasuredEngine | None,
    ended: str,
) -> None:
    hours = listened / 3600
    print(f"\nended: {ended}", file=out)
    print(f"listened: {listened / 60:.1f} min (listening periods)", file=out)
    print(f"wake words you confirmed: {wakes}", file=out)
    rate = f"{false_triggers / hours:.1f} per hour" if hours > 0 else "n/a"
    print(f"false triggers: {false_triggers} ({rate})", file=out)
    if engine is not None and engine.frames:
        audio = engine.frames * 0.08
        print(
            f"engine CPU: {engine.cpu / audio * 1000:.1f} ms per second of audio "
            f"({engine.frames} frames)",
            file=out,
        )
        print(f"engine start-up: {engine.started_in:.2f} s", file=out)
    rss, peak = memory_mib()
    print(f"resident memory: {rss:.0f} MiB now, {peak:.0f} MiB peak", file=out)
    if hours < 0.5:
        print(
            "note: under 30 minutes the false-trigger rate is a very small sample.",
            file=out,
        )


def main(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    foreground: Callable[[], bool] | None = None,
    which: Callable[[str], str | None] | None = None,
    ask: Callable[[str], str] = input,
) -> int:
    import shutil

    args = _parse(argv)
    try:
        check_openjarvis_pin()
    except PinError as exc:
        print(f"jarvis_check: {exc}", file=sys.stderr)
        return 2
    from steel.home import steel_home
    from steel.jarvis.config import jarvis_config
    from steel.jarvis.keys import owns_terminal

    home = home or steel_home()
    config, setting_problems = jarvis_config()
    for problem in setting_problems:
        print(f"setting: {problem}", file=sys.stderr)
    missing = problems_before_start(
        home, foreground=foreground or owns_terminal, which=which or shutil.which
    )
    if missing:
        print("Not listening; nothing was opened:")
        for line in missing:
            print(f"  - {line}")
        return 1
    minutes = min(args.minutes, config.max_minutes)
    config = dataclasses.replace(config, max_minutes=minutes)
    print(
        f"STEEL jarvis check: the microphone is ALWAYS ON for up to {minutes} min. "
        "m = mute, q = quit; from another terminal: steel jarvis stop."
    )
    return _listen(home, config, ask)


def _listen(home: Path, config: Any, ask: Callable[[str], str]) -> int:
    """Everything that opens something; reached only after every check passed."""
    from openjarvis.core.types import ToolCall

    from steel.jarvis.indicators import GuardClient, Indicators
    from steel.jarvis.keys import KeyPoll, owns_terminal
    from steel.jarvis.limits import MESSAGES, Limits
    from steel.jarvis.procs import remove_pidfile, write_pidfile
    from steel.security import policy_or_deny_all
    from steel.security.executor import build_executor
    from steel.tools import get_wake_tool
    from steel.voice.config import voice_config

    measured: list[MeasuredEngine] = []

    def make_engine() -> MeasuredEngine:
        engine = load_engine(home)
        measured.append(engine)
        return engine

    guard = GuardClient()
    tool: Any = None
    wakes = false_triggers = 0
    listened = 0.0
    ended = "?"
    # Everything from the guard's start on is inside try: the pidfile and the
    # guard are cleaned up on every exit (normal end, error, Ctrl-C).
    try:
        guard.start()
        write_pidfile(home, os.getpid(), guard.pid)
        keys = KeyPoll()
        limits = Limits(config)
        tool = get_wake_tool(
            home,
            config=config,
            limits=limits,
            indicators=Indicators(guard),
            keys=keys,
            in_foreground=owns_terminal,
            make_engine=make_engine,
            device=voice_config().device,
        )
        policy, _problem = policy_or_deny_all(home)
        executor = build_executor([tool], policy=policy, agent_id=AGENT)
        while True:
            started = time.monotonic()
            with keys:
                result = executor.execute(
                    ToolCall(id=WAKE_TOOL, name=WAKE_TOOL, arguments=json.dumps({}))
                )
            listened += time.monotonic() - started
            outcome = result.metadata.get("outcome")
            if outcome == "woke":
                score = result.metadata.get("score")
                # The microphone is closed now; nothing after the wake is recorded.
                if _ask(
                    f"\nWake word heard (score {score}). Did you say it? [y/n] ", ask
                ):
                    wakes += 1
                else:
                    false_triggers += 1
                continue
            if outcome == "muted":
                if _ask("\nMuted, microphone closed. Listen again? [y/n] ", ask):
                    limits.unmuted()
                    continue
                ended = "muted, then stopped"
                break
            if isinstance(outcome, str) and outcome.startswith("limit:"):
                ended = MESSAGES.get(outcome[6:], outcome)
            else:
                ended = str(outcome or result.content)
            break
    except KeyboardInterrupt:
        if tool is not None:
            tool.cancel()
            tool.wait_idle(3.0)
        ended = "Ctrl-C"
    finally:
        if tool is not None:
            tool.wipe()
        guard.close()  # its input ends: it kills any microphone it has seen
        remove_pidfile(home)
    report(
        sys.stdout,
        listened=listened,
        wakes=wakes,
        false_triggers=false_triggers,
        engine=measured[0] if measured else None,
        ended=ended,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
