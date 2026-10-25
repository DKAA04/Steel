"""CLI side of the policy: doctor, policy show, wipe --include-audit.

Each test uses a temporary OpenJarvis home (HomeTestCase), never the real one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

import test_steel as base

from steel.cli import main
from steel.memory import SteelMemory
from steel.security import PolicyAuditLog, SteelPolicy

LOG = "steel_policy_audit.jsonl"
GRANTS = "steel_policy.json"


class PolicyCliCase(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Doctor also looks for $HOME/.cache/...; keep the real one out of it.
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home.parent)})
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *argv: str, expect: int = 0) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(main(list(argv)), expect, out.getvalue())
        return out.getvalue()

    def snapshot(self) -> dict[str, tuple[int, int]]:
        """Name -> (mtime_ns, size) of everything directly in the home."""
        return {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in self.home.iterdir()
            if p.is_file()
        }

    def write_grants(self, data: object) -> None:
        path = self.home / GRANTS
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        path.chmod(0o600)

    def make_log(self) -> Path:
        policy = SteelPolicy(audit=PolicyAuditLog(self.home / LOG))
        policy.check("room", "memory:write", "t")
        return self.home / LOG


class WipeKeepsPolicyLogTest(PolicyCliCase):
    def test_wipe_keeps_the_policy_log_by_default(self) -> None:
        SteelMemory(self.home).remember("User likes tea")
        log = self.make_log()
        text = self.run_cli("wipe", "--confirm")
        self.assertTrue(log.exists())
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.assertIn("policy log: kept, 1 decision(s)", text)
        self.assertIn("All memory stores are empty.", text)
        self.assertTrue(SteelMemory(self.home).is_empty())

    def test_include_audit_removes_it(self) -> None:
        log = self.make_log()
        text = self.run_cli("wipe", "--confirm", "--include-audit")
        self.assertFalse(log.exists())
        self.assertIn("policy log: absent", text)
        self.assertIn(LOG, text.splitlines()[0])  # listed under "Removed:"

    def make_rotated_logs(self) -> list[Path]:
        log = self.make_log()
        rotated = [self.home / f"{LOG}.{n}" for n in (1, 2, 3)]
        for path in rotated:
            path.write_text('{"ts": "x"}\n')
            path.chmod(0o600)
        return [log, *rotated]

    def test_wipe_keeps_rotated_log_files_by_default(self) -> None:
        files = self.make_rotated_logs()
        text = self.run_cli("wipe", "--confirm")
        for path in files:
            self.assertTrue(path.exists(), path)
        self.assertIn("3 rotated file(s)", text)

    def test_include_audit_removes_the_rotated_files_too(self) -> None:
        files = self.make_rotated_logs()
        text = self.run_cli("wipe", "--confirm", "--include-audit")
        for path in files:
            self.assertFalse(path.exists(), path)
            self.assertIn(path.name, text.splitlines()[0])
        self.assertIn("policy log: absent", text)

    def test_wipe_api_include_audit_removes_rotated_files(self) -> None:
        files = self.make_rotated_logs()
        removed = SteelMemory(self.home).wipe(include_audit=True)
        self.assertEqual(sorted(removed), sorted(p.name for p in files))

    def test_wipe_never_deletes_the_grants_file(self) -> None:
        self.write_grants({"agents": []})
        self.make_log()
        self.run_cli("wipe", "--confirm", "--include-audit")
        self.assertTrue((self.home / GRANTS).exists())

    def test_include_audit_alone_still_needs_confirm(self) -> None:
        log = self.make_log()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main(["wipe", "--include-audit"])
        self.assertTrue(log.exists())

    def test_memory_wipe_api_default_keeps_the_log(self) -> None:
        log = self.make_log()
        memory = SteelMemory(self.home)
        self.assertNotIn(LOG, memory.wipe())
        self.assertTrue(log.exists())
        self.assertEqual(memory.wipe(include_audit=True), [LOG])
        self.assertFalse(log.exists())


class PolicyShowTest(PolicyCliCase):
    def test_default_shows_no_grants_and_the_vocabulary(self) -> None:
        text = self.run_cli("policy", "show")
        self.assertIn("Default: deny", text)
        self.assertIn("Agents and grants: (none)", text)
        for name in ("memory:read:facts", "sensor:camera", "fs:write", "shell:exec"):
            self.assertIn(name, text)
        self.assertIn("file:read", text)  # listed as never grantable
        self.assertIn("use fs:read", text)

    def test_it_is_read_only(self) -> None:
        self.write_grants({"agents": [{"agent_id": "room"}]})
        self.make_log()
        before = self.snapshot()
        self.run_cli("policy", "show")
        self.assertEqual(self.snapshot(), before)  # no file created or touched
        # And on an empty home it creates nothing at all.
        for name in (GRANTS, LOG):
            (self.home / name).unlink()
        self.run_cli("policy", "show")
        self.assertFalse((self.home / GRANTS).exists())
        self.assertFalse((self.home / LOG).exists())

    def test_it_prints_current_grants_and_denials(self) -> None:
        self.write_grants(
            {
                "agents": [
                    {
                        "agent_id": "room",
                        "grants": [
                            {"capability": "memory:read:notes"},
                            {
                                "capability": "sensor:camera",
                                "pattern": "camera_snapshot",
                            },
                        ],
                        "deny": ["net:outbound"],
                    },
                    {"agent_id": "idle"},
                ]
            }
        )
        text = self.run_cli("policy", "show")
        self.assertIn("grant memory:read:notes  pattern *", text)
        self.assertIn("grant sensor:camera  pattern camera_snapshot", text)
        self.assertIn("deny  net:outbound", text)
        self.assertIn("(no grants: everything denied)", text)

    def test_a_path_grant_shows_where_it_resolved_to(self) -> None:
        real = self.home.parent / "real-dir"
        real.mkdir()
        link = self.home.parent / "link-dir"
        link.symlink_to(real)
        self.write_grants(
            {
                "agents": [
                    {
                        "agent_id": "room",
                        "grants": [{"capability": "fs:read", "pattern": str(link)}],
                    }
                ]
            }
        )
        text = self.run_cli("policy", "show")
        self.assertIn(f"grant fs:read  pattern {link}", text)
        self.assertIn(f"resolved to {real.resolve()}", text)

    def test_it_reports_rotated_log_files(self) -> None:
        self.make_log()
        (self.home / f"{LOG}.1").write_text("{}\n")
        text = self.run_cli("policy", "show")
        self.assertIn("1 decision(s), 1 rotated file(s)", text)

    def test_an_invalid_file_is_reported_as_everything_denied(self) -> None:
        self.write_grants("not json")
        text = self.run_cli("policy", "show", expect=1)
        self.assertIn("INVALID", text)
        self.assertIn("every tool call is denied", text)


class DoctorPolicyTest(PolicyCliCase):
    def test_doctor_reports_deny_by_default_and_writes_no_log(self) -> None:
        # Exit status also covers the user's other settings; only the policy lines matter here.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["doctor"])
        lines = out.getvalue().splitlines()
        self.assertIn("policy: deny-by-default active", lines)
        self.assertIn("policy grants: 0 in steel_policy.json", lines)
        self.assertFalse((self.home / LOG).exists())

    def test_doctor_counts_grants(self) -> None:
        self.write_grants(
            {
                "agents": [
                    {"agent_id": "room", "grants": [{"capability": "memory:write"}]}
                ]
            }
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["doctor"])
        self.assertIn("policy grants: 1 in", out.getvalue())

    def test_doctor_flags_an_invalid_policy_file_and_fails(self) -> None:
        self.write_grants(
            {"agents": [{"agent_id": "room", "grants": [{"capability": "file:read"}]}]}
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = main(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("policy: INVALID file, everything is denied", out.getvalue())
        self.assertNotIn("deny-by-default active", out.getvalue())


class DoctorGrantWarningTest(PolicyCliCase):
    """Doctor loads the grants file fresh, so within one run a grant cannot have
    "moved"; it can only be a link or missing. "moved" is for a process that
    stays alive, and is checked on the policy object below."""

    def doctor_lines(self) -> list[str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["doctor"])
        return out.getvalue().splitlines()

    def grant_dir(self, path: Path) -> None:
        self.write_grants(
            {
                "agents": [
                    {
                        "agent_id": "room",
                        "grants": [{"capability": "fs:read", "pattern": str(path)}],
                    }
                ]
            }
        )

    def warnings(self) -> list[str]:
        return [line for line in self.doctor_lines() if "WARNING" in line]

    def test_a_plain_existing_directory_gives_no_warning(self) -> None:
        folder = self.home.parent / "docs-dir"
        folder.mkdir()
        self.grant_dir(folder)
        self.assertEqual(self.warnings(), [])

    def test_a_grant_through_a_symbolic_link_is_flagged(self) -> None:
        real = self.home.parent / "real"
        real.mkdir()
        link = self.home.parent / "docs-link"
        link.symlink_to(real)
        self.grant_dir(link)
        (warning,) = self.warnings()
        self.assertIn("fs:read grant for 'room'", warning)
        self.assertIn("symbolic link", warning)

    def test_a_grant_on_a_missing_directory_is_flagged(self) -> None:
        self.grant_dir(self.home.parent / "not-there")
        (warning,) = self.warnings()
        self.assertIn("does not exist", warning)

    def test_a_grant_that_moved_in_a_live_policy_is_flagged_as_grant_moved(
        self,
    ) -> None:
        from steel.cli import _policy_lines

        small = self.home.parent / "small"
        wide = self.home.parent / "wide"
        small.mkdir()
        wide.mkdir()
        link = self.home.parent / "live-link"
        link.symlink_to(small)
        policy = SteelPolicy()
        policy.grant("room", "fs:read", str(link))
        self.assertEqual([k for *_, k in policy.grant_warnings()], ["link"])
        link.unlink()
        link.symlink_to(wide)
        self.assertEqual([k for *_, k in policy.grant_warnings()], ["moved"])
        with mock.patch.object(SteelPolicy, "from_file", return_value=policy):
            ok, lines = _policy_lines(self.home)
        self.assertTrue(ok)  # a warning does not fail doctor
        self.assertTrue(any("grant_moved" in line for line in lines))
        self.assertIn("policy: deny-by-default active", lines)

    def test_non_file_grants_are_never_warned_about(self) -> None:
        self.write_grants(
            {
                "agents": [
                    {"agent_id": "room", "grants": [{"capability": "memory:read:*"}]}
                ]
            }
        )
        self.assertEqual(self.warnings(), [])

    def test_the_warning_changes_no_decision(self) -> None:
        policy = SteelPolicy()
        real = self.home.parent / "real2"
        real.mkdir()
        link = self.home.parent / "link2"
        link.symlink_to(real)
        policy.grant("room", "fs:read", str(link))
        before = policy.check_path("room", "fs:read", str(real / "x"))
        policy.grant_warnings()
        self.assertEqual(policy.check_path("room", "fs:read", str(real / "x")), before)


class ChatDoesNotTouchThePolicyTest(PolicyCliCase):
    def test_a_chat_session_creates_no_policy_files(self) -> None:
        session = self.session()
        session.ask("My favourite colour is teal.")
        session.close()
        self.assertFalse((self.home / GRANTS).exists())
        self.assertFalse((self.home / LOG).exists())
