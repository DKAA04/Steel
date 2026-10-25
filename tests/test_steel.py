"""Phase 1 tests: persistent memory, forget, wipe, permissions, file audit.

No test calls a live model: FakeEngine answers both chat turns and the
MemoryService's extraction requests. Each test gets its own temporary
OpenJarvis home via $OPENJARVIS_HOME.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from openjarvis.core.config import JarvisConfig

from steel.chat import ChatSession, open_session
from steel.cli import main
from steel.extractor import SYSTEM_PROMPT as EXTRACTOR_PROMPT
from steel.facts import SteelFactStore, fact_id
from steel.home import harden
from steel.memory import SteelMemory
from steel.notes import MemoryStore
from steel.pin import PinError, check_openjarvis_pin

# Invented facts the fake extractor "finds" in a user message.
FAKE_FACTS = {
    "teal": ["User's favourite colour is teal"],
    "Rotterdam": ["User lives in Rotterdam"],
    "Mara": ["Mara is visiting next weekend"],
    "Lucía": ["Lucía viene mañana a cenar"],
}


class FakeEngine:
    """Stands in for Ollama. Records every prompt it is given."""

    engine_id = "fake"

    def __init__(self) -> None:
        self.chat_prompts: list[str] = []
        self.extractor_inputs: list[str] = []

    def generate(self, messages: Any, *, model: str, **_: Any) -> dict[str, Any]:
        system = messages[0].content if messages else ""
        user = messages[-1].content
        if system == EXTRACTOR_PROMPT:
            self.extractor_inputs.append(user)
            # Extraction call: return the facts keyed by a word in the message.
            found = [
                f for key, facts in FAKE_FACTS.items() if key in user for f in facts
            ]
            return {"content": json.dumps(found), "usage": {}}
        # Chat call: remember the full system prompt so tests can inspect it.
        self.chat_prompts.append(system)
        # Like a real model, repeat remembered facts back to the user.
        if "teal" in system:
            return {"content": "You like teal. Rotterdam is nice too.", "usage": {}}
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
        # steel doctor reads /proc/meminfo; tests read a file that is not there,
        # so no test depends on (or reads) the real machine's memory state.
        meminfo = mock.patch("steel.meminfo.MEMINFO", self.home.parent / "meminfo")
        meminfo.start()
        self.addCleanup(meminfo.stop)
        # The same for the kernel's list of audio devices (steel voice check).
        pcm = mock.patch("steel.voice.commands.PCM_LIST", self.home.parent / "pcm")
        pcm.start()
        self.addCleanup(pcm.stop)
        # And for the speech engine: never look at the real ~/.local/share/steel.
        piper = mock.patch(
            "steel.speak.synth.default_engine_dir",
            return_value=self.home.parent / "piper",
        )
        piper.start()
        self.addCleanup(piper.stop)
        clean = mock.patch.dict(os.environ)
        clean.start()
        self.addCleanup(clean.stop)
        os.environ.pop("STEEL_PIPER_DIR", None)
        os.environ.pop("STEEL_SPEAK_MAX_CHARS", None)
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


class UserOnlyExtractionTest(HomeTestCase):
    def test_assistant_text_never_reaches_the_extractor(self) -> None:
        engine = FakeEngine()
        session = self.session(engine)
        session.ask("My favourite colour is teal.")
        session.ask("What is my favourite colour?")
        session.close()
        self.assertEqual(len(engine.extractor_inputs), 2)
        for sent in engine.extractor_inputs:
            self.assertNotIn("Assistant:", sent)
            self.assertNotIn("Noted.", sent)
            self.assertNotIn("Rotterdam is nice", sent)

    def test_a_question_adds_no_facts(self) -> None:
        # The second reply says "You like teal. Rotterdam is nice too." If the
        # reply reached the extractor, the fake would invent a Rotterdam fact.
        session = self.session()
        session.ask("My favourite colour is teal.")
        session.ask("What is my favourite colour?")
        session.close()
        texts = [e.text for e in SteelMemory(self.home).entries()]
        self.assertEqual(texts, ["User's favourite colour is teal"])


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


class TimeBoundRoutingTest(HomeTestCase):
    def test_time_bound_fact_goes_to_notes_with_a_7_day_expiry(self) -> None:
        session = self.session()
        session.ask("My sister Mara is visiting next weekend.")
        session.ask("I live in Rotterdam.")
        session.close()
        memory = SteelMemory(self.home)
        # The durable file only holds the durable fact.
        self.assertEqual(
            [f.text for f in memory.facts.list()], ["User lives in Rotterdam"]
        )
        notes = [e for e in memory.entries() if e.id.startswith("n-")]
        self.assertEqual([n.text for n in notes], ["Mara is visiting next weekend"])
        self.assertIn("expires", notes[0].store)
        with memory.notes_store() as store:
            [(_id, _text, expires, source)] = store.entries()
        self.assertAlmostEqual(expires - time.time(), 7 * 86400, delta=60)
        self.assertEqual(source, "time_bound")

    def test_spanish_time_bound_fact_is_routed_too(self) -> None:
        session = self.session()
        session.ask("Lucía viene mañana a cenar.")
        session.close()
        memory = SteelMemory(self.home)
        self.assertEqual(memory.facts.count(), 0)
        self.assertEqual(
            [e.text for e in memory.entries()], ["Lucía viene mañana a cenar"]
        )

    def test_notes_reach_the_prompt_and_repeats_are_not_duplicated(self) -> None:
        first = self.session()
        first.ask("My sister Mara is visiting next weekend.")
        first.close()
        engine = FakeEngine()
        second = self.session(engine)
        second.ask("My sister Mara is visiting next weekend.")  # same fact again
        second.ask("When is Mara coming?")
        second.close()
        self.assertIn("Mara is visiting next weekend", engine.chat_prompts[-1])
        self.assertEqual(len(SteelMemory(self.home).entries()), 1)

    def test_forgotten_note_is_not_re_extracted(self) -> None:
        session = self.session()
        session.ask("My sister Mara is visiting next weekend.")
        session.close()
        memory = SteelMemory(self.home)
        [entry] = memory.entries()
        self.assertTrue(memory.forget(entry.id))
        again = self.session()
        again.ask("My sister Mara is visiting next weekend.")
        again.close()
        self.assertEqual(SteelMemory(self.home).entries(), [])

    def test_expired_note_disappears_from_list(self) -> None:
        memory = SteelMemory(self.home)
        # A note written 8 days ago with the 7-day TTL has already expired.
        eight_days_ago = lambda: time.time() - 8 * 86400
        with MemoryStore(
            memory.notes_path, allow_persistence=True, clock=eight_days_ago
        ) as notes:
            notes.remember("Mara is visiting next weekend", ttl_hours=168)
        self.assertEqual(memory.entries(), [])


class ForgetRelatedTest(HomeTestCase):
    FACTS: ClassVar[list[str]] = [
        "User's favorite tea is genmaicha",
        "User enjoys genmaicha with a roasted rice flavor",
        "User lives in Rotterdam",
    ]

    def setUp(self) -> None:
        super().setUp()
        self.memory = SteelMemory(self.home)
        for text in self.FACTS:
            self.memory.facts.add(text)

    def test_default_removes_only_the_target_and_lists_neighbours(self) -> None:
        result = self.memory.forget(fact_id(self.FACTS[0]))
        self.assertEqual([e.text for e in result.removed], [self.FACTS[0]])
        self.assertEqual([e.text for e in result.related], [self.FACTS[1]])
        texts = [e.text for e in self.memory.entries()]
        self.assertEqual(texts, self.FACTS[1:])

    def test_with_related_removes_neighbours_and_hashes_them(self) -> None:
        result = self.memory.forget(fact_id(self.FACTS[0]), with_related=True)
        self.assertEqual([e.text for e in result.removed], self.FACTS[:2])
        self.assertEqual([e.text for e in self.memory.entries()], self.FACTS[2:])
        # Both removed facts are blocked from coming back verbatim...
        for text in self.FACTS[:2]:
            self.assertFalse(self.memory.facts.add(text))
        # ...and the forgotten list holds hashes only.
        raw = self.memory.facts.forgotten_path.read_text()
        self.assertNotIn("genmaicha", raw)
        self.assertEqual(len(self.memory.facts.forgotten_ids()), 2)

    def test_cli_lists_neighbours_and_needs_flag_to_remove_them(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["forget", fact_id(self.FACTS[0])]), 0)
        self.assertIn("--with-related", out.getvalue())
        self.assertIn(self.FACTS[1], out.getvalue())
        self.assertEqual(len(SteelMemory(self.home).entries()), 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["forget", fact_id(self.FACTS[1]), "--with-related"]), 0
            )
        self.assertEqual(len(SteelMemory(self.home).entries()), 1)

    def test_unknown_id_removes_nothing(self) -> None:
        result = self.memory.forget("f-000000000000", with_related=True)
        self.assertFalse(result)
        self.assertEqual(len(self.memory.entries()), 3)


class RememberTest(HomeTestCase):
    def run_cli(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(list(argv)), 0)
        return out.getvalue()

    def test_remember_stores_a_trusted_durable_fact(self) -> None:
        text = self.run_cli("remember", "User is vegetarian")
        self.assertIn("(trusted)", text)
        [fact] = SteelMemory(self.home).facts.list()
        self.assertEqual(fact.trust, "trusted")
        self.assertEqual(fact.source, "user")
        listing = self.run_cli("memory", "list")
        self.assertIn("facts (trusted)", listing)

    def test_remember_ignores_time_words_because_the_user_chose_durable(self) -> None:
        self.run_cli("remember", "I run every day, today and tomorrow")
        memory = SteelMemory(self.home)
        self.assertEqual(memory.facts.count(), 1)

    def test_remember_days_stores_an_expiring_note(self) -> None:
        text = self.run_cli("remember", "Dentist appointment on the 3rd", "--days", "2")
        self.assertIn("n-", text)
        [entry] = SteelMemory(self.home).entries()
        self.assertTrue(entry.id.startswith("n-"))
        self.assertIn("expires", entry.store)
        with SteelMemory(self.home).notes_store() as notes:
            [(_id, _text, expires, source)] = notes.entries()
        self.assertAlmostEqual(expires - time.time(), 2 * 86400, delta=60)
        self.assertEqual(source, "user")

    def test_days_must_be_between_0_and_7(self) -> None:
        for bad in ["0", "-1", "7.5", "8"]:
            with self.assertRaises(SystemExit) as error:
                main(["remember", "x y z", "--days", bad])
            self.assertEqual(error.exception.code, 2, bad)
        self.assertEqual(SteelMemory(self.home).entries(), [])
        self.run_cli("remember", "ok within a week", "--days", "7")

    def test_blank_and_overlong_text_are_rejected(self) -> None:
        for bad in ["   ", "x" * 501]:
            with self.assertRaises(SystemExit):
                main(["remember", bad])

    def test_near_duplicate_is_reported_not_stored(self) -> None:
        self.run_cli("remember", "User has a peanut allergy")
        text = self.run_cli("remember", "user has peanut allergy.")
        self.assertIn("Already remembered as f-", text)
        self.assertEqual(SteelMemory(self.home).facts.count(), 1)

    def test_remembered_entries_work_with_list_forget_and_wipe(self) -> None:
        self.run_cli("remember", "User is vegetarian")
        self.run_cli("remember", "Call the plumber", "--days", "3")
        memory = SteelMemory(self.home)
        self.assertEqual(len(memory.entries()), 2)
        fact, note = memory.entries()
        self.assertTrue(memory.forget(fact.id))
        self.assertTrue(memory.forget(note.id))
        self.assertEqual(memory.entries(), [])
        self.run_cli("remember", "User is vegetarian")
        self.run_cli("remember", "Call the plumber", "--days", "3")
        self.run_cli("wipe", "--confirm")
        self.assertTrue(SteelMemory(self.home).is_empty())

    def test_explicit_remember_overrides_an_earlier_forget(self) -> None:
        memory = SteelMemory(self.home)
        memory.facts.add("User lives in Rotterdam")
        memory.forget(fact_id("User lives in Rotterdam"))
        self.assertFalse(
            memory.facts.add("User lives in Rotterdam")
        )  # extractor: blocked
        self.run_cli("remember", "User lives in Rotterdam")  # the user: allowed
        self.assertEqual(memory.facts.count(), 1)
        self.assertNotIn(
            fact_id("User lives in Rotterdam"), memory.facts.forgotten_ids()
        )


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
    "steel_notes.sqlite3": "expiring notes for time-bound facts (wiped)",
}


class FileAuditTest(HomeTestCase):
    def test_every_file_a_chat_session_touches_is_known(self) -> None:
        before = _snapshot(self.home)
        session = self.session()
        session.ask("My favourite colour is teal.")
        # Contains an invented e-mail address, to trigger the PII guardrail.
        session.ask("Write to synthetic.person@example.invalid about Rotterdam.")
        session.ask("My sister Mara is visiting next weekend.")  # time-bound
        session.close()
        after = _snapshot(self.home)
        changed = {name for name, digest in after.items() if before.get(name) != digest}
        unknown = changed - set(KNOWN_CHANGED_FILES)
        self.assertEqual(unknown, set(), f"unexplained files: {sorted(unknown)}")
        self.assertIn("memory_facts.jsonl", changed)
        self.assertIn("steel_notes.sqlite3", changed)

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
