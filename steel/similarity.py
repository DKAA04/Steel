"""Deterministic text matching for facts: near-duplicates and related facts.

No embeddings and no model calls. Two facts are compared like this:

1. Normalise: lower-case, strip accents (so "Málaga" == "malaga"), drop
   punctuation and possessive 's, split into words.
2. Remove filler words (English and Spanish) and the words "user"/"usuario",
   then lightly stem plurals ("cats" -> "cat").
3. Compare the remaining content words as sets. Negations ("not", "no",
   "nunca"...) and numbers must match exactly.

Everything you may want to tune is in the three constants below.
"""

from __future__ import annotations

import re
import unicodedata

# A new fact is a near-duplicate of an old one when this share of the NEW
# fact's content words already appear in the old fact (the new fact says
# nothing more). 0.8 = four out of five. A new fact that adds words is kept.
NEAR_DUPLICATE_CONTAINMENT = 0.8
# New facts with fewer content words than this are only duplicates if
# identical after normalisation (too little text to compare safely).
MIN_CONTENT_WORDS = 2
# "Related" is looser, for `steel forget`: at least one shared content word
# and this share of the shorter fact's content words. Related facts are only
# listed for the user to review; they are removed only with --with-related.
RELATED_SHARED_FRACTION = 0.3

# Words that carry no fact content. Negations are NOT in this list.
_STOP_WORDS = frozenset(
    """
    a an the and or of to in on at for with by from as is are was were be been
    am it its that this these those he she they his her their i me my we our
    you your user users do does did have has had
    el la los las un una unos unas de del al y o que con por para es son ser
    esta este esto se su sus mi mis lo le les como ha han tiene tienen tengo
    usuario usuaria
    """.split()  # noqa: SIM905 - a wrapped word list is easier to edit
)
# Negations and numbers must match exactly, or "likes tea" would be treated as
# a duplicate of "does not like tea", and "2 cats" of "3 cats".
_NEGATIONS = frozenset(
    [
        "not",
        "no",
        "never",
        "nor",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "arent",
        "cant",
        "cannot",
        "wont",
        "nunca",
        "jamas",
        "ni",
        "tampoco",
        "sin",
    ]
)


def _words(text: str) -> list[str]:
    """Lower-case, accent-free words of *text*, possessive 's removed."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = re.sub(r"['’]s\b", "", plain)  # "user's" -> "user"
    return re.findall(r"[a-z0-9ñ]+", plain.replace("'", "").replace("’", ""))


def _stem(word: str) -> str:
    """Strip a plural 's' ("cats" -> "cat", "likes" -> "like")."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


class Signature:
    """The comparable parts of one fact."""

    def __init__(self, text: str) -> None:
        words = _words(text)
        self.negations = frozenset(w for w in words if w in _NEGATIONS)
        self.numbers = frozenset(w for w in words if w.isdigit())
        self.content = frozenset(
            _stem(w) for w in words if w not in _STOP_WORDS and w not in _NEGATIONS
        )


def _fraction_of(a: Signature, b: Signature) -> float:
    """Share of a's content words that also appear in b."""
    return len(a.content & b.content) / len(a.content) if a.content else 0.0


def is_near_duplicate(new: str, old: str) -> bool:
    """True if *new* adds nothing over *old* (see module docstring)."""
    a, b = Signature(new), Signature(old)
    if a.negations != b.negations or a.numbers != b.numbers:
        return False
    if a.content == b.content:
        return bool(a.content)
    if len(a.content) < MIN_CONTENT_WORDS:
        return False
    return _fraction_of(a, b) >= NEAR_DUPLICATE_CONTAINMENT


def is_related(one: str, other: str) -> bool:
    """True if the facts share enough words to be worth reviewing together."""
    a, b = Signature(one), Signature(other)
    shared = a.content & b.content
    smaller = min(len(a.content), len(b.content))
    return bool(shared) and len(shared) / smaller >= RELATED_SHARED_FRACTION
