"""Keep a prompt inside the model's context window (docs/LATENCY.md section 9).

Ollama cuts a prompt that is longer than ``num_ctx`` and does not say so, and
the part it keeps is not always the part we would choose. So STEEL drops the
oldest conversation messages itself until the estimate fits.
"""

from __future__ import annotations

from collections.abc import Sequence

from openjarvis.core.types import Message, Role

# Tokens kept free for the reply (OpenJarvis asks for at most 1024).
REPLY_RESERVE_TOKENS = 1024
# An estimate, rounded up on purpose: English text is about 4 characters per
# token, other languages and code need more tokens, so 3 is the safer guess.
CHARS_PER_TOKEN = 3
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(messages: Sequence[Message]) -> int:
    chars = sum(len(m.content) for m in messages)
    return chars // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS * len(messages)


def fit_to_context(messages: list[Message], num_ctx: int) -> list[Message]:
    """Drop the oldest messages after the first (system) one until the prompt
    plus the reply reserve fits in *num_ctx*. The system message and the last
    (new) message always stay. Returns a new list."""
    budget = num_ctx - REPLY_RESERVE_TOKENS
    kept = list(messages)
    while len(kept) > 2 and estimate_tokens(kept) > budget:
        del kept[1]
        # Never start the conversation with an orphaned assistant reply.
        while len(kept) > 2 and kept[1].role is Role.ASSISTANT:
            del kept[1]
    return kept
