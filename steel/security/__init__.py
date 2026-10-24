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


def policy_or_deny_all(home: Path) -> tuple[SteelPolicy, PolicyError | None]:
    """The policy in *home*, or an empty one (everything denied, still logged).

    For commands that must report a broken grants file and still log the denial.
    """
    try:
        return load_home_policy(home), None
    except PolicyError as exc:
        return SteelPolicy(audit=PolicyAuditLog(home / POLICY_AUDIT_FILE)), exc


__all__ = [
    "PolicyAuditLog",
    "PolicyError",
    "SteelPolicy",
    "load_home_policy",
    "policy_or_deny_all",
]
