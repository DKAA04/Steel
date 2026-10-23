"""SteelFactStore: OpenJarvis' LocalFactStore plus forget-by-id.

Upstream ``LocalFactStore`` can only ``clear()`` everything. STEEL needs to
forget one fact at a time, so this subclass adds:

- ``fact_id(text)``: a stable id derived from the fact text, so the same fact
  has the same id after a restart without changing the JSONL file format;
- ``forget(fact_id)``: remove exactly that fact;
- a "forgotten" list of fact-id hashes (never the text) that ``add`` checks,
  so the extractor cannot store the very same fact again.

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
from pathlib import Path

from openjarvis.memory.store import Fact, LocalFactStore, _cross_process_lock

from steel.home import FORGOTTEN_FILE

FACT_PREFIX = "f-"


def fact_id(text: str) -> str:
    """Return the stable id of a fact: 'f-' + 12 hex chars of SHA-256.

    LocalFactStore already treats facts that differ only by case as duplicates,
    so normalising the same way makes the id unique within one store.
    """
    normalised = text.strip().lower()
    return FACT_PREFIX + hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


class SteelFactStore(LocalFactStore):
    """LocalFactStore that can forget one fact and refuses forgotten repeats."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_facts: int = 1000,
        forgotten_path: str | Path | None = None,
    ) -> None:
        # Let upstream resolve the JSONL path and load existing facts.
        super().__init__(path, max_facts=max_facts)
        # Keep the forgotten list next to the facts file by default.
        self._forgotten_path = (
            Path(forgotten_path)
            if forgotten_path is not None
            else self._path.with_name(FORGOTTEN_FILE)
        )

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
        """Store a fact unless it is blank or was explicitly forgotten."""
        # Upstream add_with_trust / add_many* all funnel through add().
        if (text or "").strip() and fact_id(text) in self.forgotten_ids():
            return False
        return super().add(text, source=source, trust=trust)

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
