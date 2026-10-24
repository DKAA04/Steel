"""Local speech to text: faster-whisper on the CPU, audio in RAM only.

Written against ``faster_whisper``'s public API instead of wrapping the
OpenJarvis backend, which saves the audio to a temp file, takes a model *name*
(and downloads it if missing) and lives in a package that also loads the cloud
backends. See docs/VOICE.md section 2. Here:

- the audio goes in as a NumPy array, so no file is written;
- the model is loaded from a directory that must already exist, with
  ``local_files_only=True``, so nothing can be downloaded;
- ``openjarvis.speech`` is not imported.

``faster_whisper`` is imported only when a model is loaded.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel.home import MODELS_DIR


class SpeechModelMissing(RuntimeError):
    """The model directory is not there (it is fetched by hand, never at run time)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None


def model_dir(home: Path, name: str) -> Path:
    return home / MODELS_DIR / f"faster-whisper-{name}"


def _load_whisper(path: Path) -> Any:
    # Belt and braces: a model *directory* never triggers a download, and neither
    # does local_files_only, but the hub library is also told it is offline.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from faster_whisper import WhisperModel

    return WhisperModel(
        str(path), device="cpu", compute_type="int8", local_files_only=True
    )


def _find_speech(samples: Any) -> bool:
    """Silero VAD (bundled with faster-whisper): is there any speech at all?"""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return bool(get_speech_timestamps(samples, VadOptions()))


class LocalWhisper:
    """Turns 16-bit 16 kHz mono PCM into text. Nothing is written or sent."""

    def __init__(
        self,
        directory: Path,
        languages: tuple[str, ...] = (),
        *,
        load_model: Callable[[Path], Any] = _load_whisper,
        find_speech: Callable[[Any], bool] = _find_speech,
    ) -> None:
        self._directory = directory
        self._languages = languages
        self._load_model = load_model
        self._find_speech = find_speech
        self._model: Any = None

    def load(self) -> None:
        """Load the model now (about 1.5 s for ``small``). Idempotent."""
        if self._model is not None:
            return
        if not self._directory.is_dir():
            raise SpeechModelMissing(
                f"speech model not found at {self._directory} "
                "(see docs/VOICE.md section 3)"
            )
        self._model = self._load_model(self._directory)

    def _language(self, samples: Any) -> str | None:
        # () = let the model detect among all languages; one code = forced;
        # several = detect, then take the most likely of those (detection costs
        # about as much as the transcription itself, so it is skipped when forced).
        if not self._languages:
            return None
        if len(self._languages) == 1:
            return self._languages[0]
        _best, _probability, ranked = self._model.detect_language(samples)
        allowed = [(p, code) for code, p in ranked if code in self._languages]
        return max(allowed)[1] if allowed else self._languages[0]

    def transcribe(self, audio: bytes | bytearray) -> Transcript:
        """Text of the speech in *audio*; empty if the VAD finds none."""
        # Imported by name: numpy's type stubs need Python 3.12, and mypy checks 3.11.
        np = importlib.import_module("numpy")
        self.load()
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
        try:
            if not self._find_speech(samples):
                return Transcript("", None)  # skip the model: silence costs seconds
            segments, info = self._model.transcribe(
                samples,
                language=self._language(samples),
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return Transcript(text, getattr(info, "language", None))
        finally:
            samples[:] = 0  # best effort: Python may keep other copies
