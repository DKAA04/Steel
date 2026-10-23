"""STEEL's permission policy for tool calls (see docs/POLICY.md)."""

from __future__ import annotations

from pathlib import Path

from steel.home import POLICY_AUDIT_FILE, POLICY_FILE
from steel.security.audit import PolicyAuditLog
from steel.security.policy import PolicyError, SteelPolicy


def load_home_policy(home: Path) -> SteelPolicy:
    """The policy in *home*: its grants file plus its audit log.

    A missing grants file gives an empty policy (everything denied). An invalid
    one raises PolicyError: callers must treat that as "no policy", never as
    "allow".
    """
    return SteelPolicy.from_file(
        home / POLICY_FILE, audit=PolicyAuditLog(home / POLICY_AUDIT_FILE)
    )


__all__ = ["PolicyAuditLog", "PolicyError", "SteelPolicy", "load_home_policy"]
