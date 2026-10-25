"""Voice settings from environment variables, with safe fallbacks.

A typo must never make the microphone stay open longer or pick a path or flag
the owner did not mean, so every value is validated and falls back to its
default instead of failing or being used as given.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_SECONDS = 30.0
MAX_SECONDS_CAP = 120.0
DEFAULT_LANGUAGES = ("en", "es", "it")
DEFAULT_MODEL = "small"
# Words Whisper should expect (its initial prompt), so "STEEL" is not heard as
# "Steele". The owner adds names in $STEEL_VOICE_HINT; none is written in the code.
DEFAULT_HINT = "STEEL"
MAX_HINT_CHARS = 200

_MODEL_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
_LANGUAGE_RE = re.compile(r"[a-z]{2,3}")
# An ALSA device name. Starting with a letter or digit keeps it from being read
# as an option by arecord.
_DEVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:.,=-]{0,63}")


@dataclass(frozen=True)
class VoiceConfig:
    max_seconds: float = DEFAULT_MAX_SECONDS  # hard limit of one recording
    silence_seconds: float = 1.5  # quiet after speech that ends the recording
    startup_silence_seconds: float = 5.0  # give up if nobody speaks for this long
    silence_rms: int = 500  # 16-bit RMS below this counts as quiet
    start_wait_seconds: float = 60.0  # how long to wait for the start key
    model: str = DEFAULT_MODEL
    # () = detect among all languages, one code = forced, several = detect among those.
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    device: str = "default"  # ALSA device; "default" is PipeWire on this machine
    hint: str = DEFAULT_HINT  # Whisper's initial prompt (vocabulary, not orders)


def _number(
    env: Mapping[str, str], name: str, default: float, *, low: float, high: float
) -> float:
    try:
        value = float(env.get(name, "").strip())
    except ValueError:
        return default
    if not low < value < float("inf"):  # also rejects nan and inf
        return default
    return min(value, high)


def _hint(raw: str) -> str:
    """The vocabulary hint: 1-200 printable characters, always with "STEEL"."""
    raw = raw.strip()
    if not 0 < len(raw) <= MAX_HINT_CHARS or any(
        unicodedata.category(ch) in ("Cc", "Cf") for ch in raw
    ):
        return DEFAULT_HINT
    return raw if DEFAULT_HINT in raw else f"{DEFAULT_HINT}, {raw}"


def voice_config(env: Mapping[str, str] | None = None) -> VoiceConfig:
    """Settings from $STEEL_VOICE_* (see docs/VOICE.md section 6.2)."""
    env = os.environ if env is None else env
    base = VoiceConfig()
    model = env.get("STEEL_VOICE_MODEL", "").strip()
    device = env.get("STEEL_VOICE_DEVICE", "").strip()
    raw_languages = env.get("STEEL_VOICE_LANGUAGE", "").strip().lower()
    languages = base.languages
    if raw_languages == "auto":
        languages = ()
    elif raw_languages:
        codes = tuple(c.strip() for c in raw_languages.split(","))
        if all(_LANGUAGE_RE.fullmatch(c) for c in codes):
            languages = codes
    return VoiceConfig(
        silence_rms=int(
            _number(env, "STEEL_VOICE_SILENCE_RMS", base.silence_rms, low=0, high=10000)
        ),
        max_seconds=_number(
            env,
            "STEEL_VOICE_MAX_SECONDS",
            base.max_seconds,
            low=0,
            high=MAX_SECONDS_CAP,
        ),
        silence_seconds=_number(
            env, "STEEL_VOICE_SILENCE_SECONDS", base.silence_seconds, low=0, high=10
        ),
        model=model if _MODEL_RE.fullmatch(model) else base.model,
        languages=languages,
        device=device if _DEVICE_RE.fullmatch(device) else base.device,
        hint=_hint(env.get("STEEL_VOICE_HINT", "")),
    )
