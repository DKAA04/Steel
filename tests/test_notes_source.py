"""The notes store records where each note came from (user / time_bound / room)."""

from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from steel.cli import main
from steel.memory import SteelMemory
from steel.notes import NOTE_SOURCES, MemoryStore

# The table exactly as it was before the source column existed.
OLD_SCHEMA = (
    "CREATE TABLE notes "
    "(id TEXT PRIMARY KEY, text TEXT NOT NULL, expires REAL NOT NULL)"
)


class NoteSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "notes.sqlite3"

    def test_source_is_stored_and_defaults_to_user(self) -> None:
        with MemoryStore(self.path, allow_persistence=True) as store:
            store.remember("a")
            store.remember("b", source="time_bound")
            store.remember("c", source="room")
            self.assertEqual(
                [(t, s) for _i, t, _e, s in store.entries()],
                [("a", "user"), ("b", "time_bound"), ("c", "room")],
            )

    def test_unknown_source_is_rejected_and_nothing_is_stored(self) -> None:
        with MemoryStore() as store:
            for bad in ["", "USER", "sensor", "camera"]:
                with self.assertRaises(ValueError):
                    store.remember("x", source=bad)
            self.assertEqual(store.entries(), [])
        self.assertEqual(NOTE_SOURCES, ("user", "time_bound", "room"))

    def test_old_database_is_migrated_and_old_rows_become_user(self) -> None:
        db = sqlite3.connect(self.path)
        db.execute(OLD_SCHEMA)
        db.execute("INSERT INTO notes VALUES ('old1', 'old note', 9999999999)")
        db.commit()
        db.close()
        with MemoryStore(self.path, allow_persistence=True) as store:
            [(note_id, text, _exp, source)] = store.entries()
            self.assertEqual((note_id, text, source), ("old1", "old note", "user"))
            store.remember("new", source="room")  # the migrated table accepts writes
        # Opening it a second time must not try to migrate again.
        with MemoryStore(self.path, allow_persistence=True) as store:
            self.assertEqual([s for *_rest, s in store.entries()], ["user", "room"])

    def test_chat_extracted_note_is_time_bound_and_remember_days_is_user(self) -> None:
        from steel.facts import SteelFactStore

        facts = SteelFactStore(
            Path(self._tmp.name) / "facts.jsonl", notes_path=self.path
        )
        facts.add("Mara is visiting next weekend")  # extractor path
        facts.add_note("Dentist on the 3rd", ttl_hours=48)  # steel remember --days
        with MemoryStore(self.path, allow_persistence=True) as store:
            self.assertEqual(
                [(t, s) for _i, t, _e, s in store.entries()],
                [
                    ("Mara is visiting next weekend", "time_bound"),
                    ("Dentist on the 3rd", "user"),
                ],
            )

    def test_memory_list_shows_the_source(self) -> None:
        import os
        from unittest import mock

        home = Path(self._tmp.name) / "oj"
        home.mkdir()
        with mock.patch.dict(os.environ, {"OPENJARVIS_HOME": str(home)}):
            memory = SteelMemory(home)
            memory.facts.add("Mara is visiting next weekend")
            memory.remember("Dentist on the 3rd", days=2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["memory", "list"]), 0)
        text = out.getvalue()
        self.assertIn("note, time_bound (expires", text)
        self.assertIn("note, user (expires", text)


if __name__ == "__main__":
    unittest.main()
