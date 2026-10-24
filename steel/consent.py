"""A consent file: one timestamp, which the owner can delete.

Shared by the microphone (steel/voice/consent.py) and the camera
(steel/vision/consent.py), each with its own file, so both are held to the same
checks. The file is trusted only if it is a regular file (not a symlink), belongs
to this user and is not writable by others, so nobody else can grant consent on
the owner's behalf. It holds a time and nothing else.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path


def read_stamp(path: Path) -> str | None:
    """The timestamp in a well-formed consent file that is ours and private, else None."""
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


def write_stamp(path: Path, *, now: datetime | None = None) -> str:
    """Write the consent file (mode 600) and return the timestamp it holds."""
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    # O_NOFOLLOW: a symlink planted at this name is refused, not written through.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps({"consented_at": stamp}).encode("ascii"))
    finally:
        os.close(fd)
    return stamp


def delete_stamp(path: Path) -> bool:
    """Delete the consent file; return whether there was one.

    unlink removes a symlink itself and never follows it.
    """
    if not os.path.lexists(path):
        return False
    os.unlink(path)
    return True
