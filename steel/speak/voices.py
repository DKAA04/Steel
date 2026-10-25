"""The pinned Piper voices (docs/SPEAK.md section 3): the single source of truth.

Every file is pinned to one revision of ``rhasspy/piper-voices`` with its size
and SHA-256. The setup script downloads from here and checks each file; at run
time a voice is used only if its files are there with the pinned sizes, and
``steel speak check`` also checks the hashes. Nothing here uses the network.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from steel.home import MODELS_DIR

REPOSITORY = "rhasspy/piper-voices"
REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
BASE_URL = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/"
VOICES_DIR = "piper-voices"
CARD = "MODEL_CARD"
LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "it": "Italian"}


@dataclass(frozen=True)
class PinnedFile:
    name: str  # file name on disk (and last part of the URL)
    size: int
    sha256: str


@dataclass(frozen=True)
class Voice:
    name: str
    language: str  # en, es or it
    path: str  # directory in the repository
    sample_rate: int
    default: bool  # downloaded by the setup script without --voice
    licence: str  # of the recordings, from the model card
    model: PinnedFile
    config: PinnedFile
    card: PinnedFile

    def files(self) -> tuple[PinnedFile, ...]:
        return (self.model, self.config, self.card)

    def url(self, pinned: PinnedFile) -> str:
        return f"{BASE_URL}{self.path}/{pinned.name}"


def _voice(
    name: str,
    language: str,
    path: str,
    rate: int,
    default: bool,
    licence: str,
    model: tuple[int, str],
    config: tuple[int, str],
    card: tuple[int, str],
) -> Voice:
    return Voice(
        name=name,
        language=language,
        path=path,
        sample_rate=rate,
        default=default,
        licence=licence,
        model=PinnedFile(f"{name}.onnx", *model),
        config=PinnedFile(f"{name}.onnx.json", *config),
        card=PinnedFile(CARD, *card),
    )


VOICES: tuple[Voice, ...] = (
    _voice(
        "en_US-john-medium", "en", "en/en_US/john/medium", 22050, True,
        "public domain (LibriVox)",
        (63531379, "789c6c875726e627ddee93d51d8727859abe9c091c3d141591f4b83c2072e988"),
        (4965, "af60f177b6b550f3d7a302720c0fb89e7f94a82b5dca464775ef63b1c69ba09a"),
        (498, "a056da65cd70dcbda96fc3420fe8d3442dbf3c8b75f49cdf800a715b42e299d1"),
    ),
    _voice(
        "es_ES-carlfm-x_low", "es", "es/es_ES/carlfm/x_low", 16000, True,
        "public domain",
        (28130791, "d69677323a907cd4963f42b29c20a98b5d6bfa7f3e64df339915e4650c00d125"),
        (4159, "d9bdfa9ff01eb2bc9e62e7d2593939d1e4c4d8eb7cf75f972731539d12399966"),
        (250, "a6e62a3d36c37c7702d877f784001208b952e2bf4d17c72a6b5da442fbefb4b3"),
    ),
    _voice(
        "it_IT-riccardo-x_low", "it", "it/it_IT/riccardo/x_low", 16000, True,
        "M-AILABS licence (LibriVox recordings)",
        (28130791, "1368de15f123275a7ef951c9e5e30be0f58a032daa14a0da44037443c1d1d21b"),
        (4161, "146ab9c634afe524e9fb7530f2510df7a42fb1db56b52658ca1fb3d98001a62a"),
        (260, "7ce3b2fc4f454c84936887971574e436cc2154049b427a530f70bee050b2fdc6"),
    ),
    _voice(
        "en_GB-northern_english_male-medium", "en",
        "en/en_GB/northern_english_male/medium", 22050, False,
        "CC BY-SA 4.0; fine-tuned from lessac (research-only data)",
        (63201294, "57a219ae8e638873db7d18893304be5069c42868f392bb95c3ff17f0690d0689"),
        (4847, "69557ed3d974463453e9b0c09dd99a7ed0e52b8b87b64b357dbeeb2540a97d47"),
        (305, "75661c3a50c0ba82b32b3a438e54103d8dfe5bbe8fbb864ac687a040e79948bb"),
    ),
    _voice(
        "es_ES-davefx-medium", "es", "es/es_ES/davefx/medium", 22050, False,
        "CC0; fine-tuned from lessac (research-only data)",
        (63201294, "6658b03b1a6c316ee4c265a9896abc1393353c2d9e1bca7d66c2c442e222a917"),
        (4817, "0e0dda87c732f6f38771ff274a6380d9252f327dca77aa2963d5fbdf9ec54842"),
        (276, "420703b5d8ea239b729f13d83f31eea9bae5fcb89447de23ebc94aa8a4768f95"),
    ),
    _voice(
        "it_IT-serena-medium", "it", "it/it_IT/serena/medium", 22050, False,
        "CC BY 4.0 (synthetic speech)",
        (63516051, "fe4e26b2c1236e2a44d2e295cad564d94a0d8b0a9fca1ccdfd70dd4af3116eb5"),
        (5254, "14cb4995874138b2792ae07753b5540942124761af9ae6609f9b520f28837881"),
        (266, "1d0dbdc0182d19f6510fd3dcd2893587912a65f8cafe6da57754863f168bbe7e"),
    ),
)  # fmt: skip


def find_voice(name: str) -> Voice | None:
    return next((v for v in VOICES if v.name == name), None)


def voices_root(home: Path) -> Path:
    return home / MODELS_DIR / VOICES_DIR


def voice_dir(home: Path, voice: Voice) -> Path:
    return voices_root(home) / voice.name


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_ok(path: Path, pinned: PinnedFile, *, verify_hash: bool = False) -> bool:
    """A regular file (not a link) of the pinned size, and hash if asked."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != pinned.size:
        return False
    return not verify_hash or sha256_of(path) == pinned.sha256


def installed(home: Path, voice: Voice, *, verify_hash: bool = False) -> bool:
    directory = voice_dir(home, voice)
    return all(
        file_ok(directory / f.name, f, verify_hash=verify_hash) for f in voice.files()
    )


def voice_for(home: Path, language: str) -> Voice | None:
    """The installed voice for *language*. A non-default one the owner fetched
    on purpose (setup --voice NAME) is preferred over the default."""
    candidates = sorted(
        (v for v in VOICES if v.language == language), key=lambda v: v.default
    )
    return next((v for v in candidates if installed(home, v)), None)
