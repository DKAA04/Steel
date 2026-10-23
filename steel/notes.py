"""Small SQLite note store with explicit persistence and expiry."""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Self


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
            "(id TEXT PRIMARY KEY, text TEXT NOT NULL, expires REAL NOT NULL)"
        )

    def remember(self, text: str, *, ttl_hours: float = 24.0) -> str:
        """Store a note for at most a week. Never log note content."""
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
                "INSERT INTO notes VALUES (?, ?, ?)",
                (note_id, text.strip(), self._clock() + ttl_hours * 3600),
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
