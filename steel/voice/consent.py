"""Voice consent: one small file with a timestamp, which the owner can delete.

No consent file means no microphone. The checks on the file (a regular file, ours,
not writable by others, one timestamp and nothing else) are in steel/consent.py,
shared with the camera. It holds no text, no audio, no names.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from steel.consent import delete_stamp, read_stamp, write_stamp
from steel.home import VOICE_CONSENT_FILE

# docs/VOICE.md section 6.4 quotes this word for word; a test compares them.
CONSENT_TEXT = """STEEL voice input: please read

STEEL can turn your speech into text, using the microphone of this computer.

- The microphone opens only after you press a key, and only for one recording at a time. It closes when you press the key again, when the maximum length is reached (30 seconds), or after a pause in your speech.
- While it is open, STEEL prints a "● REC" line with the seconds elapsed. If that line cannot be printed, the microphone is not opened.
- The sound is kept in memory only. It is turned into text on this computer and then thrown away. No audio file is written, and nothing is sent over the network.
- You see the text first. It is used (sent to the model, and searched for facts about you) only when you confirm it. If you discard it, it is thrown away.
- The microphone hears everyone who is speaking near it, not only you. Do not use it when other people might be heard without knowing.
- This permission is stored as a date and time in a file in your OpenJarvis folder. You can withdraw it at any time with: steel voice revoke
- You must also grant the microphone tool by hand in steel_policy.json. This consent does not do that.

Type yes to agree."""


def consent_path(home: Path) -> Path:
    return home / VOICE_CONSENT_FILE


def read_consent(home: Path) -> str | None:
    """The timestamp in a well-formed consent file that is ours and private, else None."""
    return read_stamp(consent_path(home))


def has_consent(home: Path) -> bool:
    return read_consent(home) is not None


def record_consent(home: Path, *, now: datetime | None = None) -> str:
    """Write the consent file (mode 600) and return the timestamp it holds."""
    return write_stamp(consent_path(home), now=now)


def revoke_consent(home: Path) -> bool:
    """Delete the consent file; return whether there was one."""
    return delete_stamp(consent_path(home))
