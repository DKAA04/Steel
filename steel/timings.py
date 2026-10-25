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
    # Ollama's prompt_eval_count: tokens it really processed (fewer when it
    # reused a cached prefix), and the full prompt size (OpenJarvis: the larger
    # of Ollama's count and its own estimate, so approximate).
    prompt_evaluated: int | None = None
    prompt_total: int | None = None
    # Seconds this request waited for the start-up warm-up (None: it did not).
    queued: float | None = None

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
    # Text handed to the engine -> its first audio; None for a one-reply process.
    engine: float | None = None
    # "warm": the engine was running with its voice loaded when the text arrived;
    # "loading": it was started or restarted for this reply (or just before it).
    engine_state: str | None = None


def _seconds(timing: dict[str, Any], key: str) -> float | None:
    value = timing.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return value / _NS
    return None


def _count(value: Any) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value if ok else None


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
        tokens=_count(tokens),
        prompt_evaluated=_count(
            usage.get("prompt_tokens_evaluated") if isinstance(usage, dict) else None
        ),
        prompt_total=_count(
            usage.get("prompt_tokens") if isinstance(usage, dict) else None
        ),
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
    if model.prompt_evaluated is None or model.prompt_total is None:
        parts.append("prompt tokens n/a")
    else:
        # Ollama does not say how many tokens it reused, so that stays n/a.
        parts.append(
            f"prompt tokens {model.prompt_evaluated} evaluated of ~{model.prompt_total}"
            " (reused n/a)"
        )
    if model.queued is not None:
        parts.append(f"queued behind warm-up {_s(model.queued)}")
    if speech is not None:
        parts.append(f"first audio {_s(speech.first_audio)} after the reply")
        parts.append(f"played {_s(speech.played)}")
        if speech.engine is not None and speech.engine_state is not None:
            parts.append(f"engine: {speech.engine_state} ({_s(speech.engine)})")
    return " | ".join(parts)
