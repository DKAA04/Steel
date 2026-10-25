"""Which voice should read a reply: English, Spanish or Italian (docs/SPEAK.md 5).

A few dozen common words per language and the marks only one of them uses. A
wrong guess only means the wrong accent, so anything unclear is English; no
detection package is added for this.
"""

from __future__ import annotations

import re

# Words that are common in one language and not words of the other two. Words
# Spanish and Italian share ("la", "un", "una", "con", "se", "lo", "del") count
# for neither.
_WORDS = {
    "en": frozenset(
        [
            "the",
            "and",
            "is",
            "are",
            "you",
            "your",
            "to",
            "of",
            "it",
            "that",
            "this",
            "with",
            "for",
            "have",
            "has",
            "not",
            "what",
            "was",
            "were",
            "be",
            "at",
            "on",
            "so",
            "can",
            "will",
            "there",
            "my",
            "me",
            "an",
            "do",
            "does",
            "from",
            "they",
            "we",
            "would",
            "could",
            "should",
            "about",
            "which",
        ]
    ),
    "es": frozenset(
        [
            "el",
            "los",
            "las",
            "que",
            "y",
            "es",
            "está",
            "están",
            "estás",
            "estoy",
            "en",
            "por",
            "para",
            "pero",
            "muy",
            "también",
            "como",
            "cómo",
            "qué",
            "cuando",
            "dónde",
            "hoy",
            "mañana",
            "su",
            "sus",
            "yo",
            "tú",
            "usted",
            "hay",
            "eso",
            "esto",
            "esta",
            "este",
            "así",
            "tiene",
            "son",
            "ser",
            "hola",
            "gracias",
            "puedo",
            "puedes",
        ]
    ),
    "it": frozenset(
        [
            "il",
            "gli",
            "della",
            "delle",
            "dei",
            "degli",
            "che",
            "e",
            "è",
            "non",
            "sono",
            "sei",
            "per",
            "ma",
            "anche",
            "molto",
            "questo",
            "questa",
            "quello",
            "perché",
            "ciao",
            "sì",
            "ho",
            "hai",
            "alle",
            "alla",
            "allo",
            "nel",
            "nella",
            "sul",
            "sulla",
            "domani",
            "oggi",
            "quindi",
            "credo",
            "sia",
            "come",
            "cosa",
            "più",
            "grazie",
            "posso",
            "puoi",
        ]
    ),
}
# Spanish-only marks, and Italian elisions (l'ombrello, c'è, un'altra).
_SPANISH_MARKS = re.compile(r"[ñ¿¡áíóú]")
_ITALIAN_MARKS = re.compile(r"[àèìòù]|\b(?:l|c|un|dell|all|nell|sull|quest|d)'\w")
_WORD = re.compile(r"[^\W\d_]+")


def pick_language(text: str) -> str:
    """'es' or 'it' only with a clear lead; everything else is 'en'."""
    lower = text.lower()
    words = _WORD.findall(lower)
    score = {
        lang: sum(word in vocabulary for word in words)
        for lang, vocabulary in _WORDS.items()
    }
    score["es"] += len(_SPANISH_MARKS.findall(lower))
    score["it"] += len(_ITALIAN_MARKS.findall(lower))
    for lang, other in (("es", "it"), ("it", "es")):
        if (
            score[lang] >= 2
            and score[lang] >= 2 * score[other]
            and score[lang] > score["en"]
        ):
            return lang
    return "en"
