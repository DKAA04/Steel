"""Memory pressure for ``steel doctor``: read /proc/meminfo, change nothing.

The local models (chat, vision, speech) need several GiB of RAM. When the swap
is busy or little memory is free, a look or a transcription can take minutes
instead of seconds, so ``steel doctor`` says so in plain words. It only warns:
the doctor's exit status is not changed by it.
"""

from __future__ import annotations

from pathlib import Path

MEMINFO = Path("/proc/meminfo")
# Warn when more than this share of the swap is in use ...
SWAP_WARN_PERCENT = 50.0
# ... or when less than this much memory is available (MemAvailable).
AVAILABLE_WARN_KB = 2 * 1024 * 1024  # 2 GiB
_UNREADABLE = "memory: cannot be read from /proc/meminfo"


def read_meminfo(text: str) -> dict[str, int]:
    """The kB values of /proc/meminfo lines such as ``MemAvailable: 5092740 kB``."""
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            values[name.strip()] = int(fields[0])
    return values


def _gib(kb: int) -> str:
    return f"{kb / 1024 / 1024:.1f} GiB"


def memory_lines(values: dict[str, int]) -> list[str]:
    """One status line, plus a warning line if memory is tight."""
    needed = ("MemAvailable", "SwapTotal", "SwapFree")
    if any(name not in values for name in needed):
        return [_UNREADABLE]
    available, total, free = (values[name] for name in needed)
    if total > 0:
        used_percent = (total - free) / total * 100
        swap = (
            f"swap {used_percent:.0f}% in use ({_gib(total - free)} of {_gib(total)})"
        )
    else:
        used_percent, swap = 0.0, "no swap"
    lines = [f"memory: {_gib(available)} available, {swap}"]
    problems = []
    if available < AVAILABLE_WARN_KB:
        problems.append("less than 2 GiB of memory is available")
    if used_percent > SWAP_WARN_PERCENT:
        problems.append("the swap is more than half full")
    if problems:
        lines.append(
            "memory WARNING: "
            + " and ".join(problems)
            + ", so vision and voice will be slow (the models may be pushed out "
            "to swap). Close other programs, or unload a model with: ollama stop NAME"
        )
    return lines


def memory_report(path: Path | None = None) -> list[str]:
    """The doctor's memory lines. Reads one file; never fails."""
    try:
        text = (path or MEMINFO).read_text(encoding="ascii", errors="replace")
    except OSError:
        return [_UNREADABLE]
    return memory_lines(read_meminfo(text))
