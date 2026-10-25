"""SteelPolicy: a deny-by-default permission policy for tool calls.

Adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis), Apache-2.0.
Source:  src/openjarvis/security/capabilities.py
         (``CapabilityPolicy._check_python``, ``AgentPolicy``, ``CapabilityGrant``
         and the validation in ``CapabilityPolicy._load_file``)
Commit:  108d257188cbad85f2e50209d9a61669ea4dec33

What changed from the original:
- DENY BY DEFAULT. The original returns ``not default_deny`` (allow) when an agent
  has no policy or no grant matches. Here every such case is a deny.
- No ``_default`` fallback agent: an unknown agent is denied, not given a baseline.
- Pure Python only; the Rust ``CapabilityPolicy`` that the original's ``check()``
  calls is not used.
- Capability names must be in a fixed vocabulary; unknown and legacy names are denied.
- Matching is exact instead of ``fnmatch`` globs. The only wildcard a grant may use
  is ``memory:read:*``; deny entries may use ``*`` and any ``family:*``. File paths
  are matched on resolved path components, not strings.
- Sensors, the speaker (audio:play) and room notes (camera text) are never covered
  by a wildcard; sensor and speaker grants must name one exact tool.
- Every decision is written to a names-only audit log; no log, no allow.
- Any exception inside a decision is a deny.
- The policy file is validated strictly (unknown keys rejected, symlink or
  group/world-writable file refused) and a bad file means no policy.

This module does not import OpenJarvis at all.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from steel.security.audit import PolicyAuditLog

logger = logging.getLogger(__name__)

# -- vocabulary (docs/POLICY.md section 2) ------------------------------------

VOCABULARY: dict[str, str] = {
    "memory:read:facts": "read durable facts, including facts about other people",
    "memory:read:notes": "read the owner's expiring notes (not room notes)",
    "memory:read:room": "read room notes: camera descriptions (untrusted text)",
    "memory:write": "add facts or notes",
    "sensor:camera": "read the camera",
    "sensor:microphone": "read the microphone",
    "audio:play": "play sound on the speakers (spoken replies)",
    "net:outbound": "open network connections",
    "fs:read": "read files (needs a path grant)",
    "fs:write": "write files (needs a path grant)",
    "shell:exec": "run commands or code",
    "tool:invoke": "minimum declaration for a tool that touches nothing else",
    "channel:send": "send to a messaging channel",
    "schedule:create": "create or change scheduled tasks",
    "system:admin": "manage agents and channels",
}
# OpenJarvis names that STEEL renamed or split. Recognised, never grantable.
LEGACY_NAMES: dict[str, str] = {
    "file:read": "fs:read",
    "file:write": "fs:write",
    "network:fetch": "net:outbound",
    "code:execute": "shell:exec",
    "memory:read": "memory:read:facts, memory:read:notes or memory:read:room",
}
SENSOR_CAPS = frozenset({"sensor:camera", "sensor:microphone"})
# A grant for these must name one exact tool, never "*": the sensors, and the
# speakers (what is played can be overheard; docs/SPEAK.md section 6).
EXACT_NAME_CAPS = SENSOR_CAPS | {"audio:play"}
# Capabilities whose resource is a file path, checked by check_path().
PATH_CAPS = frozenset({"fs:read", "fs:write"})
FAMILIES = frozenset(name.split(":")[0] for name in VOCABULARY)
# The only wildcard a GRANT may use (it covers memory:read:facts and :notes).
# Everything else needs an exact name; deny entries are looser (denying more is safe).
GRANT_WILDCARDS = frozenset({"memory:read:*"})
# Camera text needs its exact name, like a sensor: memory:read:* never covers it,
# so a grants file written before it existed does not start reading it.
NOT_BY_WILDCARD = frozenset({"memory:read:room"})

AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_PATH_CHARS = 4096
ROOT = Path("/")


class PolicyError(ValueError):
    """A policy, grant or tool declaration is invalid. Never means "allowed"."""


@dataclass(frozen=True)
class Grant:
    """One capability grant for an agent."""

    capability: str  # vocabulary name or "memory:read:*"
    pattern: str = "*"  # tool name or "*"; for fs:* an absolute path
    # fs:* only: where the pattern resolved to when the grant was loaded. check_path
    # compares against this, so a directory swapped for a symlink later grants nothing.
    resolved: str = ""


@dataclass
class AgentPolicy:
    """Grants and explicit denials of one agent."""

    grants: list[Grant] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


# Set by the executor wrapper after it has checked the path arguments of the
# call in progress. check() answers fs:* only while this says "verified".
_VERIFIED_PATHS: contextvars.ContextVar[frozenset[tuple[str, str]]] = (
    contextvars.ContextVar("steel_verified_paths", default=frozenset())
)


@contextlib.contextmanager
def paths_verified(agent_id: str, capabilities: list[str]) -> Iterator[None]:
    """Mark (agent, fs capability) pairs as path-checked for the current call."""
    token = _VERIFIED_PATHS.set(frozenset((agent_id, cap) for cap in capabilities))
    try:
        yield
    finally:
        _VERIFIED_PATHS.reset(token)


def _covers(expression: str, capability: str) -> bool:
    """Does a deny expression ("*", "family:*", "memory:read:*", exact) cover it?"""
    if expression == capability or expression == "*":
        return True
    return expression.endswith(":*") and capability.startswith(expression[:-1])


def _grant_covers(expression: str, capability: str) -> bool:
    """Does a GRANT cover *capability*? Exact, or the one allowed wildcard.

    Checked again here (not only when loading) so a wildcard that got into the
    grant list some other way still covers nothing.
    """
    if expression == capability:
        return True
    if capability in NOT_BY_WILDCARD:
        return False
    return expression in GRANT_WILDCARDS and capability.startswith(expression[:-1])


def valid_agent_id(agent_id: object) -> bool:
    return isinstance(agent_id, str) and AGENT_ID_RE.fullmatch(agent_id) is not None


def valid_tool_name(name: object) -> bool:
    return isinstance(name, str) and TOOL_NAME_RE.fullmatch(name) is not None


def _clean_absolute_path(path: object) -> str | None:
    """The path if it is a plain absolute path without '..', else None."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
        return None
    # Control characters include NUL, which truncates paths in C libraries.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        return None
    if not path.startswith("/"):
        return None  # relative paths and "~" are never expanded
    # Rejected even when it would stay inside the grant: there is no good
    # reason for a tool to send "..", and string tricks around it are the usual
    # way out of a directory.
    if ".." in PurePosixPath(path).parts:
        return None
    return path


def _resolve(path: str) -> Path:
    """Follow every symlink; a path that does not exist yet resolves through
    its existing parent (a dangling symlink is followed to its target)."""
    return Path(os.path.realpath(path))


def validate_capability_expression(expression: object, *, for_deny: bool) -> str:
    """Return a grant/deny capability if it is allowed, else raise PolicyError."""
    if not isinstance(expression, str) or not expression.strip():
        raise PolicyError("capability must be a non-empty string")
    if expression in LEGACY_NAMES:
        raise PolicyError(
            f"'{expression}' is an OpenJarvis name; use {LEGACY_NAMES[expression]}"
        )
    if expression in VOCABULARY:
        return expression
    if not for_deny:
        if expression in GRANT_WILDCARDS:
            return expression
        if expression == "*" or expression.endswith(":*"):
            # Sensors, paths and the dangerous capabilities need exact names
            # (POLICY.md 3.3).
            raise PolicyError(f"'{expression}' cannot be granted by a wildcard")
        raise PolicyError(f"unknown capability '{expression}'")
    # Deny entries: denying more is always safe.
    if expression == "*" or expression in GRANT_WILDCARDS:
        return expression
    if expression.endswith(":*") and expression[:-2] in FAMILIES:
        return expression
    raise PolicyError(f"unknown capability '{expression}'")


class SteelPolicy:
    """Permission policy with the ``check(agent_id, capability, resource)`` interface
    that OpenJarvis' ``ToolExecutor`` calls. See docs/POLICY.md."""

    def __init__(self, *, audit: PolicyAuditLog | None = None) -> None:
        self._agents: dict[str, AgentPolicy] = {}
        self.audit = audit

    # -- building the policy ---------------------------------------------

    def add_agent(self, agent_id: str) -> None:
        """Declare an agent with no grants yet (it is then denied everything)."""
        if not valid_agent_id(agent_id):
            raise PolicyError("agent_id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
        self._agents.setdefault(agent_id, AgentPolicy())

    def grant(self, agent_id: str, capability: str, pattern: str = "*") -> None:
        """Grant *capability* to an agent. Raises PolicyError if it is not allowed."""
        self.add_agent(agent_id)
        capability = validate_capability_expression(capability, for_deny=False)
        if capability in PATH_CAPS:
            clean = _clean_absolute_path(pattern)
            # "//" normalises to itself but resolves to "/": compare resolved.
            if clean is None or _resolve(clean) == ROOT:
                raise PolicyError(
                    f"{capability} needs an absolute path (not '/', no '..')"
                )
            # Resolved once, here; check_path compares against it (grant_moved).
            resolved = str(_resolve(clean))
            pattern = os.path.normpath(clean)
        else:
            resolved = ""
            if not isinstance(pattern, str) or not (
                pattern == "*" or valid_tool_name(pattern)
            ):
                raise PolicyError("pattern must be '*' or one exact tool name")
            if capability in EXACT_NAME_CAPS and pattern == "*":
                raise PolicyError(f"{capability} needs one exact tool name, not '*'")
        self._agents[agent_id].grants.append(Grant(capability, pattern, resolved))

    def deny(self, agent_id: str, capability: str) -> None:
        """Always deny *capability* (or a family, or '*') to an agent."""
        self.add_agent(agent_id)
        capability = validate_capability_expression(capability, for_deny=True)
        self._agents[agent_id].deny.append(capability)

    def agents(self) -> dict[str, AgentPolicy]:
        """A copy of every agent's grants and denials (for ``steel policy show``)."""
        return {
            name: AgentPolicy(list(a.grants), list(a.deny))
            for name, a in self._agents.items()
        }

    def grant_warnings(self) -> list[tuple[str, Grant, str]]:
        """File grants that deserve a look, as (agent, grant, what is wrong).

        "moved": resolves elsewhere than when it was loaded (the grant allows
        nothing, reason ``grant_moved``; only a process that stays alive can
        see this). "link": the path goes through a symbolic link, so moving the
        link changes what the next load grants. "missing": the path does not exist.
        Nothing here changes a decision.
        """
        found = []
        for name, agent in self._agents.items():
            for grant in agent.grants:
                if grant.capability not in PATH_CAPS:
                    continue
                try:
                    now = str(_resolve(grant.pattern))
                except (OSError, ValueError):
                    now = ""
                if not grant.resolved or now != grant.resolved:
                    found.append((name, grant, "moved"))
                elif not os.path.exists(grant.pattern):
                    found.append((name, grant, "missing"))
                elif now != grant.pattern:
                    found.append((name, grant, "link"))
        return found

    @classmethod
    def from_file(
        cls, path: Path, *, audit: PolicyAuditLog | None = None
    ) -> SteelPolicy:
        """Load grants from a JSON file; a missing file gives an empty policy.

        Raises PolicyError on anything wrong with the file or its content. The
        caller must then run with no policy (everything denied), never "open".
        """
        policy = cls(audit=audit)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return policy
        except OSError as exc:
            raise PolicyError(f"cannot read policy file: {exc.strerror}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise PolicyError("policy file must be a regular file, not a symlink")
        if info.st_mode & 0o022:
            raise PolicyError("policy file must not be writable by group or others")
        if info.st_uid != os.getuid():
            raise PolicyError("policy file must belong to the current user")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyError("policy file is not valid JSON") from exc
        _load_document(policy, data)
        return policy

    # -- decisions ---------------------------------------------------------

    def check(self, agent_id: str, capability: str, resource: str = "") -> bool:
        """May *agent_id* use *capability* for *resource* (a tool name)?

        This is the method ToolExecutor calls. Any problem is a deny.
        """
        try:
            allowed, reason = self._decide(agent_id, capability, resource)
        except Exception:  # noqa: BLE001 - fail closed on any bug
            allowed, reason = False, "error"
        return self._record(agent_id, capability, allowed, reason)

    def preview(
        self, agent_id: str, capability: str, resource: str
    ) -> tuple[bool, str]:
        """What check() would answer, with the reason code, without logging it.

        For ``steel tool list``: asking "would this be allowed?" is not a tool
        call, so it leaves no audit line.
        """
        try:
            return self._decide(agent_id, capability, resource)
        except Exception:  # noqa: BLE001 - fail closed on any bug
            return False, "error"

    def check_path(self, agent_id: str, capability: str, path: object) -> bool:
        """May *agent_id* use *capability* (fs:read / fs:write) on *path*?"""
        try:
            allowed, reason = self._decide_path(agent_id, capability, path)
        except Exception:  # noqa: BLE001 - fail closed on any bug
            allowed, reason = False, "error"
        return self._record(agent_id, capability, allowed, reason)

    def _decide(
        self, agent_id: Any, capability: Any, resource: Any
    ) -> tuple[bool, str]:
        problem = self._common_problem(agent_id, capability)
        if problem:
            return False, problem
        # The resource ToolExecutor passes is the tool name.
        if not valid_tool_name(resource):
            return False, "bad_resource"
        agent = self._agents[agent_id]
        if any(_covers(d, capability) for d in agent.deny):
            return False, "explicit_deny"
        if capability in PATH_CAPS:
            # ToolExecutor cannot see the path. Only the executor wrapper can
            # vouch for it, for the call in progress.
            if not any(g.capability == capability for g in agent.grants):
                return False, "no_grant"
            if (agent_id, capability) not in _VERIFIED_PATHS.get():
                return False, "path_not_checked"
            return True, "granted"
        for grant in agent.grants:
            if not _grant_covers(grant.capability, capability):
                continue
            if capability in EXACT_NAME_CAPS and grant.pattern == "*":
                continue  # a sensor or the speaker needs one exact tool name
            if grant.pattern == "*" or grant.pattern == resource:
                return True, "granted"
        return False, "no_grant"

    def _decide_path(
        self, agent_id: Any, capability: Any, path: Any
    ) -> tuple[bool, str]:
        problem = self._common_problem(agent_id, capability)
        if problem:
            return False, problem
        if capability not in PATH_CAPS:
            return False, "not_path_capability"
        agent = self._agents[agent_id]
        if any(_covers(d, capability) for d in agent.deny):
            return False, "explicit_deny"
        clean = _clean_absolute_path(path)
        if clean is None:
            return False, "bad_resource"
        grants = [g for g in agent.grants if g.capability == capability]
        if not grants:
            return False, "no_grant"
        real = _resolve(clean)
        moved = False
        for grant in grants:
            # The grant must still resolve to what it resolved to at load time.
            # A moved grant allows nothing; it never silently follows the move.
            base = Path(grant.resolved) if grant.resolved else None
            if base is None or str(_resolve(grant.pattern)) != grant.resolved:
                moved = True
                continue
            if base == ROOT:
                continue  # never grants "everything"
            # Compare path components, never string prefixes:
            # "/home/a" must not match "/home/ab".
            if real == base or real.is_relative_to(base):
                return True, "granted"
        return False, "grant_moved" if moved else "path_outside_grants"

    def _common_problem(self, agent_id: Any, capability: Any) -> str:
        """Reason code if the agent or capability is unusable, else ''."""
        if not valid_agent_id(agent_id):
            return "bad_agent_id"
        if not isinstance(capability, str) or (
            capability not in VOCABULARY and capability not in LEGACY_NAMES
        ):
            return "unknown_capability"
        if capability in LEGACY_NAMES:
            return "legacy_name"
        if agent_id not in self._agents:
            return "unknown_agent"
        return ""

    def _record(
        self, agent_id: Any, capability: Any, allowed: bool, reason: str
    ) -> bool:
        """Write the decision to the audit log; return the final answer.

        Only validated names reach the log. If the line cannot be written, an
        allow becomes a deny: no log, no action.
        """
        if self.audit is None:
            return allowed
        safe_agent = agent_id if valid_agent_id(agent_id) else "(invalid)"
        known = isinstance(capability, str) and (
            capability in VOCABULARY or capability in LEGACY_NAMES
        )
        safe_capability = capability if known else "(unknown)"
        try:
            self.audit.write(safe_agent, str(safe_capability), allowed, reason)
        except Exception:  # noqa: BLE001 - never raise out of a decision
            logger.warning("policy audit log could not be written; denying")
            return False
        return allowed


def _load_document(policy: SteelPolicy, data: object) -> None:
    """Apply a parsed policy file to *policy*; raise PolicyError if invalid.

    The whole document is checked as it is applied and the caller throws the
    policy away on error, so a half-loaded file is never used.
    """
    if not isinstance(data, dict) or set(data) != {"agents"}:
        raise PolicyError('policy file must be {"agents": [...]}')
    if not isinstance(data["agents"], list):
        raise PolicyError("agents must be a list")
    seen: set[str] = set()
    for entry in data["agents"]:
        if not isinstance(entry, dict) or not {"agent_id"} <= set(entry) <= {
            "agent_id",
            "grants",
            "deny",
        }:
            raise PolicyError("each agent needs agent_id and optionally grants, deny")
        agent_id = entry["agent_id"]
        if not isinstance(agent_id, str) or agent_id in seen:
            raise PolicyError("agent_id must be a string and unique")
        seen.add(agent_id)
        grants, denied = entry.get("grants", []), entry.get("deny", [])
        if not isinstance(grants, list) or not isinstance(denied, list):
            raise PolicyError("grants and deny must be lists")
        policy.add_agent(agent_id)
        for item in grants:
            if (
                not isinstance(item, dict)
                or "capability" not in item
                or not set(item) <= {"capability", "pattern"}
            ):
                raise PolicyError("a grant needs capability and optionally pattern")
            policy.grant(agent_id, item["capability"], item.get("pattern", "*"))
        for item in denied:
            policy.deny(agent_id, item)
