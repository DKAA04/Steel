"""Small SQLite note store with explicit persistence and expiry."""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Self

# Where a note came from. "user": the user stored it (steel remember --days,
# steel notes). "time_bound": the extractor found a short-lived fact in a chat
# message. "room": a camera description the owner saved (steel look --save). Kept
# apart so a grant can give a tool the room notes without the owner's own notes,
# or the other way round (memory:read:room and memory:read:notes).
NOTE_SOURCES = ("user", "time_bound", "room")


class MemoryStore:
    """Keep notes in RAM by default; callers must explicitly opt into disk storage."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        allow_persistence: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if path is not None and not allow_persistence:
            raise ValueError("Disk storage requires explicit allow_persistence=True.")
        if path is not None:
            if not path.parent.is_dir():
                raise ValueError(
                    "Create a private parent directory for the database first."
                )
            if path.is_symlink():
                raise ValueError("Use a regular database path, not a symbolic link.")
        self._clock = clock
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS notes "
            "(id TEXT PRIMARY KEY, text TEXT NOT NULL, expires REAL NOT NULL, "
            "source TEXT NOT NULL DEFAULT 'user')"
        )
        self._migrate()

    def _migrate(self) -> None:
        """Add the source column to a database created before it existed.

        Every older row was written by the user or by a chat extraction that
        had no label, so they all become "user" (the SQL default).
        """
        columns = [row[1] for row in self._db.execute("PRAGMA table_info(notes)")]
        if "source" in columns:
            return
        try:
            with self._db:
                self._db.execute(
                    "ALTER TABLE notes ADD COLUMN source TEXT NOT NULL DEFAULT 'user'"
                )
        except sqlite3.OperationalError:
            # Another process may have migrated between our check and the ALTER.
            columns = [row[1] for row in self._db.execute("PRAGMA table_info(notes)")]
            if "source" not in columns:
                raise

    def remember(
        self, text: str, *, ttl_hours: float = 24.0, source: str = "user"
    ) -> str:
        """Store a note for at most a week. Never log note content."""
        if source not in NOTE_SOURCES:
            raise ValueError(f"Note source must be one of {', '.join(NOTE_SOURCES)}.")
        if not text.strip() or len(text) > 2000:
            raise ValueError(
                "A note must contain 1–2000 characters and cannot be blank."
            )
        if not 0 < ttl_hours <= 168:
            raise ValueError(
                "Retention must be greater than zero and at most 168 hours."
            )
        note_id = uuid.uuid4().hex
        with self._db:
            self._db.execute(
                "INSERT INTO notes (id, text, expires, source) VALUES (?, ?, ?, ?)",
                (note_id, text.strip(), self._clock() + ttl_hours * 3600, source),
            )
        return note_id

    def recall(self, query: str = "") -> list[dict[str, str]]:
        """Search literal note text, excluding expired records."""
        self.purge_expired()
        rows = self._db.execute(
            "SELECT id, text FROM notes WHERE instr(lower(text), lower(?)) > 0 ORDER BY rowid",
            (query,),
        ).fetchall()
        return [{"id": note_id, "text": text} for note_id, text in rows]

    def entries(self) -> list[tuple[str, str, float, str]]:
        """Unexpired notes as (id, text, expiry unix time, source), oldest first."""
        self.purge_expired()
        return list(
            self._db.execute(
                "SELECT id, text, expires, source FROM notes ORDER BY rowid"
            )
        )

    def forget(self, note_id: str) -> bool:
        """Delete one explicitly identified note; return whether it existed."""
        with self._db:
            cursor = self._db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cursor.rowcount == 1

    def purge_expired(self) -> int:
        """Expiry is enforced on access, not by a background daemon."""
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM notes WHERE expires <= ?", (self._clock(),)
            )
        return cursor.rowcount

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def read_unexpired(
    path: Path,
    *,
    sources: tuple[str, ...] = NOTE_SOURCES,
    now: float | None = None,
) -> list[tuple[str, str]]:
    """(id, text) of the unexpired notes of *sources* in *path*, oldest first.

    Writes nothing. ``MemoryStore`` deletes expired rows and may add a column
    when it opens a database, so a read-only caller must not use it. This opens
    the file read-only (SQLite refuses any write on the connection) and skips
    expired rows instead of deleting them. A missing file has no notes; in a
    database older than the source column every row counts as "user", as
    ``MemoryStore._migrate`` would label it.
    """
    if path.is_symlink():
        raise ValueError("Use a regular database path, not a symbolic link.")
    if not path.exists():
        return []
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        columns = [row[1] for row in db.execute("PRAGMA table_info(notes)")]
        source = "source" if "source" in columns else "'user'"
        rows = db.execute(
            f"SELECT id, text, {source} FROM notes WHERE expires > ? ORDER BY rowid",
            (time.time() if now is None else now,),
        ).fetchall()
    finally:
        db.close()
    return [(note_id, text) for note_id, text, kind in rows if kind in sources]
