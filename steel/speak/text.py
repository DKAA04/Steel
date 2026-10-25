"""What may be spoken aloud, and in what form (docs/SPEAK.md section 6).

A spoken reply can be overheard, so: only the assistant's own reply, never a
tool-data block or camera text; nothing the injection scanner flags or cannot
check; secrets and personal data redacted with the same scanners as tool
results; markdown, links and code removed (they make no sense read aloud);
and a length cap, after which the listener is told the rest is on screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from steel import tooldata
from steel.speak.language import pick_language

# Never spoken: the wrapper of an added tool result, and the camera's line.
_MARKERS = (tooldata.HEADER_START, tooldata.END_LINE, "[Camera description")

REST_ON_SCREEN = {
    "en": "The rest is on screen.",
    "es": "El resto está en pantalla.",
    "it": "Il resto è sullo schermo.",
}
_CODE_ON_SCREEN = {
    "en": "There is code on screen.",
    "es": "Hay código en pantalla.",
    "it": "C'è del codice sullo schermo.",
}
_LINK_ON_SCREEN = {
    "en": "a link on screen",
    "es": "un enlace en pantalla",
    "it": "un link sullo schermo",
}
_REDACTED_WORD = {"en": "redacted", "es": "censurado", "it": "oscurato"}

_CODE_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)  # an unclosed one too
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_REDACTED = re.compile(r"\[REDACTED:[^\]]*\]")
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$|^\s*\|?\s*:?-{2,}.*$", re.MULTILINE)
_LINE_MARK = re.compile(r"^\s{0,3}(?:#{1,6}\s*|>\s?|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_STRONG = re.compile(r"(\*\*|__)(.+?)\1")
_EMPHASIS = re.compile(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)")
_LEFTOVER = re.compile(r"[*#`|_~]")
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


@dataclass(frozen=True)
class Speech:
    text: str  # what will be said; empty if refused
    language: str
    cut: bool = False
    refused: str | None = None  # "tool data", "flagged", "unredacted", "empty"


def _redact(text: str) -> str:
    return tooldata._redact(text)


def strip_for_speech(text: str, language: str) -> str:
    """Markdown, links and code out; one line of plain words."""
    text = tooldata.clean_text(text)
    code = f" {_CODE_ON_SCREEN[language]} "
    text = _CODE_BLOCK.sub(code, text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(_LINK_ON_SCREEN[language], text)
    text = _REDACTED.sub(_REDACTED_WORD[language], text)
    text = _RULE.sub(" ", text)
    text = _LINE_MARK.sub("", text)
    text = _STRONG.sub(r"\2", text)
    text = _EMPHASIS.sub(r"\1", text)
    text = _LEFTOVER.sub(" ", text)
    return " ".join(text.split())


def _cap(text: str, max_chars: int, language: str) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    window = text[:max_chars]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    # A sentence end, unless it would throw most of the window away.
    if ends and ends[-1] >= max_chars * 0.4:
        body = window[: ends[-1]]
    else:
        body = window.rsplit(" ", 1)[0] if " " in window else window
    return f"{body.rstrip()} {REST_ON_SCREEN[language]}", True


def prepare_speech(reply: str, *, max_chars: int) -> Speech:
    """The words to speak for *reply*, or a refusal (never the reason's text)."""
    if any(marker in reply for marker in _MARKERS):
        return Speech("", "en", refused="tool data")
    text = tooldata.clean_text(reply)
    try:
        clean = tooldata._scan_is_clean(text)
    except Exception:  # noqa: BLE001 - if it cannot be checked, it is not spoken
        clean = False
    if not clean:
        return Speech("", "en", refused="flagged")
    try:
        text = _redact(text)
    except Exception:  # noqa: BLE001 - never speak unredacted text
        return Speech("", "en", refused="unredacted")
    language = pick_language(_URL.sub(" ", _CODE_BLOCK.sub(" ", text)))
    spoken = strip_for_speech(text, language)
    if not any(ch.isalnum() for ch in spoken):
        return Speech("", language, refused="empty")
    spoken, cut = _cap(spoken, max_chars, language)
    return Speech(spoken, language, cut=cut)
