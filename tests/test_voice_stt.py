"""The local transcriber, with a fake model: nothing is loaded, heard or sent."""

from __future__ import annotations

import builtins
import os
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

from steel.voice.stt import LocalWhisper, SpeechModelMissing, Transcript, model_dir


def pcm(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


class Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class Info:
    language = "es"


class FakeModel:
    """Records what it is given; answers like faster-whisper's WhisperModel."""

    def __init__(self, ranked: list[tuple[str, float]] | None = None) -> None:
        self.ranked = ranked or [("es", 0.6), ("it", 0.3), ("en", 0.1)]
        self.transcribe_calls: list[dict[str, Any]] = []
        self.detect_calls = 0
        self.seen: list[Any] = []

    def detect_language(self, samples: Any) -> tuple[str, float, list]:
        self.detect_calls += 1
        self.seen.append(samples)
        return self.ranked[0][0], self.ranked[0][1], self.ranked

    def transcribe(self, samples: Any, **kwargs: Any) -> tuple[list[Segment], Info]:
        self.seen.append(samples)
        self.transcribe_calls.append(kwargs)
        return [Segment(" Hola, "), Segment("qué tal ")], Info()


class LocalWhisperTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "faster-whisper-small"
        self.dir.mkdir()
        self.model = FakeModel()
        self.loaded: list[Path] = []

    def make(
        self, languages: tuple[str, ...] = (), speech: bool = True
    ) -> LocalWhisper:
        def load(path: Path) -> FakeModel:
            self.loaded.append(path)
            return self.model

        return LocalWhisper(
            self.dir, languages, load_model=load, find_speech=lambda _s: speech
        )

    def test_it_returns_the_joined_text_and_the_language(self) -> None:
        result = self.make().transcribe(pcm(100, -100, 200))
        self.assertEqual(result, Transcript("Hola, qué tal", "es"))

    def test_the_model_gets_a_float_array_not_a_file(self) -> None:
        self.make().transcribe(pcm(16384, -16384))
        call_audio = self.model.seen[0]
        self.assertIsInstance(call_audio, np.ndarray)
        self.assertEqual(call_audio.dtype, np.float32)

    def test_samples_are_scaled_to_the_range_whisper_expects(self) -> None:
        seen: list[Any] = []
        speech = self.make()
        speech._find_speech = lambda s: seen.append(s.copy()) or True  # type: ignore[assignment]
        speech.transcribe(pcm(16384, -16384, 0))
        np.testing.assert_allclose(seen[0], [0.5, -0.5, 0.0])

    def test_the_audio_array_is_zeroed_afterwards(self) -> None:
        self.make().transcribe(pcm(5000, -5000, 7000))
        self.assertFalse(self.model.seen[0].any())

    def test_the_audio_array_is_zeroed_even_if_the_model_fails(self) -> None:
        class Boom(FakeModel):
            def transcribe(self, samples: Any, **kwargs: Any) -> Any:
                self.seen.append(samples)
                raise RuntimeError("model failed")

        self.model = Boom()
        with self.assertRaises(RuntimeError):
            self.make().transcribe(pcm(5000, -5000))
        self.assertFalse(self.model.seen[0].any())

    def test_no_speech_means_the_model_is_never_asked(self) -> None:
        result = self.make(speech=False).transcribe(pcm(1, 2, 3))
        self.assertEqual(result, Transcript("", None))
        self.assertEqual(self.model.transcribe_calls, [])
        self.assertEqual(self.model.detect_calls, 0)

    def test_a_single_language_is_forced_and_not_detected(self) -> None:
        self.make(("it",)).transcribe(pcm(1000))
        self.assertEqual(self.model.detect_calls, 0)
        self.assertEqual(self.model.transcribe_calls[0]["language"], "it")

    def test_several_languages_pick_the_likeliest_of_those(self) -> None:
        self.model = FakeModel([("pt", 0.5), ("ca", 0.2), ("it", 0.15), ("es", 0.1)])
        self.make(("en", "es", "it")).transcribe(pcm(1000))
        self.assertEqual(self.model.transcribe_calls[0]["language"], "it")

    def test_when_none_of_the_allowed_languages_is_ranked_the_first_is_used(
        self,
    ) -> None:
        self.model = FakeModel([("pt", 0.9)])
        self.make(("en", "es")).transcribe(pcm(1000))
        self.assertEqual(self.model.transcribe_calls[0]["language"], "en")

    def test_no_language_setting_lets_the_model_detect_among_all(self) -> None:
        self.make(()).transcribe(pcm(1000))
        self.assertIsNone(self.model.transcribe_calls[0]["language"])
        self.assertEqual(self.model.detect_calls, 0)

    def test_it_asks_the_model_for_greedy_decoding_with_its_own_vad(self) -> None:
        self.make(("en",)).transcribe(pcm(1000))
        call = self.model.transcribe_calls[0]
        self.assertEqual(call["beam_size"], 1)
        self.assertIs(call["vad_filter"], True)
        self.assertIs(call["condition_on_previous_text"], False)

    def test_a_missing_model_directory_is_reported_not_downloaded(self) -> None:
        self.dir.rmdir()
        speech = self.make()
        with self.assertRaises(SpeechModelMissing):
            speech.transcribe(pcm(1000))
        self.assertEqual(self.loaded, [])  # the loader was never even called

    def test_the_model_is_loaded_once(self) -> None:
        speech = self.make()
        speech.transcribe(pcm(1000))
        speech.transcribe(pcm(1000))
        self.assertEqual(len(self.loaded), 1)

    def test_model_dir_is_inside_the_models_folder_of_the_home(self) -> None:
        self.assertEqual(
            model_dir(Path("/h"), "small"),
            Path("/h/steel_models/faster-whisper-small"),
        )


class RealLoaderIsOfflineTest(unittest.TestCase):
    """The default loader, with WhisperModel replaced: how it is called matters."""

    def test_the_model_is_loaded_from_a_directory_with_local_files_only(self) -> None:
        import sys
        import types

        from steel.voice import stt

        calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        class Spy:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                calls.append((args, kwargs))

        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = Spy  # type: ignore[attr-defined]
        with (
            mock.patch.dict(sys.modules, {"faster_whisper": fake_module}),
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            stt._load_whisper(Path("/models/faster-whisper-small"))
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        args, kwargs = calls[0]
        self.assertEqual(args, ("/models/faster-whisper-small",))  # a path, not a name
        self.assertIs(kwargs["local_files_only"], True)
        self.assertEqual(kwargs["device"], "cpu")
        self.assertEqual(kwargs["compute_type"], "int8")


class NoDiskNoNetworkTest(unittest.TestCase):
    def test_transcribing_creates_no_file_and_opens_no_connection(self) -> None:
        import socket
        import tempfile as tf

        def forbidden(*_: Any, **__: Any) -> Any:
            raise AssertionError("must not be used while transcribing")

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "faster-whisper-small"
            directory.mkdir()
            speech = LocalWhisper(
                directory,
                ("en", "es"),
                load_model=lambda _p: FakeModel(),
                find_speech=lambda _s: True,
            )
            before = sorted(os.listdir(tmp))
            real_open = builtins.open

            def guarded_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
                if any(flag in mode for flag in "wax+"):
                    raise AssertionError(f"tried to open {file!r} for writing")
                return real_open(file, mode, *a, **k)

            with (
                mock.patch.object(tf, "mkstemp", forbidden),
                mock.patch.object(tf, "NamedTemporaryFile", forbidden),
                mock.patch.object(tf, "TemporaryDirectory", forbidden),
                mock.patch.object(builtins, "open", guarded_open),
                mock.patch.object(socket.socket, "connect", forbidden),
                mock.patch.object(socket, "getaddrinfo", forbidden),
            ):
                speech.transcribe(pcm(1000, -1000))
            self.assertEqual(sorted(os.listdir(tmp)), before)


class SourceHygieneTest(unittest.TestCase):
    """STEEL's voice code must not be able to reach a cloud or write a file."""

    VOICE = Path(__file__).resolve().parent.parent / "steel" / "voice"
    TOOLS = Path(__file__).resolve().parent.parent / "steel" / "tools"

    def sources(self) -> dict[str, str]:
        files = list(self.VOICE.glob("*.py")) + [self.TOOLS / "microphone.py"]
        return {p.name: p.read_text(encoding="utf-8") for p in files if p.exists()}

    def test_nothing_imports_openjarvis_speech_or_a_cloud_backend(self) -> None:
        banned = (
            "openjarvis.speech",
            "get_speech_backend",
            "get_tts_backend",
            "SpeechRegistry",
            "TTSRegistry",
            "openai",
            "deepgram",
            "cartesia",
            "kokoro",
            "sounddevice",
            "soundfile",
            "httpx",
            "requests",
            "urllib",
            "socket",
        )
        for name, text in self.sources().items():
            code = "\n".join(
                line
                for line in text.splitlines()
                if not line.lstrip().startswith(("#", '"', "'"))
            )
            for word in banned:
                # The words may appear in docstrings that explain why they are
                # avoided; they must not appear in code lines.
                self.assertNotRegex(
                    code,
                    rf"(import|from)\s+[\w.]*{word}|\b{word}\s*\(",
                    f"{name} uses {word}",
                )

    def test_nothing_writes_a_file_or_makes_a_temporary_one(self) -> None:
        for name, text in self.sources().items():
            for word in (
                "tempfile",
                "NamedTemporaryFile",
                "mkstemp",
                "write_bytes",
                "wave.open",
                ".save(",
            ):
                self.assertNotIn(word, text, f"{name} mentions {word}")

    def test_importing_the_voice_package_loads_no_audio_or_speech_library(
        self,
    ) -> None:
        import subprocess
        import sys

        code = (
            "import sys, steel.voice, steel.voice.recorder, steel.voice.consent, "
            "steel.voice.indicator, steel.voice.terminal, steel.voice.stt, "
            "steel.voice.config; "
            "bad = [m for m in sys.modules if m.split('.')[0] in "
            "{'sounddevice', 'faster_whisper', 'ctranslate2', 'soundfile', 'av'} "
            "or m.startswith('openjarvis.speech')]; "
            "print(bad)"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(out, "[]")


if __name__ == "__main__":
    unittest.main()
