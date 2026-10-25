"""Switches that keep third-party libraries from phoning home.

onnxruntime 1.31 (used by faster-whisper's voice detection, and by Piper)
queues Microsoft telemetry in ~/.cache/Microsoft/DeveloperTools/.onnxruntime
and uploads it to mobile.events.data.microsoft.com, unless
ORT_DISABLE_TELEMETRY is set before it starts (docs/VOICE.md section 3). The
value is forced, not defaulted: STEEL never wants it on.
"""

from __future__ import annotations

import os

TELEMETRY_OFF = {"ORT_DISABLE_TELEMETRY": "1"}


def telemetry_off() -> None:
    """Set the switches in this process (and so in its children)."""
    os.environ.update(TELEMETRY_OFF)
