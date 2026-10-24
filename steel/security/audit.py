"""Names-only log of permission decisions: ~/.openjarvis/steel_policy_audit.jsonl.

One JSON line per decision with exactly five fields: time, agent id,
capability, allow/deny and a reason code. It never receives the resource
(tool name, path, arguments) or any user text: ``write`` has no parameter
for them, so a caller cannot leak them by mistake.

When the log would grow past ``max_bytes`` (5 MB) it is renamed to ``.1``
(``.1`` -> ``.2`` -> ``.3``, the oldest is deleted) and a new file starts. If
that fails, ``write`` raises, which the policy turns into a deny.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

MAX_LOG_BYTES = 5 * 1024 * 1024
KEEP_ROTATED = 3  # steel_policy_audit.jsonl.1 ... .3


class PolicyAuditLog:
    """Append-only decision log, mode 600, rotated by size."""

    def __init__(self, path: Path, *, max_bytes: int = MAX_LOG_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.path = Path(path)
        self.max_bytes = max_bytes

    def _numbered(self, n: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{n}")

    def rotated_files(self) -> list[Path]:
        """The rotated files that exist, newest (.1) first."""
        found = [self._numbered(n) for n in range(1, KEEP_ROTATED + 1)]
        return [p for p in found if os.path.lexists(p)]

    def remove_all(self) -> list[str]:
        """Delete the log and every rotated file; return the names removed.

        unlink removes a symlink itself and never follows it.
        """
        removed = []
        for path in (
            self.path,
            *(self._numbered(n) for n in range(1, KEEP_ROTATED + 1)),
        ):
            if os.path.lexists(path):
                os.unlink(path)
                removed.append(path.name)
        return removed

    def _rotate_if_full(self, incoming: int) -> None:
        """Start a new file if appending *incoming* bytes would pass the limit."""
        try:
            info = os.lstat(self.path)  # lstat: a symlink is not followed
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise OSError("audit log is not a regular file")
        if info.st_size == 0 or info.st_size + incoming <= self.max_bytes:
            return
        # Oldest first. os.rename replaces a symlink planted at the destination
        # instead of writing through it.
        if os.path.lexists(self._numbered(KEEP_ROTATED)):
            os.unlink(self._numbered(KEEP_ROTATED))
        for n in range(KEEP_ROTATED - 1, 0, -1):
            if os.path.lexists(self._numbered(n)):
                os.rename(self._numbered(n), self._numbered(n + 1))
        os.rename(self.path, self._numbered(1))
        # An old log that was created wider than 600 must not stay that way.
        os.chmod(self._numbered(1), 0o600)

    def write(self, agent_id: str, capability: str, allowed: bool, reason: str) -> None:
        """Append one decision. Raises OSError if it cannot be written."""
        line = json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "agent_id": agent_id,
                "capability": capability,
                "decision": "allow" if allowed else "deny",
                "reason": reason,
            },
            ensure_ascii=True,
        )
        encoded = (line + "\n").encode("ascii")
        self._rotate_if_full(len(encoded))
        # O_NOFOLLOW: a symlink planted at this path is refused, not followed.
        # 0o600 at creation, then fchmod in case the file already existed wider.
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, encoded)
        finally:
            os.close(fd)

    def count(self) -> int:
        """Number of logged decisions (0 if the file does not exist)."""
        try:
            with open(self.path, "rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0
