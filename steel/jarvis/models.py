"""The pinned wake-word model files (docs/JARVIS.md section 1.1).

From the openWakeWord GitHub release v0.5.1. GitHub publishes no checksum for
release assets, so the SHA-256 values were recorded at the first download
(2026-10-25), after the sizes matched GitHub's own listing byte for byte, and
are pinned here. The models are CC BY-NC-SA 4.0: personal, non-commercial use.
Nothing here uses the network; scripts/wake_setup.py is the only downloader.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from steel.home import MODELS_DIR

RELEASE = "v0.5.1"
BASE_URL = f"https://github.com/dscripka/openWakeWord/releases/download/{RELEASE}/"
WAKE_DIR = "wake"
LICENCE = "CC BY-NC-SA 4.0 (non-commercial)"
WAKE_PHRASE = "hey jarvis"


@dataclass(frozen=True)
class WakeFile:
    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return BASE_URL + self.name


WAKE_FILES: tuple[WakeFile, ...] = (
    WakeFile(
        "melspectrogram.onnx",
        1087958,
        "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    ),
    WakeFile(
        "embedding_model.onnx",
        1326578,
        "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
    ),
    WakeFile(
        "hey_jarvis_v0.1.onnx",
        1271370,
        "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
    ),
)
WAKE_MODEL = WAKE_FILES[2].name


def wake_dir(home: Path) -> Path:
    return home / MODELS_DIR / WAKE_DIR


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_ok(path: Path, pinned: WakeFile, *, verify_hash: bool = True) -> bool:
    """A regular file (not a link) with the pinned size and, if asked, hash."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != pinned.size:
        return False
    return not verify_hash or sha256_of(path) == pinned.sha256


def models_ready(home: Path) -> bool:
    """Every file present and matching its SHA-256 (3.7 MB: cheap to hash at
    every start, unlike the 120 MB of voices)."""
    directory = wake_dir(home)
    return all(file_ok(directory / f.name, f) for f in WAKE_FILES)
