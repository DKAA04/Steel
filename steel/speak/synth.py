"""Piper as a separate program (docs/SPEAK.md section 4: the GPL boundary).

STEEL never imports Piper. It starts ``python -I -m piper`` from Piper's own
virtual environment, inside ``unshare --net`` (a network namespace with only a
loopback that is down), writes the text to its standard input and reads raw
16-bit mono PCM from its standard output. The text is never on the command
line or in the environment, so ``ps`` cannot show it. Without ``unshare`` the
engine is not started at all.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from steel.offline import TELEMETRY_OFF

ENGINE_DIR_ENV = "STEEL_PIPER_DIR"
PIPER_VERSION = "1.8.0"
# Only these: no proxies, no keys, no HOME (Piper needs none of them).
ENGINE_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", **TELEMETRY_OFF}
ISOLATION = ("--net", "--map-root-user", "--")
_KILL_WAIT = 2.0


class EngineUnavailable(RuntimeError):
    """The engine cannot be started. The message never contains the text."""


def default_engine_dir() -> Path:
    return Path.home() / ".local" / "share" / "steel" / "piper"


def engine_dir(env: Mapping[str, str] | None = None) -> Path:
    """$STEEL_PIPER_DIR if it is an absolute path, else ~/.local/share/steel/piper."""
    env = os.environ if env is None else env
    raw = env.get(ENGINE_DIR_ENV, "")
    return Path(raw) if raw and os.path.isabs(raw) else default_engine_dir()


def engine_python(directory: Path) -> Path:
    return directory / "bin" / "python"


def engine_version(directory: Path) -> str | None:
    """The installed piper-tts version, read from its metadata (runs nothing)."""
    for meta in sorted(
        directory.glob("lib/python3*/site-packages/piper_tts-*.dist-info/METADATA")
    ):
        try:
            for line in meta.read_text(encoding="utf-8").splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            continue
    return None


def engine_argv(unshare: str, python: Path, model: Path) -> list[str]:
    return [
        unshare,
        *ISOLATION,
        str(python),
        "-I",
        "-m",
        "piper",
        "-m",
        str(model),
        "--output-raw",
    ]


class EngineRun(Protocol):
    """One reply being synthesized."""

    def readable(self, timeout: float) -> bool: ...

    def read_into(self, buffer: memoryview) -> int: ...

    def stop(self) -> None: ...


class Synthesizer(Protocol):
    def start(self, text: str, model: Path) -> EngineRun: ...


class PiperRun:
    def __init__(self, process: Any) -> None:
        self._process = process

    def readable(self, timeout: float) -> bool:
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        return bool(ready)

    def read_into(self, buffer: memoryview) -> int:
        return int(self._process.stdout.readinto(buffer) or 0)

    def stop(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
        try:
            self._process.wait(timeout=_KILL_WAIT)
        except subprocess.TimeoutExpired:
            pass
        if self._process.stdout is not None:
            self._process.stdout.close()


class PiperEngine:
    def __init__(
        self,
        directory: Path,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._directory = directory
        self._popen = popen
        self._which = which

    def start(self, text: str, model: Path) -> PiperRun:
        python = engine_python(self._directory)
        if not python.is_file():
            raise EngineUnavailable(
                f"the speech engine is not installed in {self._directory} "
                "(run: python scripts/speak_setup.py)"
            )
        unshare = self._which("unshare")
        if unshare is None:
            raise EngineUnavailable(
                "unshare (util-linux) was not found, so the engine cannot be run "
                "without network access; nothing is spoken"
            )
        try:
            process = self._popen(
                engine_argv(unshare, python, model),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env=dict(ENGINE_ENV),
                close_fds=True,
                bufsize=0,
            )
        except OSError as exc:
            raise EngineUnavailable("the speech engine could not be started") from exc
        try:
            # One line: Piper speaks each input line separately.
            process.stdin.write(" ".join(text.split()).encode("utf-8") + b"\n")
            process.stdin.close()
        except OSError as exc:
            PiperRun(process).stop()
            raise EngineUnavailable("the speech engine stopped at once") from exc
        return PiperRun(process)
