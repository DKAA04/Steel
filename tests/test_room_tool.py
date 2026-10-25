"""memory_search_room and the capability memory:read:room (docs/INJECTION.md section 3).

Room notes are camera descriptions: untrusted text. They get their own
capability and tool, memory_search_notes no longer returns them, and the grant
wildcard memory:read:* does not cover them. Temporary home, invented data; no
model, camera or network.
"""

from __future__ import annotations

import sqlite3
import time

from test_memory_tools import NO_MATCHES, ToolCase, grant_file

from steel.memory import SteelMemory
from steel.security.policy import VOCABULARY, SteelPolicy
from steel.tools import get_memory_tools
from steel.vision.commands import room_note

ROOM = ("memory:read:room", "memory_search_room")
NOTES = ("memory:read:notes", "memory_search_notes")


class RoomCase(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        memory = SteelMemory(self.home)
        memory.remember("Dentist on Friday, bring the blue folder", days=3)  # user
        memory.facts._add_note("Visiting Ana next week with the folder")  # time_bound
        memory.remember_room(
            room_note("A blue folder on the desk next to a mug.", time.time()),
            hours=24,
        )

    def found(self, name: str, query: str = "folder") -> list[str]:
        result = self.call(name, {"query": query})
        self.assertTrue(result.success, result.content)
        return self.entries(result)


class RoomToolTest(RoomCase):
    def test_the_room_tool_declares_exactly_memory_read_room(self) -> None:
        tools = {t.spec.name: t for t in get_memory_tools(self.home)}
        self.assertEqual(
            set(tools),
            {"memory_search_facts", "memory_search_notes", "memory_search_room"},
        )
        spec = tools["memory_search_room"].spec
        self.assertEqual(spec.required_capabilities, ["memory:read:room"])
        self.assertNotIn("path_params", spec.metadata)
        self.assertIn("camera", spec.description)
        self.assertIn("memory:read:room", VOCABULARY)

    def test_the_room_tool_returns_room_notes_and_nothing_else(self) -> None:
        self.write_grants(grant_file(ROOM))
        lines = self.found("memory_search_room")
        self.assertEqual(len(lines), 1)
        self.assertIn("A blue folder on the desk", lines[0])
        self.assertNotIn("Dentist", "\n".join(lines))
        self.assertNotIn("Ana", "\n".join(lines))

    def test_memory_search_notes_no_longer_returns_room_notes(self) -> None:
        self.write_grants(grant_file(NOTES))
        text = "\n".join(self.found("memory_search_notes"))
        self.assertIn("Dentist on Friday", text)  # user note
        self.assertIn("Visiting Ana", text)  # time_bound note
        self.assertNotIn("blue folder on the desk", text)
        self.assertNotIn("Camera description", text)
        readable = SteelMemory(self.home).readable("notes")
        self.assertEqual(len(readable), 2)

    def test_the_notes_grant_does_not_allow_the_room_tool(self) -> None:
        self.write_grants(grant_file(NOTES))
        result = self.call("memory_search_room", {"query": "folder"})
        self.assertFalse(result.success)
        self.assertIn("memory:read:room", result.content)
        entry = self.log_lines()[-1]
        self.assertEqual(
            (entry["capability"], entry["decision"]), ("memory:read:room", "deny")
        )

    def test_the_room_grant_does_not_allow_the_notes_or_facts_tool(self) -> None:
        self.write_grants(grant_file(ROOM))
        for name in ("memory_search_notes", "memory_search_facts"):
            self.assertFalse(self.call(name, {"query": "folder"}).success, name)

    def test_the_memory_read_wildcard_does_not_cover_room_notes(self) -> None:
        # A grants file written before Phase 4 must not start reading camera text.
        self.write_grants(grant_file(("memory:read:*", "*")))
        self.assertTrue(self.call("memory_search_notes", {"query": "folder"}).success)
        self.assertTrue(self.call("memory_search_facts", {"query": "folder"}).success)
        result = self.call("memory_search_room", {"query": "folder"})
        self.assertFalse(result.success)
        self.assertEqual(self.log_lines()[-1]["reason"], "no_grant")

    def test_a_deny_wildcard_still_covers_room_notes(self) -> None:
        policy = SteelPolicy()
        policy.grant("cli", "memory:read:room", "memory_search_room")
        policy.deny("cli", "memory:read:*")
        self.assertFalse(policy.check("cli", "memory:read:room", "memory_search_room"))

    def test_a_room_search_writes_nothing(self) -> None:
        self.write_grants(grant_file(ROOM))
        self.call("memory_search_room", {"query": "folder"})  # creates the log
        before = self.snapshot()
        self.call("memory_search_room", {"query": "folder"})
        after = self.snapshot()
        before.pop("steel_policy_audit.jsonl")
        after.pop("steel_policy_audit.jsonl")
        self.assertEqual(after, before)

    def test_an_old_database_without_sources_has_only_owner_notes(self) -> None:
        # Databases from before the source column: every row was the owner's.
        (self.home / "steel_notes.sqlite3").unlink()
        db = sqlite3.connect(self.home / "steel_notes.sqlite3")
        db.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, text TEXT, expires REAL)")
        db.execute(
            "INSERT INTO notes VALUES ('a1', 'Old folder note', ?)",
            (time.time() + 999,),
        )
        db.commit()
        db.close()
        raw = (self.home / "steel_notes.sqlite3").read_bytes()
        self.write_grants(grant_file(ROOM, NOTES))
        self.assertEqual(self.found("memory_search_room"), [NO_MATCHES])
        self.assertEqual(len(self.found("memory_search_notes")), 1)
        self.assertEqual((self.home / "steel_notes.sqlite3").read_bytes(), raw)


class RoomToolCliTest(RoomCase):
    def test_tool_list_shows_the_room_tool_and_its_capability(self) -> None:
        self.write_grants(grant_file(NOTES))
        out, _ = self.run_cli("tool", "list")
        self.assertIn("memory_search_room", out)
        self.assertIn("needs: memory:read:room", out)
        room = out.split("memory_search_room")[1]
        self.assertIn("agent cli: denied (no_grant)", room)

    def test_policy_show_lists_the_room_capability(self) -> None:
        out, _ = self.run_cli("policy", "show")
        self.assertIn("memory:read:room", out)

    def test_tool_call_runs_the_room_tool_with_its_grant(self) -> None:
        self.write_grants(grant_file(ROOM))
        out, _ = self.run_cli(
            "tool", "call", "memory_search_room", "--agent", "cli",
            "--args", '{"query": "folder"}',
        )  # fmt: skip
        self.assertIn("A blue folder on the desk", out)
