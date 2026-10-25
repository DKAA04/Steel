"""Set up spoken replies: the Piper engine and the pinned voices (docs/SPEAK.md 3).

    python scripts/speak_setup.py                 # engine + the three default voices
    python scripts/speak_setup.py --voices-only
    python scripts/speak_setup.py --voice en_GB-northern_english_male-medium
    python scripts/speak_setup.py --dry-run       # say what would happen, fetch nothing

This is the only part of spoken replies that uses the network, and only when
you run it. Running it again checks what is there and fetches only what is
missing or wrong.

Engine: a virtual environment of its own at ~/.local/share/steel/piper (or
$STEEL_PIPER_DIR), outside ~/.openjarvis, with piper-tts 1.8.0 (GPL-3.0-or-later)
installed from scripts/piper-requirements.txt, where every wheel is pinned by
SHA-256 (pip --require-hashes --only-binary :all: --no-deps). STEEL never imports
it; it starts it as a separate program.

Voices: each file comes from huggingface.co/rhasspy/piper-voices at one pinned
revision, is written as NAME.part, checked against its pinned size and SHA-256,
and only then renamed into ~/.openjarvis/steel_models/piper-voices/<voice>/
(directory 700, files 600). A file that does not match is deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from steel.home import steel_home
from steel.offline import TELEMETRY_OFF
from steel.speak.synth import PIPER_VERSION, engine_dir, engine_python, engine_version
from steel.speak.voices import (
    BASE_URL,
    VOICES,
    PinnedFile,
    Voice,
    file_ok,
    find_voice,
    voice_dir,
)

REQUIREMENTS = Path(__file__).resolve().parent / "piper-requirements.txt"
TIMEOUT = 60.0
BLOCK = 1 << 20


class SetupError(RuntimeError):
    pass


def install_commands(directory: Path, python: str) -> list[list[str]]:
    """The two commands that build the engine's environment."""
    return [
        [python, "-m", "venv", str(directory)],
        [
            str(engine_python(directory)), "-m", "pip", "install",
            "--require-hashes", "--only-binary", ":all:", "--no-deps",
            "--no-input", "--disable-pip-version-check", "-r", str(REQUIREMENTS),
        ],
    ]  # fmt: skip


def install_engine(
    directory: Path,
    *,
    python: str | None,
    dry_run: bool,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    version = engine_version(directory) if engine_python(directory).is_file() else None
    if version == PIPER_VERSION:
        print(f"engine: piper-tts {version} already in {directory}; nothing to do")
        return
    if version is not None:
        raise SetupError(
            f"{directory} holds piper-tts {version}, not {PIPER_VERSION}; "
            "move it away first (this script does not delete it)"
        )
    if python is None:
        raise SetupError("python3 was not found to create the engine's environment")
    commands = install_commands(directory, python)
    for argv in commands:
        print("engine: " + " ".join(argv))
        if not dry_run:
            # No onnxruntime telemetry even if something imports it here.
            run(
                argv,
                check=True,
                env={**os.environ, **TELEMETRY_OFF, "PIP_NO_INPUT": "1"},
            )
    if not dry_run:
        print(f"engine: piper-tts {engine_version(directory)} installed in {directory}")


def _open_url(url: str) -> BinaryIO:
    if not url.startswith(BASE_URL):
        raise SetupError("refusing a URL that is not the pinned repository")
    # BASE_URL is https; a redirect elsewhere is refused below.
    response = urllib.request.urlopen(url, timeout=TIMEOUT)
    if not response.geturl().startswith("https://"):
        raise SetupError("the download was redirected away from https")
    return response  # type: ignore[no-any-return]


def fetch_file(
    voice: Voice,
    pinned: PinnedFile,
    directory: Path,
    *,
    opener: Callable[[str], BinaryIO] = _open_url,
) -> bool:
    """Download one file and keep it only if it matches. True if downloaded."""
    target = directory / pinned.name
    if file_ok(target, pinned, verify_hash=True):
        return False
    part = directory / f"{pinned.name}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out, opener(voice.url(pinned)) as response:
            while block := response.read(BLOCK):
                size += len(block)
                if size > pinned.size:
                    raise SetupError(f"{pinned.name}: larger than pinned")
                digest.update(block)
                out.write(block)
        if size != pinned.size or digest.hexdigest() != pinned.sha256:
            raise SetupError(f"{pinned.name}: size or SHA-256 does not match the pin")
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()
    return True


def fetch_voice(
    home: Path,
    voice: Voice,
    *,
    dry_run: bool = False,
    opener: Callable[[str], BinaryIO] = _open_url,
) -> None:
    directory = voice_dir(home, voice)
    if not dry_run:
        # Both levels private, also on a re-run (mkdir's mode is only a default).
        for level in (directory.parent, directory):
            level.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(level, 0o700)
    for pinned in voice.files():
        if file_ok(directory / pinned.name, pinned, verify_hash=True):
            print(f"voice {voice.name}: {pinned.name} already there and checked")
            continue
        if dry_run:
            print(
                f"voice {voice.name}: would fetch {voice.url(pinned)} ({pinned.size} bytes)"
            )
            continue
        fetch_file(voice, pinned, directory, opener=opener)
        print(
            f"voice {voice.name}: fetched {pinned.name}, {pinned.size} bytes, SHA-256 {pinned.sha256}"
        )


def chosen_voices(names: list[str]) -> list[Voice]:
    voices = [v for v in VOICES if v.default]
    for name in names:
        voice = find_voice(name)
        if voice is None:
            known = ", ".join(v.name for v in VOICES)
            raise SetupError(f"unknown voice {name}; pinned voices: {known}")
        if voice not in voices:
            voices.append(voice)
    return voices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    only = parser.add_mutually_exclusive_group()
    only.add_argument("--engine-only", action="store_true")
    only.add_argument("--voices-only", action="store_true")
    parser.add_argument(
        "--voice", action="append", default=[], help="an extra pinned voice"
    )
    parser.add_argument(
        "--python", help="interpreter for the engine's venv (default: python3)"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.voices_only:
            install_engine(
                engine_dir(),
                python=args.python or shutil.which("python3"),
                dry_run=args.dry_run,
            )
        if not args.engine_only:
            home = steel_home()
            for voice in chosen_voices(args.voice):
                fetch_voice(home, voice, dry_run=args.dry_run)
    except (SetupError, OSError, subprocess.CalledProcessError) as exc:
        print(f"speak setup: {exc}", file=sys.stderr)
        return 1
    print("Done. Next: steel speak check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
