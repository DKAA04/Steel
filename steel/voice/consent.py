"""Voice consent: one small file with a timestamp, which the owner can delete.

No consent file means no microphone. The file is trusted only if it is a
regular file (not a symlink), belongs to this user and is not writable by
others, so nobody else can grant consent on the owner's behalf. It holds a
time and nothing else: no text, no audio, no names.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

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
    path = consent_path(home)
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):  # a symlink is not followed
            return None
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"consented_at"}:
            return None
        stamp = data["consented_at"]
        datetime.fromisoformat(stamp)  # a bad value raises
        return str(stamp)
    except (OSError, ValueError, TypeError):
        return None  # unreadable or malformed: no consent


def has_consent(home: Path) -> bool:
    return read_consent(home) is not None


def record_consent(home: Path, *, now: datetime | None = None) -> str:
    """Write the consent file (mode 600) and return the timestamp it holds."""
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    # O_NOFOLLOW: a symlink planted at this name is refused, not written through.
    fd = os.open(
        consent_path(home),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps({"consented_at": stamp}).encode("ascii"))
    finally:
        os.close(fd)
    return stamp


def revoke_consent(home: Path) -> bool:
    """Delete the consent file; return whether there was one.

    unlink removes a symlink itself and never follows it.
    """
    path = consent_path(home)
    if not os.path.lexists(path):
        return False
    os.unlink(path)
    return True
