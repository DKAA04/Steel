"""scripts/speak_setup.py and scripts/speak_check.py, with fakes only.

No download, no pip, no engine, no player: the owner runs the real thing.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from speak_helpers import FakeEngine, FakeKeys, FakeSink, install_defaults
from voice_helpers import FakeTTY

from steel.speak.indicator import SpeakingIndicator
from steel.speak.synth import PIPER_VERSION
from steel.speak.voices import BASE_URL, REVISION, VOICES, PinnedFile, Voice, voice_dir
from steel.tools import get_speak_tool

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tiny_voice(data: bytes = b"model bytes") -> Voice:
    def pin(name: str, content: bytes) -> PinnedFile:
        return PinnedFile(name, len(content), hashlib.sha256(content).hexdigest())

    return Voice(
        "en_XX-tiny-x_low", "en", "en/en_XX/tiny/x_low", 16000, True, "test",
        pin("en_XX-tiny-x_low.onnx", data), pin("en_XX-tiny-x_low.onnx.json", b"{}"),
        pin("MODEL_CARD", b"card"),
    )  # fmt: skip


class Opener:
    """Serves bytes per file name, as the network would; records the URLs."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.urls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        return io.BytesIO(self.files[url.rsplit("/", 1)[1]])


class SetupVoicesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.setup = load("speak_setup")
        self.voice = tiny_voice()
        self.good = {
            "en_XX-tiny-x_low.onnx": b"model bytes",
            "en_XX-tiny-x_low.onnx.json": b"{}",
            "MODEL_CARD": b"card",
        }

    def fetch(self, files: dict[str, bytes], **kw: Any) -> Opener:
        opener = Opener(files)
        with contextlib.redirect_stdout(io.StringIO()):
            self.setup.fetch_voice(self.home, self.voice, opener=opener, **kw)
        return opener

    def test_matching_files_are_kept_private(self) -> None:
        self.fetch(self.good)
        directory = voice_dir(self.home, self.voice)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(directory.parent.stat().st_mode), 0o700)
        for pinned in self.voice.files():
            path = directory / pinned.name
            self.assertEqual(path.read_bytes(), self.good[pinned.name])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(directory)), sorted(self.good))  # no .part

    def test_a_file_that_does_not_match_its_pin_is_not_kept(self) -> None:
        for bad in (b"model bytez", b"model bytes and more", b"short"):
            with self.subTest(bad=bad), self.assertRaises(self.setup.SetupError):
                self.fetch({**self.good, "en_XX-tiny-x_low.onnx": bad})
            directory = voice_dir(self.home, self.voice)
            self.assertNotIn("en_XX-tiny-x_low.onnx", os.listdir(directory))
            self.assertFalse(any(n.endswith(".part") for n in os.listdir(directory)))

    def test_files_already_there_and_correct_are_not_fetched_again(self) -> None:
        self.fetch(self.good)
        voice_dir(self.home, self.voice).parent.chmod(0o775)
        again = self.fetch(self.good)
        self.assertEqual(again.urls, [])
        # A re-run still makes the directories private.
        mode = voice_dir(self.home, self.voice).parent.stat().st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o700)

    def test_a_dry_run_fetches_and_writes_nothing(self) -> None:
        opener = self.fetch(self.good, dry_run=True)
        self.assertEqual(opener.urls, [])
        self.assertFalse(voice_dir(self.home, self.voice).exists())

    def test_every_url_is_https_at_the_pinned_revision_and_others_are_refused(
        self,
    ) -> None:
        for voice in VOICES:
            for pinned in voice.files():
                self.assertTrue(
                    voice.url(pinned).startswith(
                        f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/"
                    )
                )
        with self.assertRaises(self.setup.SetupError):
            self.setup._open_url("https://example.com/x.onnx")

    def test_the_defaults_and_named_extra_voices_are_chosen(self) -> None:
        names = [v.name for v in self.setup.chosen_voices([])]
        self.assertEqual(
            names, ["en_US-john-medium", "es_ES-carlfm-x_low", "it_IT-riccardo-x_low"]
        )
        more = self.setup.chosen_voices(["it_IT-serena-medium"])
        self.assertEqual(more[-1].name, "it_IT-serena-medium")
        with self.assertRaises(self.setup.SetupError):
            self.setup.chosen_voices(["en_GB-alan-medium"])  # not pinned: refused


class SetupEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "piper"
        self.setup = load("speak_setup")

    def test_the_lock_file_pins_every_package_by_hash(self) -> None:
        text = (SCRIPTS / "piper-requirements.txt").read_text()
        packages = re.findall(r"^([a-z0-9_.-]+)==([^\s\\]+)", text, re.MULTILINE)
        self.assertIn(("piper-tts", PIPER_VERSION), packages)
        self.assertIn(("onnxruntime", "1.31.0"), packages)
        blocks = re.split(r"\n(?=[a-z0-9_.-]+==)", text.split("\n", 5)[-1])
        for block in blocks:
            if "==" in block:
                self.assertRegex(block, r"--hash=sha256:[0-9a-f]{64}", block[:40])

    def test_the_engine_is_installed_with_hashes_only_and_no_telemetry(self) -> None:
        calls: list[tuple[list[str], dict[str, str]]] = []
        with contextlib.redirect_stdout(io.StringIO()):
            self.setup.install_engine(
                self.dir,
                python="/usr/bin/python3",
                dry_run=False,
                run=lambda argv, **kw: calls.append((argv, kw["env"])),
            )
        (venv, _), (pip, env) = calls
        self.assertEqual(venv, ["/usr/bin/python3", "-m", "venv", str(self.dir)])
        for flag in ("--require-hashes", "--no-deps", ":all:"):
            self.assertIn(flag, pip)
        self.assertEqual(env["ORT_DISABLE_TELEMETRY"], "1")

    def test_an_installed_engine_is_left_alone_and_a_dry_run_runs_nothing(self) -> None:
        run = mock.Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            self.setup.install_engine(
                self.dir, python="/usr/bin/python3", dry_run=True, run=run
            )
        run.assert_not_called()
        (self.dir / "bin").mkdir(parents=True)
        (self.dir / "bin" / "python").write_text("")
        meta = (
            self.dir
            / f"lib/python3.13/site-packages/piper_tts-{PIPER_VERSION}.dist-info"
        )
        meta.mkdir(parents=True)
        (meta / "METADATA").write_text(f"Version: {PIPER_VERSION}\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.setup.install_engine(
                self.dir, python="/usr/bin/python3", dry_run=False, run=run
            )
        run.assert_not_called()
        self.assertIn("nothing to do", out.getvalue())


class SpeakCheckScriptTest(base.HomeTestCase):
    def test_it_measures_then_speaks_each_language_with_a_scratch_grant(self) -> None:
        install_defaults(self.home)
        script = load("speak_check")
        engine = FakeEngine()
        sinks: list[FakeSink] = []

        def sink() -> FakeSink:
            sinks.append(FakeSink())
            return sinks[-1]

        tool = get_speak_tool(
            self.home,
            engine=engine,
            open_sink=sink,
            indicator=SpeakingIndicator(FakeTTY()),
            keys=FakeKeys(),
        )
        made: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*a: Any, **k: Any) -> str:
            made.append(real_mkdtemp(*a, **k))
            return made[-1]

        before = sorted(p.name for p in self.home.rglob("*"))
        out = io.StringIO()
        with (
            mock.patch.object(tempfile, "mkdtemp", spy),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(script.main(home=self.home, engine=engine, tool=tool), 0)
        text = out.getvalue()
        self.assertEqual(text.count('"real_time_factor"'), 3)
        self.assertEqual(text.count("Spoken ("), 3)
        self.assertEqual(len(engine.texts), 6)  # measured once, spoken once, each
        self.assertEqual(len(sinks), 3)  # only the spoken ones reach a player
        self.assertFalse(Path(made[0]).exists())  # scratch deleted
        self.assertEqual(
            sorted(p.name for p in self.home.rglob("*")), before
        )  # real home untouched
        self.assertIn("scratch directory deleted", text)

    def test_the_url_base_is_the_pinned_repository(self) -> None:
        self.assertEqual(
            BASE_URL, f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/"
        )


if __name__ == "__main__":
    unittest.main()
