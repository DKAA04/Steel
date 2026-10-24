"""One interface over every store that can hold what the user told STEEL.

Stores STEEL reads and writes:
  f-  memory_facts.jsonl   facts extracted by the OpenJarvis MemoryService
  m-  MEMORY.md            OpenJarvis agent memory file (edited by hand today)
  u-  USER.md              OpenJarvis user profile file (edited by hand today)
  n-  steel_notes.sqlite3  STEEL's expiring notes (future room observations)

OpenJarvis databases STEEL never writes, but which can hold conversation text
when other OpenJarvis commands (``jarvis ask/serve/agent``...) run, so
``wipe`` deletes them too. See SECURITY.md for what each one contains.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from steel.facts import (
    FACT_PREFIX,
    NOTE_PREFIX,
    AddResult,
    SteelFactStore,
    fact_id,
)
from steel.home import FACTS_FILE, MEMORY_MD, NOTES_FILE, POLICY_AUDIT_FILE, USER_MD
from steel.memory_files import MemoryFile
from steel.notes import MemoryStore
from steel.security.audit import PolicyAuditLog
from steel.similarity import is_related

# Limits for `steel remember`.
MAX_REMEMBER_CHARS = 500
MAX_REMEMBER_DAYS = 7

# OpenJarvis SQLite files that can contain conversation text (pinned commit).
# name -> what text it can hold.
OPENJARVIS_CONTENT_DBS = {
    "traces.db": "raw queries and model results (disabled in config)",
    "sessions.db": "full chat messages of cross-channel sessions",
    "agents.db": "managed-agent messages, tasks and checkpoints",
    "memory.db": "document chunks indexed for retrieval",
    "audit.db": "security findings; boundary alerts keep an 80-char preview",
}
# SQLite side files that may hold recent pages of a database.
_SQLITE_SIDE_FILES = ("", "-wal", "-shm", "-journal")


def _when(unix_time: float) -> str:
    """Local date and time, minute precision, for ``steel memory list``."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(unix_time))


@dataclass(frozen=True)
class Entry:
    """One remembered item, as shown by ``steel memory list``."""

    id: str
    store: str
    text: str


@dataclass
class ForgetResult:
    """Outcome of ``SteelMemory.forget``."""

    removed: list[Entry]  # entries deleted (the target, plus neighbours if asked)
    related: list[Entry]  # related entries still stored (not removed)

    def __bool__(self) -> bool:
        return bool(self.removed)


class SteelMemory:
    """List, forget and wipe across all memory stores in one home directory."""

    def __init__(self, home: Path, *, max_facts: int = 1000) -> None:
        self.home = home
        self.notes_path = home / NOTES_FILE
        self.facts = SteelFactStore(
            home / FACTS_FILE, max_facts=max_facts, notes_path=self.notes_path
        )
        self.memory_md = MemoryFile(home / MEMORY_MD, "m-", "# Agent Memory\n\n")
        self.user_md = MemoryFile(home / USER_MD, "u-", "# User Profile\n\n")

    # -- notes helper ----------------------------------------------------

    def _notes(self) -> MemoryStore | None:
        """Open the notes DB only if it exists, so listing never creates it."""
        if not self.notes_path.exists():
            return None
        return MemoryStore(self.notes_path, allow_persistence=True)

    def notes_store(self) -> MemoryStore:
        """Open (creating if needed) the notes DB for writing."""
        return MemoryStore(self.notes_path, allow_persistence=True)

    # -- list / forget / wipe ---------------------------------------------

    def entries(self) -> list[Entry]:
        """Every entry in every store, each with an id that names its store."""
        entries = [
            Entry(fact_id(f.text), f"facts ({f.trust or 'legacy'})", f.text)
            for f in self.facts.list()
        ]
        entries += [Entry(i, "MEMORY.md", t) for i, t in self.memory_md.entries()]
        entries += [Entry(i, "USER.md", t) for i, t in self.user_md.entries()]
        notes = self._notes()
        if notes is not None:
            with notes:
                # recall("") returns all unexpired notes (and purges expired ones).
                entries += [
                    Entry(
                        NOTE_PREFIX + note_id,
                        f"note, {source} (expires {_when(expires)})",
                        text,
                    )
                    for note_id, text, expires, source in notes.entries()
                ]
        return entries

    def remember(self, text: str, *, days: float | None = None) -> AddResult:
        """``steel remember``: a trusted durable fact, or an expiring note.

        Raises ValueError for blank or over-long text, or for days outside
        (0, 7].
        """
        text = text.strip()
        if not text or len(text) > MAX_REMEMBER_CHARS:
            raise ValueError(f"Text must be 1-{MAX_REMEMBER_CHARS} characters.")
        if days is None:
            return self.facts.add_checked(text, source="user", explicit=True)
        if not 0 < days <= MAX_REMEMBER_DAYS:
            raise ValueError(f"--days must be above 0 and at most {MAX_REMEMBER_DAYS}.")
        return self.facts.add_note(text, ttl_hours=days * 24)

    def related(self, entry_id: str) -> list[Entry]:
        """Facts and notes that look related to this one (loose word overlap)."""
        everything = self.entries()
        target = next((e for e in everything if e.id == entry_id), None)
        if target is None or not entry_id.startswith((FACT_PREFIX, NOTE_PREFIX)):
            return []
        return [
            e
            for e in everything
            if e.id != entry_id
            and e.id.startswith((FACT_PREFIX, NOTE_PREFIX))
            and is_related(target.text, e.text)
        ]

    def forget(self, entry_id: str, *, with_related: bool = False) -> ForgetResult:
        """Delete one entry; with_related also deletes its related neighbours.

        Every forgotten fact or note has its hash added to the forgotten list,
        so the extractor cannot store the same text again.
        """
        target = next((e for e in self.entries() if e.id == entry_id), None)
        if target is None:
            return ForgetResult([], [])
        # Find neighbours BEFORE deleting: afterwards the target is gone.
        neighbours = self.related(entry_id)
        if not self._forget_one(target):
            return ForgetResult([], [])
        if not with_related:
            return ForgetResult([target], neighbours)
        removed = [target] + [n for n in neighbours if self._forget_one(n)]
        return ForgetResult(removed, [])

    def _forget_one(self, entry: Entry) -> bool:
        """Delete exactly one entry, chosen by the prefix of its id."""
        if entry.id.startswith(FACT_PREFIX):
            return self.facts.forget(entry.id)
        if entry.id.startswith("m-"):
            return self.memory_md.forget(entry.id)
        if entry.id.startswith("u-"):
            return self.user_md.forget(entry.id)
        if entry.id.startswith(NOTE_PREFIX):
            notes = self._notes()
            if notes is None:
                return False
            with notes:
                gone = notes.forget(entry.id[len(NOTE_PREFIX) :])
            if gone:
                # Notes can be re-extracted too: keep only the hash of the text.
                self.facts.mark_forgotten(entry.text)
            return gone
        return False

    def policy_log_path(self) -> Path:
        return self.home / POLICY_AUDIT_FILE

    def wipe(self, *, include_audit: bool = False) -> list[str]:
        """Empty every content store; return the names of what was removed.

        The permission-decision log (names and reason codes only, no text) is
        kept unless include_audit is True.
        """
        removed: list[str] = []
        # clear() also deletes the forgotten-hash list and the lock file.
        if self.facts.clear():
            removed.append(FACTS_FILE)
        for md in (self.memory_md, self.user_md):
            if md.wipe():
                removed.append(md.path.name)
        # Notes, and every OpenJarvis DB that can hold conversation text.
        for name in (NOTES_FILE, *OPENJARVIS_CONTENT_DBS):
            for suffix in _SQLITE_SIDE_FILES:
                path = self.home / (name + suffix)
                if path.exists():
                    path.unlink()
                    removed.append(path.name)
        if include_audit:
            # The log and its rotated files (.1 to .3).
            removed += PolicyAuditLog(self.policy_log_path()).remove_all()
        return removed

    # -- audit -------------------------------------------------------------

    def audit(self) -> dict[str, str]:
        """State of every content store: 'empty', 'absent' or what is left.

        ``steel wipe`` is judged by this: everything must be empty or absent.
        """
        report: dict[str, str] = {}
        n_facts = self.facts.count()
        report[FACTS_FILE] = f"{n_facts} fact(s)" if n_facts else "empty"
        n_forgotten = len(self.facts.forgotten_ids())
        report[self.facts.forgotten_path.name] = (
            f"{n_forgotten} hash(es)" if n_forgotten else "empty"
        )
        for md in (self.memory_md, self.user_md):
            n = len(md.entries())
            report[md.path.name] = f"{n} entr(y/ies)" if n else "empty"
        notes = self._notes()
        if notes is None:
            report[NOTES_FILE] = "absent"
        else:
            with notes:
                n = len(notes.recall(""))
            report[NOTES_FILE] = f"{n} note(s)" if n else "empty"
        for name in OPENJARVIS_CONTENT_DBS:
            present = [
                name + s
                for s in _SQLITE_SIDE_FILES
                if (self.home / (name + s)).exists()
            ]
            report[name] = "present: " + ", ".join(present) if present else "absent"
        return report

    def policy_log_state(self) -> str:
        """State of the permission-decision log (not part of is_empty)."""
        log = PolicyAuditLog(self.policy_log_path())
        rotated = len(log.rotated_files())
        if not self.policy_log_path().exists() and not rotated:
            return "absent"
        state = f"{log.count()} decision(s)"
        return f"{state}, {rotated} rotated file(s)" if rotated else state

    def is_empty(self) -> bool:
        return all(state in ("empty", "absent") for state in self.audit().values())
