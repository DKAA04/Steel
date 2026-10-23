"""Line-level access to OpenJarvis' MEMORY.md and USER.md.

Each non-blank line that is not a Markdown heading counts as one entry. Its id
is a prefix ('m-' or 'u-') plus 12 hex chars of SHA-256 of the exact line.
``forget`` removes exactly one line whose id matches. This replaces
OpenJarvis' ``memory_manage`` tool, whose ``remove`` deletes *every* line that
merely contains the given text.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryFile:
    """One Markdown memory file and how to identify and reset it."""

    path: Path
    prefix: str  # 'm-' for MEMORY.md, 'u-' for USER.md
    header: str  # content written back by wipe(), same as OpenJarvis' default

    def _line_id(self, line: str) -> str:
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]
        return self.prefix + digest

    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def _is_entry(line: str) -> bool:
        # Headings ('# ...') are structure, not remembered content.
        return bool(line.strip()) and not line.lstrip().startswith("#")

    def entries(self) -> list[tuple[str, str]]:
        """Return (id, line) for every entry line, in file order."""
        return [(self._line_id(ln), ln) for ln in self._lines() if self._is_entry(ln)]

    def forget(self, entry_id: str) -> bool:
        """Remove the first line with this id; return whether one was removed."""
        lines = self._lines()
        for index, line in enumerate(lines):
            if self._is_entry(line) and self._line_id(line) == entry_id:
                del lines[index]  # exactly one line, never a substring match
                self._write("\n".join(lines) + "\n")
                return True
        return False

    def wipe(self) -> int:
        """Reset the file to its bare header; return how many entries it held."""
        count = len(self.entries())
        if self.path.exists():
            self._write(self.header)
        return count

    def _write(self, text: str) -> None:
        """Atomic rewrite: temp file in the same directory, then rename."""
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
