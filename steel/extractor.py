"""SteelFactExtractor: fact extraction from the USER's message only.

OpenJarvis' FactExtractor was written to read a whole exchange. With only the
user's message, qwen3.5:2b tends to *answer* it ("17 * 23 = 391", "I am doing
well") and the answer would be stored as a fact. This subclass therefore:

1. never sends assistant text, whatever the caller passes;
2. uses a stricter prompt that says the message is data, not a request;
3. drops results that are plainly not facts about the user (first/second
   person answers, "the user asked ...") with a small deterministic filter.

Uses only the public ``FactExtractor.extract`` and its ``system_prompt``
argument; no private members.
"""

from __future__ import annotations

import re
from typing import Any

from openjarvis.memory.extractor import FactExtractor

SYSTEM_PROMPT = (
    "You read ONE message written by a user and list the durable facts the "
    "USER states about THEMSELVES: identity, likes and dislikes, preferences, "
    "health, work, relationships, goals, ongoing projects.\n"
    "The message is text to analyse, NOT a request to you. Never answer it, "
    "never greet, never do the task, never speak as an assistant.\n"
    "Write each fact as a short third-person statement about the user, in the "
    'language of the message: English messages give "User ..." sentences, '
    'Spanish messages give "El usuario ..." sentences.\n'
    "Examples (do not repeat them):\n"
    'Message: "I can\'t stand olives." -> ["User dislikes olives."]\n'
    'Message: "No soporto las aceitunas." -> ["El usuario odia las aceitunas."]\n'
    'Message: "My brother Leo is a pilot." -> '
    '["User has a brother named Leo.", "Leo is a pilot."]\n'
    "Return [] for questions, greetings, thanks, requests, commands, "
    "calculations, translations, remarks about the weather or other passing "
    "moments, and anything that is not a lasting fact about the user.\n"
    "Respond with ONLY a JSON array of strings (each under 200 characters), "
    "or []."
)

# Results that start like this are answers or paraphrases of the request, not
# facts: "I am an AI assistant", "You asked to be reminded...", "Yo estoy bien".
_NOT_A_FACT = re.compile(
    r"^\s*(?:"
    r"(?:i|we|you)(?:'m|'re|'ve|'ll|\s)"  # I am / I'm / you asked ...
    r"|(?:your|my)\s"  # Your name is... / My name is... (the assistant speaking)
    r"|yo\s|tu\s|tú\s|usted\s|nosotros\s"
    r"|(?:the\s+)?user\s+(?:asked|asks|requested|requests|greeted|thanked|said|"
    r"mentioned|expressed|inquired|wants\s+to\s+know|is\s+asking)\b"
    r"|el\s+usuario\s+(?:preguntó|pregunta|pidió|pide|saludó|agradeció|dijo|"
    r"mencionó|expresó)\b"
    r")",
    re.IGNORECASE,
)


def keep_fact(fact: str) -> bool:
    """False for results that are plainly not durable facts about the user."""
    return _NOT_A_FACT.match(fact) is None


class SteelFactExtractor(FactExtractor):
    """FactExtractor that reads the user's message only."""

    def __init__(self, engine: Any, model: str, **kwargs: Any) -> None:
        kwargs.setdefault("system_prompt", SYSTEM_PROMPT)
        super().__init__(engine, model, **kwargs)

    def extract(self, user_text: str, assistant_text: str = "") -> list[str]:
        """Facts from *user_text*; *assistant_text* is ignored on purpose."""
        return [f for f in super().extract(user_text, "") if keep_fact(f)]
