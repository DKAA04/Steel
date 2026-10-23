"""CLI side of the policy: doctor, policy show, wipe --include-audit.

Each test uses a temporary OpenJarvis home (HomeTestCase), never the real one.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import test_steel as base

from steel.cli import main
from steel.memory import SteelMemory
from steel.security import PolicyAuditLog, SteelPolicy

LOG = "steel_policy_audit.jsonl"
GRANTS = "steel_policy.json"


class PolicyCliCase(base.HomeTestCase):
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


class ChatDoesNotTouchThePolicyTest(PolicyCliCase):
    def test_a_chat_session_creates_no_policy_files(self) -> None:
        session = self.session()
        session.ask("My favourite colour is teal.")
        session.close()
        self.assertFalse((self.home / GRANTS).exists())
        self.assertFalse((self.home / LOG).exists())
