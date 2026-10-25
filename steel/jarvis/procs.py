"""Process ids, the pidfile and ``steel jarvis stop`` (docs/JARVIS.md 3c).

Everything here works from /proc and signals, never from the loop's goodwill,
so it still works when the loop is stuck. A process id is only ever killed if
its start time (``/proc/PID/stat`` field 22) is the one recorded, so a number
the kernel has since given to another program is left alone.
"""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from steel.home import JARVIS_PIDFILE

PROC = Path("/proc")
MIC_PROGRAM = "arecord"  # the comm name of every microphone process STEEL starts


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    comm: str
    state: str
    ppid: int
    start: int  # clock ticks after boot; with the pid it names one process


def proc_info(pid: int, proc: Path = PROC) -> ProcInfo | None:
    """The process, or None if it is gone (or a zombie)."""
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    head, _, rest = text.rpartition(")")  # comm may contain spaces and ")"
    comm = head.partition("(")[2]
    fields = rest.split()
    if len(fields) < 20 or fields[0] == "Z":
        return None
    return ProcInfo(pid, comm, fields[0], int(fields[1]), int(fields[19]))


def all_pids(proc: Path = PROC) -> Iterable[int]:
    for entry in proc.iterdir():
        if entry.name.isdigit():
            yield int(entry.name)


def mic_children(parent: int, proc: Path = PROC) -> list[ProcInfo]:
    """The microphone processes (arecord) whose parent is *parent*."""
    found = []
    for pid in all_pids(proc):
        info = proc_info(pid, proc)
        if info and info.ppid == parent and info.comm == MIC_PROGRAM:
            found.append(info)
    return found


def kill_exact(info: ProcInfo, proc: Path = PROC) -> bool:
    """SIGKILL *info* if that very process (pid and start time) still runs."""
    now = proc_info(info.pid, proc)
    if now is None or now.start != info.start:
        return False
    try:
        os.kill(info.pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    return True


# --- the pidfile -------------------------------------------------------------


def pidfile_path(home: Path) -> Path:
    return home / JARVIS_PIDFILE


def write_pidfile(home: Path, loop: int, guard: int | None) -> None:
    """Record the loop and its guard (mode 600, never through a symlink)."""
    entries = {}
    for name, pid in (("loop", loop), ("guard", guard)):
        info = proc_info(pid) if pid is not None else None
        if info is not None:
            entries[name] = {"pid": info.pid, "start": info.start}
    fd = os.open(
        pidfile_path(home),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="ascii") as out:
        json.dump(entries, out)


def read_pidfile(home: Path) -> dict[str, ProcInfo]:
    """The recorded processes that still run as recorded; {} if none."""
    try:
        data = json.loads(pidfile_path(home).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return {}
    found = {}
    if not isinstance(data, dict):
        return {}
    for name in ("loop", "guard"):
        entry = data.get(name)
        if not isinstance(entry, dict):
            continue
        pid, start = entry.get("pid"), entry.get("start")
        if not isinstance(pid, int) or not isinstance(start, int):
            continue
        info = proc_info(pid)
        if info is not None and info.start == start:
            found[name] = info
    return found


def remove_pidfile(home: Path) -> None:
    try:
        os.unlink(pidfile_path(home))
    except FileNotFoundError:
        pass


FALLBACK = (
    "If a microphone is still on: pkill -KILL -f 'steel jarvis' and then "
    "pkill -KILL -x arecord (the second ends EVERY arecord of this user)."
)


def stop(home: Path) -> list[str]:
    """``steel jarvis stop``: SIGKILL the loop's microphones, the loop and its
    guard. Needs nothing from the loop itself. Returns what it did."""
    running = read_pidfile(home)
    lines: list[str] = []
    loop = running.get("loop")
    if loop is not None:
        # The microphones first: they are what must not stay open.
        for mic in mic_children(loop.pid):
            if kill_exact(mic):
                lines.append(f"killed microphone process {mic.pid}")
        if kill_exact(loop):
            lines.append(f"killed the Jarvis loop {loop.pid}")
    guard = running.get("guard")
    if guard is not None and kill_exact(guard):
        lines.append(f"killed the indicator and watchdog {guard.pid}")
    remove_pidfile(home)
    if not lines:
        lines.append("No running Jarvis loop was recorded; nothing was killed.")
    lines.append(FALLBACK)
    return lines
