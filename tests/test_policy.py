"""SteelPolicy: deny by default, matching rules, sensors, paths, audit log.

Everything runs in temporary directories with invented names. No tool is
registered anywhere; the executor tests use a fake harmless tool. No test
touches the real ~/.openjarvis or calls a model.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from steel.security import PolicyAuditLog, PolicyError, SteelPolicy, load_home_policy
from steel.security.policy import (
    LEGACY_NAMES,
    VOCABULARY,
    Grant,
    paths_verified,
)


class PolicyCase(unittest.TestCase):
    """A temporary directory, an audit log in it, and helpers."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # realpath: /tmp may itself be a symlink on some systems.
        self.root = Path(os.path.realpath(self._tmp.name))
        self.log_path = self.root / "audit.jsonl"
        self.policy = SteelPolicy(audit=PolicyAuditLog(self.log_path))

    def log(self) -> list[dict[str, str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def last_decision(self) -> tuple[str, str]:
        entry = self.log()[-1]
        return entry["decision"], entry["reason"]


class DenyByDefaultTest(PolicyCase):
    def test_deny_with_no_grants_at_all(self) -> None:
        for cap in VOCABULARY:
            self.assertIs(self.policy.check("room", cap, "any_tool"), False, cap)
        self.assertEqual(self.last_decision(), ("deny", "unknown_agent"))

    def test_deny_for_a_known_agent_with_no_grants(self) -> None:
        self.policy.add_agent("room")
        self.assertIs(self.policy.check("room", "memory:write", "t"), False)
        self.assertEqual(self.last_decision(), ("deny", "no_grant"))

    def test_allow_with_an_exact_grant(self) -> None:
        self.policy.grant("room", "memory:read:notes", "notes_reader")
        self.assertIs(
            self.policy.check("room", "memory:read:notes", "notes_reader"), True
        )
        self.assertEqual(self.last_decision(), ("allow", "granted"))

    def test_grant_is_per_capability_and_per_tool(self) -> None:
        self.policy.grant("room", "memory:read:notes", "notes_reader")
        self.assertFalse(self.policy.check("room", "memory:read:facts", "notes_reader"))
        self.assertFalse(self.policy.check("room", "memory:read:notes", "other_tool"))
        self.assertFalse(self.policy.check("room", "memory:write", "notes_reader"))

    def test_star_pattern_allows_any_tool_for_that_capability_only(self) -> None:
        self.policy.grant("room", "memory:read:notes")
        self.assertTrue(self.policy.check("room", "memory:read:notes", "anything"))
        self.assertFalse(self.policy.check("room", "memory:write", "anything"))

    def test_deny_for_the_wrong_agent_id(self) -> None:
        self.policy.grant("room", "memory:read:notes")
        self.assertFalse(self.policy.check("kitchen", "memory:read:notes", "t"))
        self.assertEqual(self.last_decision(), ("deny", "unknown_agent"))
        # Near misses are different agents, not the same one.
        for other in ("Room", "room ", "room2", "roo"):
            self.assertFalse(self.policy.check(other, "memory:read:notes", "t"), other)

    def test_there_is_no_default_agent_fallback(self) -> None:
        # OpenJarvis lets unknown agents inherit "_default"; STEEL does not.
        with self.assertRaises(PolicyError):
            self.policy.grant("_default", "memory:read:notes")
        self.policy.grant("default", "memory:read:notes")
        self.assertFalse(self.policy.check("room", "memory:read:notes", "t"))

    def test_explicit_deny_beats_a_grant(self) -> None:
        self.policy.grant("room", "memory:read:*")
        self.policy.deny("room", "memory:read:facts")
        self.assertFalse(self.policy.check("room", "memory:read:facts", "t"))
        self.assertEqual(self.last_decision(), ("deny", "explicit_deny"))
        self.assertTrue(self.policy.check("room", "memory:read:notes", "t"))

    def test_deny_family_wildcard(self) -> None:
        self.policy.grant("room", "memory:read:facts")
        self.policy.grant("room", "memory:write")
        self.policy.grant("room", "shell:exec")
        self.policy.deny("room", "memory:*")
        self.assertFalse(self.policy.check("room", "memory:read:facts", "t"))
        self.assertFalse(self.policy.check("room", "memory:write", "t"))
        self.assertTrue(self.policy.check("room", "shell:exec", "t"))

    def test_deny_entries_may_use_any_wildcard(self) -> None:
        # Denying more is safe, so deny is looser than grant (POLICY.md 3.3).
        for expression in ("*", "memory:*", "memory:read:*", "sensor:*", "fs:*"):
            policy = SteelPolicy()
            policy.grant("room", "memory:read:facts")
            policy.deny("room", expression)
            self.assertEqual(policy.agents()["room"].deny, [expression])
        policy = SteelPolicy()
        policy.grant("room", "memory:read:facts")
        policy.deny("room", "*")
        self.assertFalse(policy.check("room", "memory:read:facts", "t"))


class FailClosedTest(PolicyCase):
    def setUp(self) -> None:
        super().setUp()
        self.policy.grant("room", "memory:read:notes")

    def test_unknown_capability_is_denied(self) -> None:
        for cap in (
            "memory:delete",
            "",
            "*",
            "memory:*",
            "MEMORY:WRITE",
            None,
            5,
            ["x"],
        ):
            self.assertIs(self.policy.check("room", cap, "t"), False, repr(cap))  # type: ignore[arg-type]
            self.assertEqual(self.last_decision(), ("deny", "unknown_capability"))

    def test_legacy_openjarvis_names_are_denied_and_not_grantable(self) -> None:
        for old in LEGACY_NAMES:
            self.assertFalse(self.policy.check("room", old, "t"), old)
            self.assertEqual(self.last_decision(), ("deny", "legacy_name"))
            with self.assertRaises(PolicyError):
                self.policy.grant("room", old)

    def test_bad_agent_ids_are_denied(self) -> None:
        for agent in ("", " ", "a b", "../x", "room\n", "-room", "x" * 65, None, 7):
            self.assertIs(self.policy.check(agent, "memory:read:notes", "t"), False)  # type: ignore[arg-type]
            self.assertEqual(self.last_decision(), ("deny", "bad_agent_id"))

    def test_malformed_resource_is_denied(self) -> None:
        for resource in ("", "a/b", "../t", "t t", "t\n", "x" * 65, None, 3):
            self.assertIs(
                self.policy.check("room", "memory:read:notes", resource),  # type: ignore[arg-type]
                False,
                repr(resource),
            )
            self.assertEqual(self.last_decision(), ("deny", "bad_resource"))

    def test_an_exception_inside_matching_is_a_deny(self) -> None:
        self.assertTrue(self.policy.check("room", "memory:read:notes", "t"))
        with mock.patch.object(
            SteelPolicy, "_decide", side_effect=RuntimeError("boom")
        ):
            self.assertIs(self.policy.check("room", "memory:read:notes", "t"), False)
        self.assertEqual(self.last_decision(), ("deny", "error"))
        with mock.patch.object(
            SteelPolicy, "_decide_path", side_effect=RuntimeError("boom")
        ):
            self.assertIs(self.policy.check_path("room", "fs:read", "/tmp"), False)
        self.assertEqual(self.last_decision(), ("deny", "error"))

    def test_a_corrupt_internal_state_is_a_deny_not_a_crash(self) -> None:
        self.policy._agents["room"].grants.append(None)  # type: ignore[arg-type]
        self.assertIs(self.policy.check("room", "shell:exec", "t"), False)
        self.assertEqual(self.last_decision(), ("deny", "error"))


class WildcardAndSensorTest(PolicyCase):
    def test_the_only_grantable_wildcard_covers_the_memory_read_pair(self) -> None:
        self.policy.grant("room", "memory:read:*")
        for cap in ("memory:read:facts", "memory:read:notes"):
            self.assertTrue(self.policy.check("room", cap, "t"), cap)
        for cap in ("memory:write", "net:outbound", "shell:exec", "system:admin"):
            self.assertFalse(self.policy.check("room", cap, "t"), cap)

    def test_every_other_wildcard_grant_is_refused(self) -> None:
        for expression in (
            "*",
            "memory:*",
            "memory:write:*",
            "net:*",
            "shell:*",
            "system:*",
            "channel:*",
            "schedule:*",
            "tool:*",
            "sensor:*",
            "fs:*",
            "memory:read:*x",
            "memory:read*",
            "*:*",
        ):
            with self.assertRaises(PolicyError, msg=expression):
                self.policy.grant("room", expression)
        self.assertEqual(self.policy.agents()["room"].grants, [])

    def test_exact_names_are_required_for_the_dangerous_capabilities(self) -> None:
        for cap in (
            "shell:exec",
            "net:outbound",
            "memory:write",
            "system:admin",
            "channel:send",
            "schedule:create",
        ):
            policy = SteelPolicy()
            policy.grant("room", cap, "one_tool")
            self.assertTrue(policy.check("room", cap, "one_tool"), cap)
            self.assertFalse(policy.check("room", cap, "other_tool"), cap)

    def test_a_wildcard_inserted_behind_the_validation_covers_nothing(self) -> None:
        # Defence in depth: check() itself ignores "*" and the family wildcards.
        self.policy.add_agent("room")
        self.policy._agents["room"].grants += [
            Grant("*", "*"),
            Grant("memory:*", "*"),
            Grant("net:*", "*"),
        ]
        for cap in VOCABULARY:
            self.assertFalse(self.policy.check("room", cap, "t"), cap)

    def test_sensor_wildcards_cannot_even_be_written(self) -> None:
        for cap, pattern in (
            ("sensor:*", "*"),
            ("sensor:*", "camera_snapshot"),
            ("sensor:camera", "*"),
            ("sensor:microphone", "*"),
        ):
            with self.assertRaises(PolicyError, msg=f"{cap} {pattern}"):
                self.policy.grant("room", cap, pattern)
        self.assertEqual(self.policy.agents()["room"].grants, [])

    def test_sensor_needs_its_own_explicit_grant_with_a_tool_name(self) -> None:
        self.policy.grant("room", "sensor:camera", "camera_snapshot")
        self.assertTrue(self.policy.check("room", "sensor:camera", "camera_snapshot"))
        self.assertFalse(self.policy.check("room", "sensor:camera", "other_tool"))
        # One sensor grant does not open the other sensor.
        self.assertFalse(
            self.policy.check("room", "sensor:microphone", "camera_snapshot")
        )

    def test_a_wildcard_sensor_grant_inserted_behind_the_validation_is_ignored(
        self,
    ) -> None:
        # Defence in depth: even if something bypassed grant(), check() refuses.
        self.policy.add_agent("room")
        agent = self.policy._agents["room"]
        agent.grants += [
            Grant("sensor:*", "*"),
            Grant("*", "*"),
            Grant("sensor:camera", "*"),
        ]
        for cap in ("sensor:camera", "sensor:microphone"):
            self.assertFalse(self.policy.check("room", cap, "camera_snapshot"), cap)

    def test_wildcards_do_not_grant_file_access(self) -> None:
        self.policy.grant("room", "memory:read:*")
        with self.assertRaises(PolicyError):
            self.policy.grant("room", "fs:*", "/tmp")
        self.assertFalse(self.policy.check_path("room", "fs:read", str(self.root)))
        self.assertEqual(self.last_decision(), ("deny", "no_grant"))
        self.assertFalse(self.policy.check("room", "fs:read", "file_reader"))


class PathTest(PolicyCase):
    def setUp(self) -> None:
        super().setUp()
        base = self.root
        (base / "a" / "sub").mkdir(parents=True)
        (base / "ab").mkdir()
        (base / "elsewhere").mkdir()
        (base / "a" / "sub" / "ok.txt").write_text("synthetic")
        (base / "ab" / "secret.txt").write_text("synthetic secret")
        (base / "elsewhere" / "secret.txt").write_text("synthetic secret")
        # A link inside the granted directory that points outside it.
        (base / "a" / "escape").symlink_to(base / "elsewhere")
        (base / "a" / "escape_file").symlink_to(base / "ab" / "secret.txt")
        # A link inside the granted directory that stays inside it.
        (base / "a" / "inside").symlink_to(base / "a" / "sub")
        self.policy.grant("room", "fs:read", str(base / "a"))

    def read(self, path: str | Path) -> bool:
        return self.policy.check_path("room", "fs:read", str(path))

    def test_paths_inside_the_grant_are_allowed(self) -> None:
        for path in (
            self.root / "a",
            self.root / "a" / "sub" / "ok.txt",
            self.root / "a" / "sub",
        ):
            self.assertTrue(self.read(path), path)
        self.assertEqual(self.last_decision(), ("allow", "granted"))

    def test_a_file_that_does_not_exist_yet_inside_the_grant_is_allowed(self) -> None:
        self.policy.grant("room", "fs:write", str(self.root / "a"))
        self.assertTrue(
            self.policy.check_path("room", "fs:write", str(self.root / "a" / "new.txt"))
        )

    def test_sibling_prefix_is_not_a_match(self) -> None:
        # "/…/a" must not match "/…/ab".
        self.assertFalse(self.read(self.root / "ab" / "secret.txt"))
        self.assertFalse(self.read(self.root / "ab"))
        self.assertEqual(self.last_decision(), ("deny", "path_outside_grants"))
        self.assertFalse(self.read(str(self.root / "a") + "b/secret.txt"))

    def test_paths_with_dot_dot_are_rejected_even_when_they_stay_inside(self) -> None:
        inside = f"{self.root}/a/sub/../sub/ok.txt"
        escape = f"{self.root}/a/../ab/secret.txt"
        for path in (inside, escape, f"{self.root}/a/..", f"{self.root}/a/sub/.."):
            self.assertFalse(self.read(path), path)
            self.assertEqual(self.last_decision(), ("deny", "bad_resource"))

    def test_absolute_paths_outside_the_grant_are_denied(self) -> None:
        for path in (
            "/etc/passwd",
            "/",
            "/tmp",
            str(self.root),
            str(self.root / "elsewhere"),
        ):
            self.assertFalse(self.read(path), path)

    def test_relative_home_and_malformed_paths_are_denied(self) -> None:
        for path in (
            "a/sub/ok.txt",
            "./a",
            "~/a",
            "",
            " ",
            f"{self.root}/a\x00/x",
            f"{self.root}/a\n",
        ):
            self.assertFalse(self.read(path), repr(path))
            self.assertEqual(self.last_decision(), ("deny", "bad_resource"))
        for value in (None, 5, ["/tmp"], b"/tmp"):
            self.assertFalse(self.policy.check_path("room", "fs:read", value))  # type: ignore[arg-type]
        self.assertFalse(self.read("/" + "a" * 5000))

    def test_symlink_pointing_outside_is_resolved_and_denied(self) -> None:
        self.assertFalse(self.read(self.root / "a" / "escape" / "secret.txt"))
        self.assertFalse(self.read(self.root / "a" / "escape"))
        self.assertFalse(self.read(self.root / "a" / "escape_file"))
        self.assertEqual(self.last_decision(), ("deny", "path_outside_grants"))

    def test_a_dangling_symlink_to_the_outside_is_denied_for_writing(self) -> None:
        self.policy.grant("room", "fs:write", str(self.root / "a"))
        (self.root / "a" / "dangling").symlink_to(self.root / "ab" / "not-yet")
        self.assertFalse(
            self.policy.check_path(
                "room", "fs:write", str(self.root / "a" / "dangling")
            )
        )

    def test_symlink_that_stays_inside_is_allowed(self) -> None:
        self.assertTrue(self.read(self.root / "a" / "inside" / "ok.txt"))

    def test_the_granted_path_is_resolved_too(self) -> None:
        link = self.root / "grantlink"
        link.symlink_to(self.root / "ab")
        self.policy.grant("room", "fs:write", str(link))
        self.assertTrue(
            self.policy.check_path("room", "fs:write", str(self.root / "ab" / "x"))
        )
        self.assertFalse(
            self.policy.check_path("room", "fs:write", str(self.root / "a" / "x"))
        )

    def test_read_grant_does_not_allow_write_and_other_agents_are_denied(self) -> None:
        self.assertFalse(
            self.policy.check_path("room", "fs:write", str(self.root / "a" / "x"))
        )
        self.assertEqual(self.last_decision(), ("deny", "no_grant"))
        self.assertFalse(
            self.policy.check_path("kitchen", "fs:read", str(self.root / "a"))
        )
        self.assertEqual(self.last_decision(), ("deny", "unknown_agent"))

    def test_check_path_only_answers_for_path_capabilities(self) -> None:
        self.policy.grant("room", "memory:write")
        self.assertFalse(self.policy.check_path("room", "memory:write", str(self.root)))
        self.assertEqual(self.last_decision(), ("deny", "not_path_capability"))

    def test_bad_path_grants_are_refused(self) -> None:
        for pattern in (
            "/",
            "//",
            "relative/dir",
            "~/docs",
            "*",
            "/a/../b",
            "",
            "/a\x00",
        ):
            with self.assertRaises(PolicyError, msg=repr(pattern)):
                self.policy.grant("room", "fs:read", pattern)

    def test_a_grant_that_resolves_to_root_is_refused_or_ignored(self) -> None:
        (self.root / "toroot").symlink_to("/")
        with self.assertRaises(PolicyError):
            self.policy.grant("room", "fs:write", str(self.root / "toroot"))
        # A link retargeted to "/" after it was granted grants nothing either.
        link = self.root / "later"
        link.symlink_to(self.root / "a")
        self.policy.grant("room", "fs:write", str(link))
        self.assertTrue(
            self.policy.check_path("room", "fs:write", str(self.root / "a" / "x"))
        )
        link.unlink()
        link.symlink_to("/")
        self.assertFalse(self.policy.check_path("room", "fs:write", "/etc/passwd"))
        self.assertFalse(
            self.policy.check_path("room", "fs:write", str(self.root / "a" / "x"))
        )

    def test_explicit_deny_beats_a_path_grant(self) -> None:
        self.policy.deny("room", "fs:read")
        self.assertFalse(self.read(self.root / "a" / "sub" / "ok.txt"))
        self.assertEqual(self.last_decision(), ("deny", "explicit_deny"))

    def test_check_answers_fs_only_after_the_executor_verified_the_path(self) -> None:
        # ToolExecutor passes only the tool name, so check() alone cannot allow fs:*.
        self.assertFalse(self.policy.check("room", "fs:read", "file_reader"))
        self.assertEqual(self.last_decision(), ("deny", "path_not_checked"))
        with paths_verified("room", ["fs:read"]):
            self.assertTrue(self.policy.check("room", "fs:read", "file_reader"))
            # Verified for one agent and capability, nothing else.
            self.assertFalse(self.policy.check("room", "fs:write", "file_reader"))
            self.assertFalse(self.policy.check("kitchen", "fs:read", "file_reader"))
        self.assertFalse(self.policy.check("room", "fs:read", "file_reader"))


class AuditLogTest(PolicyCase):
    def test_every_decision_is_logged_with_exactly_five_fields(self) -> None:
        self.policy.grant("room", "memory:write", "writer")
        self.policy.check("room", "memory:write", "writer")
        self.policy.check("room", "memory:write", "other")
        self.policy.check("nobody", "memory:write", "writer")
        self.policy.check_path("room", "fs:read", "/etc/passwd")
        entries = self.log()
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertEqual(
                sorted(entry), ["agent_id", "capability", "decision", "reason", "ts"]
            )
        self.assertEqual(
            [(e["decision"], e["reason"]) for e in entries],
            [
                ("allow", "granted"),
                ("deny", "no_grant"),
                ("deny", "unknown_agent"),
                ("deny", "no_grant"),
            ],
        )

    def test_log_never_contains_resources_paths_or_caller_text(self) -> None:
        secret_tool = "SECRET_TOOL_NAME_4711"
        secret_path = str(self.root / "SECRET_PATH_4711" / "diary.txt")
        self.policy.grant("room", "memory:write")
        self.policy.grant("room", "fs:read", str(self.root / "ok"))
        self.policy.check("room", "memory:write", secret_tool)
        self.policy.check("room", "fs:read", secret_tool)
        self.policy.check_path("room", "fs:read", secret_path)
        self.policy.check_path("room", "fs:read", f"{secret_path}/../..")
        self.policy.check("room", "my secret sentence 4711", secret_tool)
        self.policy.check("evil\nagent SECRET_4711", "memory:write", secret_tool)
        raw = self.log_path.read_text()
        self.assertNotIn("4711", raw)
        self.assertNotIn(str(self.root), raw)
        agents = {e["agent_id"] for e in self.log()}
        self.assertEqual(agents, {"room", "(invalid)"})
        caps = {e["capability"] for e in self.log()}
        self.assertEqual(caps, {"memory:write", "fs:read", "(unknown)"})

    def test_log_has_mode_600_when_created_and_when_it_already_existed(self) -> None:
        self.policy.check("room", "memory:write", "t")
        self.assertEqual(stat.S_IMODE(self.log_path.stat().st_mode), 0o600)
        self.log_path.chmod(0o644)
        self.policy.check("room", "memory:write", "t")
        self.assertEqual(stat.S_IMODE(self.log_path.stat().st_mode), 0o600)

    def test_log_is_created_private_even_with_a_permissive_umask(self) -> None:
        old = os.umask(0o000)
        self.addCleanup(os.umask, old)
        self.policy.check("room", "memory:write", "t")
        self.assertEqual(stat.S_IMODE(self.log_path.stat().st_mode), 0o600)

    def test_an_allow_becomes_a_deny_when_the_log_cannot_be_written(self) -> None:
        self.policy.grant("room", "memory:write")
        self.assertTrue(self.policy.check("room", "memory:write", "t"))
        # Directory missing: nothing can be logged, so nothing may be allowed.
        broken = SteelPolicy(
            audit=PolicyAuditLog(self.root / "missing" / "audit.jsonl")
        )
        broken.grant("room", "memory:write")
        self.assertIs(broken.check("room", "memory:write", "t"), False)

    def test_a_symlinked_log_is_refused_not_followed(self) -> None:
        target = self.root / "elsewhere.txt"
        target.write_text("")
        link = self.root / "link.jsonl"
        link.symlink_to(target)
        policy = SteelPolicy(audit=PolicyAuditLog(link))
        policy.grant("room", "memory:write")
        self.assertFalse(policy.check("room", "memory:write", "t"))
        self.assertEqual(target.read_text(), "")


class PolicyFileTest(PolicyCase):
    def write(self, data: object, mode: int = 0o600) -> Path:
        path = self.root / "steel_policy.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        path.chmod(mode)
        return path

    GOOD: ClassVar[dict[str, object]] = {
        "agents": [
            {
                "agent_id": "room",
                "grants": [
                    {"capability": "memory:read:notes"},
                    {"capability": "sensor:camera", "pattern": "camera_snapshot"},
                    {"capability": "fs:read", "pattern": "/srv/synthetic"},
                ],
                "deny": ["net:outbound"],
            }
        ]
    }

    def test_missing_file_means_no_grants(self) -> None:
        policy = SteelPolicy.from_file(self.root / "nope.json")
        self.assertEqual(policy.agents(), {})
        self.assertFalse(policy.check("room", "memory:read:notes", "t"))

    def test_a_valid_file_is_loaded(self) -> None:
        policy = SteelPolicy.from_file(self.write(self.GOOD))
        self.assertTrue(policy.check("room", "memory:read:notes", "t"))
        self.assertTrue(policy.check("room", "sensor:camera", "camera_snapshot"))
        self.assertFalse(policy.check("room", "net:outbound", "t"))

    def test_invalid_files_are_refused_with_no_partial_policy(self) -> None:
        bad_documents = [
            "not json",
            "[]",
            {},
            {"agents": {}},
            {"agents": [], "extra": 1},
            {"agents": [{"grants": []}]},
            {"agents": [{"agent_id": "room", "grant": []}]},  # typo of "grants"
            {"agents": [{"agent_id": "room", "denies": []}]},  # typo of "deny"
            {"agents": [{"agent_id": "room"}, {"agent_id": "room"}]},
            {"agents": [{"agent_id": "bad id"}]},
            {"agents": [{"agent_id": "room", "grants": [{"capability": "file:read"}]}]},
            {"agents": [{"agent_id": "room", "grants": [{"capability": "nonsense"}]}]},
            {"agents": [{"agent_id": "room", "grants": [{"capability": "sensor:*"}]}]},
            # A bare "*" (or a family wildcard) makes the whole file invalid.
            {"agents": [{"agent_id": "room", "grants": [{"capability": "*"}]}]},
            {"agents": [{"agent_id": "room", "grants": [{"capability": "memory:*"}]}]},
            {
                "agents": [
                    {
                        "agent_id": "room",
                        "grants": [
                            {"capability": "memory:read:facts"},
                            {"capability": "*", "pattern": "cli"},
                        ],
                    }
                ]
            },
            {
                "agents": [
                    {"agent_id": "room", "grants": [{"capability": "sensor:camera"}]}
                ]
            },
            {"agents": [{"agent_id": "room", "grants": [{"capability": "fs:read"}]}]},
            {
                "agents": [
                    {
                        "agent_id": "room",
                        "grants": [{"capability": "fs:read", "pattern": "/"}],
                    }
                ]
            },
            {
                "agents": [
                    {
                        "agent_id": "room",
                        "grants": [{"capability": "memory:write", "extra": 1}],
                    }
                ]
            },
            {"agents": [{"agent_id": "room", "deny": ["typo:cap"]}]},
            {"agents": [{"agent_id": "room", "grants": "memory:write"}]},
        ]
        for document in bad_documents:
            with self.assertRaises(PolicyError, msg=str(document)):
                SteelPolicy.from_file(self.write(document))

    def test_symlinked_or_loosely_protected_files_are_refused(self) -> None:
        real = self.write(self.GOOD)
        link = self.root / "link.json"
        link.symlink_to(real)
        with self.assertRaises(PolicyError):
            SteelPolicy.from_file(link)
        for mode in (0o660, 0o666, 0o602, 0o620):
            with self.assertRaises(PolicyError, msg=oct(mode)):
                SteelPolicy.from_file(self.write(self.GOOD, mode))
        SteelPolicy.from_file(self.write(self.GOOD, 0o644))  # readable is fine
        with self.assertRaises(PolicyError):
            SteelPolicy.from_file(self.root)  # a directory

    def test_load_home_policy_wires_the_audit_log(self) -> None:
        (self.root / "steel_policy.json").write_text(json.dumps(self.GOOD))
        (self.root / "steel_policy.json").chmod(0o600)
        policy = load_home_policy(self.root)
        policy.check("room", "memory:read:notes", "t")
        self.assertEqual(
            len((self.root / "steel_policy_audit.jsonl").read_text().splitlines()), 1
        )


if __name__ == "__main__":
    unittest.main()
