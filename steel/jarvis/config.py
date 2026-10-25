"""Jarvis loop settings from environment variables, with safe fallbacks.

A typo must never make the microphone stay open longer or trigger more easily,
so every value is validated; anything else falls back to the default and is
reported once at start (docs/JARVIS.md sections 4, 6 and 11).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

STT_MODELS = ("base", "small")


@dataclass(frozen=True)
class JarvisConfig:
    max_minutes: int = 60  # the whole session; the microphone closes after it
    idle_minutes: int = 20  # without a wake word
    max_wakes_per_hour: int = 30  # more ends the session: a noisy room or a TV
    threshold: float = 0.5  # wake-word score that counts as a detection
    confirm_seconds: int = 3  # countdown before a transcript is sent; 0 = at once
    stt_model: str = "base"  # Whisper model for the request


# name -> (field, low, high, whole number?)
_SETTINGS: dict[str, tuple[str, float, float, bool]] = {
    "STEEL_JARVIS_MAX_MINUTES": ("max_minutes", 1, 240, True),
    "STEEL_JARVIS_IDLE_MINUTES": ("idle_minutes", 1, 240, True),
    "STEEL_JARVIS_MAX_WAKES_PER_HOUR": ("max_wakes_per_hour", 1, 120, True),
    "STEEL_JARVIS_THRESHOLD": ("threshold", 0.3, 0.95, False),
    "STEEL_JARVIS_CONFIRM_SECONDS": ("confirm_seconds", 0, 10, True),
}
STT_ENV = "STEEL_JARVIS_STT_MODEL"


def _parse(raw: str, low: float, high: float, whole: bool) -> float | None:
    raw = raw.strip()
    if whole:
        return float(raw) if raw.isdigit() and low <= int(raw) <= high else None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if low <= value <= high else None  # also rejects nan and inf


def jarvis_config(
    env: Mapping[str, str] | None = None,
) -> tuple[JarvisConfig, list[str]]:
    """The settings and one line per setting that was set but not usable."""
    env = os.environ if env is None else env
    base = JarvisConfig()
    values: dict[str, object] = {}
    problems: list[str] = []
    for name, (field, low, high, whole) in _SETTINGS.items():
        raw = env.get(name, "")
        if not raw.strip():
            continue
        value = _parse(raw, low, high, whole)
        default = getattr(base, field)
        if value is None:
            problems.append(
                f"{name}={raw.strip()!r} is not {'a whole number' if whole else 'a number'}"
                f" from {low:g} to {high:g}; {default:g} is used."
            )
        else:
            values[field] = int(value) if whole else value
    model = env.get(STT_ENV, "").strip()
    if model:
        if model in STT_MODELS:
            values["stt_model"] = model
        else:
            problems.append(f"{STT_ENV}={model!r} is not base or small; base is used.")
    return JarvisConfig(**values), problems  # type: ignore[arg-type]
