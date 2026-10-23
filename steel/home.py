"""Where STEEL keeps its data, and the permissions it enforces there.

STEEL shares OpenJarvis' home directory (``~/.openjarvis`` unless
``$OPENJARVIS_HOME`` or ``$XDG_DATA_HOME`` says otherwise) so both tools see
the same memory files.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from openjarvis.core.paths import get_config_dir

# File names inside the home directory.
FACTS_FILE = "memory_facts.jsonl"  # OpenJarvis automatic facts (JSONL)
FORGOTTEN_FILE = "steel_forgotten.json"  # hashes of facts the user forgot
NOTES_FILE = "steel_notes.sqlite3"  # STEEL's expiring notes (future room store)
MEMORY_MD = "MEMORY.md"  # OpenJarvis agent memory file
USER_MD = "USER.md"  # OpenJarvis user profile file
POLICY_FILE = "steel_policy.json"  # permission grants, written by hand
POLICY_AUDIT_FILE = "steel_policy_audit.jsonl"  # names-only log of decisions


def steel_home() -> Path:
    """Return the shared OpenJarvis home directory (resolved on every call)."""
    return get_config_dir()


def harden(home: Path | None = None) -> Path:
    """Create the home directory if needed and restrict it to the current user.

    - New files this process creates get mode 600 (umask 077).
    - The home directory itself gets mode 700.
    - Regular files directly inside it get mode 600.
    Subdirectories (``src/``, ``.venv/`` ...) are left alone: the 700 on the
    parent already stops other users from reaching them.
    """
    home = home or steel_home()
    # umask 077: anything created from now on is private to this user.
    os.umask(0o077)
    # Create the directory privately if it does not exist yet.
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Refuse a symlinked home: chmod would then change some other directory.
    if home.is_symlink():
        raise RuntimeError(f"{home} is a symbolic link; refusing to use it.")
    home.chmod(0o700)
    for entry in home.iterdir():
        # lstat does not follow symlinks, so a link cannot redirect our chmod.
        mode = entry.lstat().st_mode
        if stat.S_ISREG(mode) and stat.S_IMODE(mode) != 0o600:
            entry.chmod(0o600)
    return home
