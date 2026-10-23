"""SteelFactStore: OpenJarvis' LocalFactStore plus forget-by-id.

Upstream ``LocalFactStore`` can only ``clear()`` everything. STEEL needs to
forget one fact at a time, so this subclass adds:

- ``fact_id(text)``: a stable id derived from the fact text, so the same fact
  has the same id after a restart without changing the JSONL file format;
- ``forget(fact_id)``: remove exactly that fact;
- a "forgotten" list of fact-id hashes (never the text) that ``add`` checks,
  so the extractor cannot store the very same fact again.

Near-duplicates of an existing fact are skipped (steel/similarity.py).
Facts containing a short-term time expression ("tomorrow", "este fin de
semana"; steel/timewords.py) are routed to the expiring notes store with a
7-day TTL instead of the durable facts file.

The forgotten list only blocks *exact* repeats (same text ignoring case and
surrounding spaces). A reworded version of a forgotten fact is new text with a
new hash and will be stored.

WARNING: ``forget`` uses private members of LocalFactStore (``_lock``,
``_lock_path``, ``_sync_from_disk_locked``, ``_facts``, ``_flush``) and the
module-private ``_cross_process_lock``. They are only checked against OpenJarvis
commit 108d257188cbad85f2e50209d9a61669ea4dec33 (see steel/pin.py). Re-review
this file before changing the pin.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openjarvis.memory.store import (
    RECALLABLE_TRUST_TIERS,
    Fact,
    LocalFactStore,
    _cross_process_lock,
)

from steel.home import FORGOTTEN_FILE
from steel.notes import MemoryStore
from steel.similarity import is_near_duplicate
from steel.timewords import is_time_bound

FACT_PREFIX = "f-"
NOTE_PREFIX = "n-"
# Time-bound facts expire after this many hours (7 days = the notes maximum).
TIME_BOUND_TTL_HOURS = 168.0


def fact_id(text: str) -> str:
    """Return the stable id of a fact: 'f-' + 12 hex chars of SHA-256.

    LocalFactStore already treats facts that differ only by case as duplicates,
    so normalising the same way makes the id unique within one store.
    """
    normalised = text.strip().lower()
    return FACT_PREFIX + hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class AddResult:
    """What happened to a fact offered to the store."""

    # "stored" (durable fact), "note" (expiring note), "blank", "forgotten"
    # or "duplicate".
    outcome: str
    # "stored"/"note": id of the new entry. "duplicate": id of the existing one.
    entry_id: str = ""

    @property
    def stored(self) -> bool:
        return self.outcome in ("stored", "note")


class SteelFactStore(LocalFactStore):
    """LocalFactStore that can forget one fact and refuses forgotten repeats."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_facts: int = 1000,
        forgotten_path: str | Path | None = None,
        notes_path: str | Path | None = None,
    ) -> None:
        # Let upstream resolve the JSONL path and load existing facts.
        super().__init__(path, max_facts=max_facts)
        # Keep the forgotten list next to the facts file by default.
        self._forgotten_path = (
            Path(forgotten_path)
            if forgotten_path is not None
            else self._path.with_name(FORGOTTEN_FILE)
        )
        # Where time-bound facts go. None = no routing (everything is durable).
        self._notes_path = Path(notes_path) if notes_path is not None else None

    # -- forgotten list (hashes only) -------------------------------------

    def forgotten_ids(self) -> set[str]:
        """Return the set of forgotten fact ids (empty if the file is absent)."""
        try:
            data = json.loads(self._forgotten_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        # Accept only a JSON list of strings; anything else counts as empty.
        if not isinstance(data, list):
            return set()
        return {item for item in data if isinstance(item, str)}

    def _write_forgotten(self, ids: set[str]) -> None:
        """Atomically replace the forgotten list (temp file + rename)."""
        self._forgotten_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._forgotten_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(sorted(ids), handle)
            os.replace(tmp, self._forgotten_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- FactStore API ----------------------------------------------------

    def add(self, text: str, source: str = "", trust: str = "") -> bool:
        """Store a fact unless it is blank, forgotten or a near-duplicate."""
        # Upstream add_with_trust / add_many* all funnel through add().
        return self.add_checked(text, source, trust).stored

    def add_checked(self, text: str, source: str = "", trust: str = "") -> AddResult:
        """Like add(), but says why a fact was not stored."""
        text = (text or "").strip()
        if not text:
            return AddResult("blank")
        # A forgotten fact (same text, ignoring case) is never stored again.
        if fact_id(text) in self.forgotten_ids():
            return AddResult("forgotten")
        # Near-duplicate guard. It only runs for recallable facts: a quarantined
        # (untrusted) copy must still reach upstream add(), which uses it to
        # quarantine an older clean copy of the same text.
        if (trust or "").strip().lower() in RECALLABLE_TRUST_TIERS:
            for fact in self.list():
                if fact.trusted_for_recall and is_near_duplicate(text, fact.text):
                    return AddResult("duplicate", fact_id(fact.text))
        # Short-lived facts ("visiting next weekend") go to the expiring notes.
        # Quarantined facts never do: notes carry no trust marker.
        if (
            self._notes_path is not None
            and (trust or "").strip().lower() in RECALLABLE_TRUST_TIERS
            and is_time_bound(text)
        ):
            return self._add_note(text)
        stored = super().add(text, source=source, trust=trust)
        return AddResult("stored" if stored else "duplicate", fact_id(text))

    def _add_note(self, text: str) -> AddResult:
        """Store *text* as an expiring note unless a similar note exists."""
        assert self._notes_path is not None
        with MemoryStore(self._notes_path, allow_persistence=True) as notes:
            for note_id, old_text, _expires in notes.entries():
                if is_near_duplicate(text, old_text):
                    return AddResult("duplicate", NOTE_PREFIX + note_id)
            note_id = notes.remember(text, ttl_hours=TIME_BOUND_TTL_HOURS)
        return AddResult("note", NOTE_PREFIX + note_id)

    def mark_forgotten(self, text: str) -> None:
        """Record the hash of *text* (e.g. a forgotten note) without storing it."""
        with self._lock, _cross_process_lock(self._lock_path()):
            self._write_forgotten(self.forgotten_ids() | {fact_id(text)})

    def forget(self, fid: str) -> bool:
        """Remove the fact with this id; return whether one was removed."""
        # Same locks as upstream add/clear: thread lock + cross-process lock.
        with self._lock, _cross_process_lock(self._lock_path()):
            # Re-read the file so we never overwrite another process's writes.
            self._sync_from_disk_locked()
            current: list[Fact] = self._facts  # type: ignore[has-type]
            kept = [fact for fact in current if fact_id(fact.text) != fid]
            if len(kept) == len(current):
                return False  # no such fact
            self._facts = kept
            # Rewrite the JSONL atomically without the forgotten fact.
            self._flush()
            # Record only the hash, so the text itself is gone from disk.
            self._write_forgotten(self.forgotten_ids() | {fid})
        return True

    def clear(self) -> int:
        """Remove every fact, the forgotten list and the lock file."""
        removed = super().clear()
        self._forgotten_path.unlink(missing_ok=True)
        self._lock_path().unlink(missing_ok=True)
        return removed

    @property
    def forgotten_path(self) -> Path:
        return self._forgotten_path
