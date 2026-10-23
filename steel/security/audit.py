"""Names-only log of permission decisions: ~/.openjarvis/steel_policy_audit.jsonl.

One JSON line per decision with exactly five fields: time, agent id,
capability, allow/deny and a reason code. It never receives the resource
(tool name, path, arguments) or any user text: ``write`` has no parameter
for them, so a caller cannot leak them by mistake.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


class PolicyAuditLog:
    """Append-only decision log, mode 600."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

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
        # O_NOFOLLOW: a symlink planted at this path is refused, not followed.
        # 0o600 at creation, then fchmod in case the file already existed wider.
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (line + "\n").encode("ascii"))
        finally:
            os.close(fd)

    def count(self) -> int:
        """Number of logged decisions (0 if the file does not exist)."""
        try:
            with open(self.path, "rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0
