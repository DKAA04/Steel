"""Phase 1 tests: persistent memory, forget, wipe, permissions, file audit.

No test calls a live model: FakeEngine answers both chat turns and the
MemoryService's extraction requests. Each test gets its own temporary
OpenJarvis home via $OPENJARVIS_HOME.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from openjarvis.core.config import JarvisConfig

from steel.chat import ChatSession, open_session
from steel.cli import main
from steel.facts import SteelFactStore, fact_id
from steel.home import harden
from steel.memory import SteelMemory
from steel.pin import PinError, check_openjarvis_pin

# Invented facts the fake extractor "finds" in a user message.
FAKE_FACTS = {
    "teal": ["User's favourite colour is teal"],
    "Rotterdam": ["User lives in Rotterdam"],
}
EXTRACTOR_MARKER = "You extract durable"  # start of OpenJarvis' extractor prompt


class FakeEngine:
    """Stands in for Ollama. Records every prompt it is given."""

    engine_id = "fake"

    def __init__(self) -> None:
        self.chat_prompts: list[str] = []

    def generate(self, messages: Any, *, model: str, **_: Any) -> dict[str, Any]:
        system = messages[0].content if messages else ""
        user = messages[-1].content
        if system.startswith(EXTRACTOR_MARKER):
            # Extraction call: return the facts keyed by a word in the message.
            found = [
                f for key, facts in FAKE_FACTS.items() if key in user for f in facts
            ]
            return {"content": json.dumps(found), "usage": {}}
        # Chat call: remember the full system prompt so tests can inspect it.
        self.chat_prompts.append(system)
        return {"content": "Noted.", "usage": {}}

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["fake"]


class HomeTestCase(unittest.TestCase):
    """Gives each test a private temporary OpenJarvis home."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "oj"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"OPENJARVIS_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        # harden() changes the process umask; restore it afterwards.
        old_umask = os.umask(0o022)
        os.umask(old_umask)
        self.addCleanup(os.umask, old_umask)
        # OpenJarvis creates these two files on first run.
        (self.home / "MEMORY.md").write_text("# Agent Memory\n\n")
        (self.home / "USER.md").write_text("# User Profile\n\n")
        self.addCleanup(self._tmp.cleanup)

    def config(self) -> JarvisConfig:
        # Built after OPENJARVIS_HOME is set, so every default path is in home.
        cfg = JarvisConfig()
        cfg.memory.enabled = True
        cfg.memory.extraction_model = "fake"
        cfg.intelligence.default_model = "fake"
        cfg.traces.enabled = False
        cfg.analytics.enabled = False
        return cfg

    def session(self, engine: FakeEngine | None = None) -> ChatSession:
        return open_session(self.config(), self.home, engine=engine or FakeEngine())


class SuccessTest(HomeTestCase):
    def test_tell_restart_recall_forget_wipe(self) -> None:
        # 1. Tell STEEL a fact, then quit (close waits for extraction).
        first = self.session()
        first.ask("My favourite colour is teal.")
        first.close()

        # 2. "Restart": a brand-new session and store read it back from disk.
        engine = FakeEngine()
        second = self.session(engine)
        second.ask("What is my favourite colour?")
        second.close()
        self.assertIn("User's favourite colour is teal", engine.chat_prompts[-1])
        memory = SteelMemory(self.home)
        [entry] = memory.entries()
        self.assertEqual(entry.text, "User's favourite colour is teal")

        # 3. Forget that one fact and confirm it is gone everywhere.
        self.assertTrue(memory.forget(entry.id))
        self.assertEqual(memory.entries(), [])
        self.assertNotIn("teal", (self.home / "memory_facts.jsonl").read_text())
        engine = FakeEngine()
        third = self.session(engine)
        third.ask("What is my favourite colour?")
        third.close()
        self.assertNotIn("teal", engine.chat_prompts[-1])

        # 4. Put something in every store, wipe, and confirm all are empty.
        fourth = self.session()
        fourth.ask("I live in Rotterdam.")
        fourth.close()
        with (self.home / "MEMORY.md").open("a") as f:
            f.write("- Synthetic agent note\n")
        with (self.home / "USER.md").open("a") as f:
            f.write("- Synthetic profile line\n")
        with memory.notes_store() as notes:
            notes.remember("Synthetic room observation")
        self.assertEqual(len(memory.entries()), 4)
        memory.wipe()
        self.assertEqual(memory.entries(), [])
        self.assertTrue(memory.is_empty(), memory.audit())


class ForgetTest(HomeTestCase):
    def test_forgotten_fact_is_not_re_extracted_verbatim(self) -> None:
        store = SteelFactStore(self.home / "memory_facts.jsonl")
        store.add("User lives in Rotterdam")
        fid = fact_id("User lives in Rotterdam")
        self.assertTrue(store.forget(fid))
        self.assertFalse(store.forget(fid))  # already gone
        # Exact repeat (even with different case/spacing) is refused...
        self.assertFalse(store.add("  user LIVES in rotterdam "))
        # ...but a reworded fact is new text and is stored (documented limit).
        self.assertTrue(store.add("The user's home city is Rotterdam"))
        # The forgotten list holds only the hash, never the text.
        raw = store.forgotten_path.read_text()
        self.assertIn(fid, raw)
        self.assertNotIn("Rotterdam", raw)
        # A fresh instance (restart) still refuses the forgotten fact.
        again = SteelFactStore(self.home / "memory_facts.jsonl")
        self.assertFalse(again.add("User lives in Rotterdam"))
        # Wipe clears the list, so the fact may be learned again.
        again.clear()
        self.assertFalse(again.forgotten_path.exists())
        self.assertTrue(again.add("User lives in Rotterdam"))

    def test_memory_md_forget_removes_one_exact_line(self) -> None:
        (self.home / "MEMORY.md").write_text(
            "# Agent Memory\n\n- likes tea\n- likes tea with milk\n"
        )
        memory = SteelMemory(self.home)
        ids = {e.text: e.id for e in memory.entries()}
        self.assertTrue(memory.forget(ids["- likes tea"]))
        # memory_manage would have removed both lines; we keep the other one.
        self.assertEqual(
            (self.home / "MEMORY.md").read_text(),
            "# Agent Memory\n\n- likes tea with milk\n",
        )

    def test_unknown_id_is_rejected(self) -> None:
        memory = SteelMemory(self.home)
        for bad in ["f-000000000000", "m-nothing", "n-nothing", "x-1", ""]:
            self.assertFalse(memory.forget(bad))


class WipeTest(HomeTestCase):
    def test_wipe_deletes_openjarvis_content_databases(self) -> None:
        for name in [
            "traces.db",
            "sessions.db",
            "agents.db",
            "memory.db",
            "audit.db-wal",
        ]:
            (self.home / name).write_bytes(b"synthetic")
        (self.home / "telemetry.db").write_bytes(b"metrics only")
        memory = SteelMemory(self.home)
        self.assertFalse(memory.is_empty())
        memory.wipe()
        self.assertTrue(memory.is_empty(), memory.audit())
        # telemetry.db holds token counts and timings, no text: kept.
        self.assertTrue((self.home / "telemetry.db").exists())

    def test_cli_wipe_requires_confirm(self) -> None:
        with self.assertRaises(SystemExit) as error:
            main(["wipe"])
        self.assertEqual(error.exception.code, 2)

    def test_cli_wipe_and_list(self) -> None:
        SteelFactStore(self.home / "memory_facts.jsonl").add("Synthetic fact")
        self.assertEqual(main(["wipe", "--confirm"]), 0)
        self.assertEqual(SteelMemory(self.home).entries(), [])


class PermissionTest(HomeTestCase):
    def test_harden_sets_700_and_600(self) -> None:
        self.home.chmod(0o755)
        loose = self.home / "config.toml"
        loose.write_text("")
        loose.chmod(0o644)
        outside = Path(self._tmp.name) / "outside.txt"
        outside.write_text("")
        outside.chmod(0o644)
        (self.home / "link").symlink_to(outside)
        harden(self.home)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(loose.stat().st_mode), 0o600)
        # A symlink must not let harden() change a file outside the home.
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o644)

    def test_files_created_after_harden_are_private(self) -> None:
        harden(self.home)
        session = self.session()
        session.ask("My favourite colour is teal.")
        session.close()
        for path in self.home.iterdir():
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)


def _snapshot(root: Path) -> dict[str, str]:
    """Map every file under root to a hash of its bytes."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# Every file a STEEL chat session may create or change, and why.
KNOWN_CHANGED_FILES = {
    "memory_facts.jsonl": "extracted facts (memory store, wiped)",
    "memory_facts.jsonl.lock": "empty cross-process lock file (wiped)",
    "audit.db": "OpenJarvis security audit: pattern names, no text (wiped)",
    "audit.db-wal": "SQLite write-ahead log of audit.db (wiped)",
    "audit.db-shm": "SQLite shared-memory index of audit.db (wiped)",
}


class FileAuditTest(HomeTestCase):
    def test_every_file_a_chat_session_touches_is_known(self) -> None:
        before = _snapshot(self.home)
        session = self.session()
        session.ask("My favourite colour is teal.")
        # Contains an invented e-mail address, to trigger the PII guardrail.
        session.ask("Write to synthetic.person@example.invalid about Rotterdam.")
        session.close()
        after = _snapshot(self.home)
        changed = {name for name, digest in after.items() if before.get(name) != digest}
        unknown = changed - set(KNOWN_CHANGED_FILES)
        self.assertEqual(unknown, set(), f"unexplained files: {sorted(unknown)}")
        self.assertIn("memory_facts.jsonl", changed)

        # The audit log must not contain the conversation text itself.
        # The guardrail did log the e-mail finding, but only by pattern name.
        with sqlite3.connect(self.home / "audit.db") as db:
            dump = "\n".join(db.iterdump())
        self.assertIn('"pattern_name": "email"', dump)
        for secret in ["synthetic.person", "teal", "Rotterdam"]:
            self.assertNotIn(secret, dump)

        # After wipe, no file that can hold conversation content is left.
        SteelMemory(self.home).wipe()
        leftover = set(_snapshot(self.home)) - {"MEMORY.md", "USER.md"}
        self.assertEqual(leftover, set())
        self.assertTrue(SteelMemory(self.home).is_empty())


class PinTest(unittest.TestCase):
    def test_pin(self) -> None:
        check_openjarvis_pin()  # the installed build is the pinned one
        with self.assertRaises(PinError):
            check_openjarvis_pin("1.0.6")


if __name__ == "__main__":
    unittest.main()
