"""``steel chat --timings``: one line of stage times after each turn.

Numbers only: the line never contains the message or the reply, and nothing
here is logged or stored (docs/LATENCY.md section 2). The chat is not
streamed (section 4), so the time to the first token cannot be seen from
here; it is taken from Ollama's own durations: loading the model plus
reading the prompt is what comes before the first token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_NS = 1e9


@dataclass(frozen=True)
class ModelTiming:
    total: float  # wall time of the whole request, seen from STEEL
    load: float | None = None  # Ollama: loading the model (0 when warm)
    prompt: float | None = None  # Ollama: reading the prompt
    generate: float | None = None  # Ollama: writing the reply
    tokens: int | None = None  # tokens in the reply

    @property
    def first_token(self) -> float | None:
        if self.load is None or self.prompt is None:
            return None
        return self.load + self.prompt

    @property
    def tokens_per_second(self) -> float | None:
        if not self.tokens or not self.generate:
            return None
        return self.tokens / self.generate


@dataclass(frozen=True)
class SpeechTiming:
    first_audio: float | None  # reply handed to the speaker -> first audio
    played: float  # seconds of audio played


def _seconds(timing: dict[str, Any], key: str) -> float | None:
    value = timing.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return value / _NS
    return None


def model_timing(result: Any, total: float) -> ModelTiming:
    """Read Ollama's durations (nanoseconds) from an engine result, if any."""
    if not isinstance(result, dict):
        return ModelTiming(total=total)
    timing = result.get("engine_timing")
    timing = timing if isinstance(timing, dict) else {}
    usage = result.get("usage")
    tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    return ModelTiming(
        total=total,
        load=_seconds(timing, "load_duration"),
        prompt=_seconds(timing, "prompt_eval_duration"),
        generate=_seconds(timing, "eval_duration"),
        tokens=tokens if isinstance(tokens, int) and tokens >= 0 else None,
    )


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} s"


def timing_line(model: ModelTiming, speech: SpeechTiming | None = None) -> str:
    """The one line printed after a turn. Built from numbers only."""
    rate = model.tokens_per_second
    loading = f"load {_s(model.load)}, prompt {_s(model.prompt)}"
    parts = [
        f"timings: model {_s(model.total)}",
        f"first token {_s(model.first_token)} ({loading})",
        f"generation {_s(model.generate)}",
        "n/a tokens/s" if rate is None else f"{rate:.1f} tokens/s",
    ]
    if speech is not None:
        parts.append(f"first audio {_s(speech.first_audio)} after the reply")
        parts.append(f"played {_s(speech.played)}")
    return " | ".join(parts)
