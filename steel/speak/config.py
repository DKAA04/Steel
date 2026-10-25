"""Settings for spoken replies (docs/SPEAK.md section 6)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 600
MIN_MAX_CHARS, MAX_MAX_CHARS = 50, 4000
MAX_CHARS_ENV = "STEEL_SPEAK_MAX_CHARS"
# A hard stop for one reply, whatever the text: a stuck engine must not talk on.
MAX_AUDIO_SECONDS = 120.0
# ``steel speak say`` is a test of one short sentence.
SAY_MAX_CHARS = 200


@dataclass(frozen=True)
class SpeakConfig:
    max_chars: int = DEFAULT_MAX_CHARS
    max_audio_seconds: float = MAX_AUDIO_SECONDS


def speak_config(env: Mapping[str, str] | None = None) -> SpeakConfig:
    """$STEEL_SPEAK_MAX_CHARS, a whole number from 50 to 4000; else 600.

    A typo must not lift the limit, so anything else falls back to the default.
    """
    env = os.environ if env is None else env
    raw = env.get(MAX_CHARS_ENV, "").strip()
    if raw.isdigit() and MIN_MAX_CHARS <= int(raw) <= MAX_MAX_CHARS:
        return SpeakConfig(max_chars=int(raw))
    return SpeakConfig()
