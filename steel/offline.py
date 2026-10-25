"""Switches that keep third-party libraries from phoning home.

onnxruntime 1.31 (used by faster-whisper's voice detection, and by Piper)
queues Microsoft telemetry in ~/.cache/Microsoft/DeveloperTools/.onnxruntime
and uploads it to mobile.events.data.microsoft.com, unless
ORT_DISABLE_TELEMETRY is set before it starts (docs/VOICE.md section 3). The
value is forced, not defaulted: STEEL never wants it on.
"""

from __future__ import annotations

import os
from pathlib import Path

TELEMETRY_OFF = {"ORT_DISABLE_TELEMETRY": "1"}
# Where onnxruntime keeps its device id and queued events (relative to $HOME).
ONNXRUNTIME_QUEUE = Path(".cache") / "Microsoft" / "DeveloperTools" / ".onnxruntime"


def telemetry_off() -> None:
    """Set the switches in this process (and so in its children)."""
    os.environ.update(TELEMETRY_OFF)


def telemetry_queue_lines(home: Path | None = None) -> list[str]:
    """``steel doctor``: a warning if onnxruntime's telemetry queue exists.

    Read only. Its presence means onnxruntime ran once without the switch
    (by STEEL before the fix, by another program, or by hand); the queued events
    may be uploaded the next time that happens. STEEL does not delete it.
    """
    queue = (home or Path.home()) / ONNXRUNTIME_QUEUE
    if not os.path.lexists(queue):
        return ["onnxruntime telemetry queue: none"]
    return [
        (
            f"onnxruntime telemetry queue: WARNING, {queue} exists (a device id "
            "and queued usage events that onnxruntime may upload to Microsoft). "
            "Delete it with:"
        ),
        f"  rm -r {queue}",
    ]
