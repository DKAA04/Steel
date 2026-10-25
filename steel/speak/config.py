"""Settings for spoken replies (docs/SPEAK.md section 6)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 600
MIN_MAX_CHARS, MAX_MAX_CHARS = 50, 4000
MAX_CHARS_ENV = "STEEL_SPEAK_MAX_CHARS"
IDLE_END_ENV = "STEEL_SPEAK_IDLE_END_SECONDS"
MIN_IDLE_END, MAX_IDLE_END = 0.5, 5.0
DEFAULT_IDLE_END = 1.5
PERSISTENT_ENV = "STEEL_SPEAK_PERSISTENT"
# A hard stop for one reply, whatever the text: a stuck engine must not talk on.
MAX_AUDIO_SECONDS = 120.0
# ``steel speak say`` is a test of one short sentence.
SAY_MAX_CHARS = 200


@dataclass(frozen=True)
class SpeakConfig:
    max_chars: int = DEFAULT_MAX_CHARS
    max_audio_seconds: float = MAX_AUDIO_SECONDS
    # Silence after a reply's last audio that ends the reply (long-lived engine).
    idle_end_seconds: float = DEFAULT_IDLE_END
    # False ($STEEL_SPEAK_PERSISTENT=0): one engine process per reply, as in 5a.
    persistent: bool = True


def speak_config(env: Mapping[str, str] | None = None) -> SpeakConfig:
    """$STEEL_SPEAK_MAX_CHARS (whole number, 50 to 4000; else 600),
    $STEEL_SPEAK_IDLE_END_SECONDS (0.5 to 5; else 1.5) and
    $STEEL_SPEAK_PERSISTENT (0 turns the long-lived engine off).

    A typo must not lift a limit, so anything else falls back to the default.
    """
    env = os.environ if env is None else env
    max_chars = DEFAULT_MAX_CHARS
    raw = env.get(MAX_CHARS_ENV, "").strip()
    if raw.isdigit() and MIN_MAX_CHARS <= int(raw) <= MAX_MAX_CHARS:
        max_chars = int(raw)
    return SpeakConfig(
        max_chars=max_chars,
        idle_end_seconds=_idle_end(env.get(IDLE_END_ENV, "")),
        persistent=env.get(PERSISTENT_ENV, "").strip() != "0",
    )


def _idle_end(raw: str) -> float:
    raw = raw.strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_IDLE_END
    # float() accepts "nan" and "inf": the range test rejects both.
    return value if MIN_IDLE_END <= value <= MAX_IDLE_END else DEFAULT_IDLE_END
