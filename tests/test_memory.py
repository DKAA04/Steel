import tempfile
import unittest
from pathlib import Path

from core.__main__ import main
from memory.store import MemoryStore


class MemoryTests(unittest.TestCase):
    def test_new_store_does_not_retain_previous_session(self):
        with MemoryStore() as first:
            first.remember("synthetic note")
        with MemoryStore() as second:
            self.assertEqual(second.recall(), [])

    def test_persistence_requires_consent_before_file_creation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notes.sqlite3"
            with self.assertRaises(ValueError):
                MemoryStore(path)
            self.assertFalse(path.exists())

    def test_opted_in_note_survives_reopen_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notes.sqlite3"
            with MemoryStore(path, allow_persistence=True) as store:
                note = store.remember("Synthetic sensor check")
            with MemoryStore(path, allow_persistence=True) as store:
                self.assertEqual(store.recall("SENSOR")[0]["id"], note)
                self.assertTrue(store.forget(note))
                self.assertFalse(store.forget(note))
                self.assertEqual(store.recall(), [])

    def test_expiry_boundary(self):
        now = [100.0]
        with MemoryStore(clock=lambda: now[0]) as store:
            store.remember("expires", ttl_hours=1)
            now[0] = 3699.0
            self.assertEqual(len(store.recall()), 1)
            now[0] = 3700.0
            self.assertEqual(store.recall(), [])

    def test_query_is_literal_and_cannot_inject_sql(self):
        with MemoryStore() as store:
            store.remember("100% synthetic")
            self.assertEqual(store.recall("' OR 1=1 --"), [])
            self.assertEqual(len(store.recall("%")), 1)

    def test_invalid_notes_and_retention(self):
        with MemoryStore() as store:
            for note in ["", "  ", "x" * 2001]:
                with self.assertRaises(ValueError):
                    store.remember(note)
            for ttl in [0, -1, 169, float("nan"), float("inf")]:
                with self.assertRaises(ValueError):
                    store.remember("synthetic", ttl_hours=ttl)

    def test_cli_refuses_forget_without_confirmation(self):
        with self.assertRaises(SystemExit) as error:
            main(["--db", "unused.sqlite3", "--allow-persistence", "forget", "dummy"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
