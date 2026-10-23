"""Detect facts that are only true for a short time (English and Spanish).

"Mara is visiting next weekend" is useless in two weeks. Such facts are sent
to the expiring notes store instead of the durable facts file. Detection is a
plain list of patterns, matched on lower-case text with accents removed
("mañana" is matched as "manana").

Deliberately NOT matched, because the fact would be lost after the 7-day
expiry: month- or year-scale words ("next month", "next year", "el año que
viene"). Habits ("every morning", "por la mañana", "hoy en día") are not
time-bound either.
"""

from __future__ import annotations

import re
import unicodedata

WEEKDAYS_EN = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
WEEKDAYS_ES = "lunes|martes|miercoles|jueves|viernes|sabado|domingo"
NUMBER_EN = r"\d+|a few|a couple of|one|two|three|four|five|six|seven"
NUMBER_ES = r"\d+|unos|unas|pocos|pocas|un|una|dos|tres|cuatro|cinco|seis|siete"

# Each pattern is searched in the normalised text.
_PATTERNS = [
    # --- English ---
    r"\btomorrow\b",
    r"\btonight\b",
    r"\btoday\b",
    r"\byesterday\b",
    r"\blast night\b",
    rf"\b(this|next|last|this coming) (week|weekend|morning|afternoon|evening|{WEEKDAYS_EN})\b",
    rf"\bin ({NUMBER_EN}) (days?|hours?)\b",
    # --- Spanish ---
    r"\bhoy\b(?! en dia)",  # not "hoy en dia" (nowadays)
    r"(?<!\bla )(?<!\bcada )(?<!\bde )\bmanana\b",  # not "por la mañana"/"cada mañana"
    r"\b(ayer|anoche|anteayer)\b",
    r"\bpasado manana\b",
    rf"\b(esta|este) (manana|tarde|noche|semana|fin de semana|{WEEKDAYS_ES})\b",
    r"\b(proxima|proximo|pasada|pasado) (semana|fin de semana|noche)\b",
    rf"\b(semana|fin de semana|{WEEKDAYS_ES}) que viene\b",
    rf"\b(dentro de|en) ({NUMBER_ES}) (dias?|horas?)\b",
]
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))


def _plain(text: str) -> str:
    """Lower-case text without accents (so "mañana" -> "manana")."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def is_time_bound(text: str) -> bool:
    """True if *text* contains a short-term time expression."""
    return _REGEX.search(_plain(text)) is not None
