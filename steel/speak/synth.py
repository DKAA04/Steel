"""Piper as a separate program (docs/SPEAK.md section 4: the GPL boundary).

STEEL never imports Piper. It starts ``python -I -m piper`` from Piper's own
virtual environment, inside ``unshare --net`` (a network namespace with only a
loopback that is down), writes the text to its standard input and reads raw
16-bit mono PCM from its standard output. The text is never on the command
line or in the environment, so ``ps`` cannot show it. Without ``unshare`` the
engine is not started at all.

Loading a voice takes about 4 s, so the engine can be started EARLY
(``prepare``), before the reply exists: it loads the voice and waits on its
standard input, and ``start`` then only writes the text and closes stdin. One
process still speaks one reply, so the end of its output is the end of the
reply (the stock CLI marks no end between lines; docs/LATENCY.md section 5).
A waiting engine is stopped by ``close``, at exit, and by the kernel if STEEL
itself dies (``setpriv --pdeathsig``); its stdin also reaches end of file
then, which makes Piper exit.
"""

from __future__ import annotations

import atexit
import os
import select
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from steel.offline import TELEMETRY_OFF

ENGINE_DIR_ENV = "STEEL_PIPER_DIR"
PIPER_VERSION = "1.8.0"
# Only these: no proxies, no keys, no HOME (Piper needs none of them).
ENGINE_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", **TELEMETRY_OFF}
ISOLATION = ("--net", "--map-root-user", "--")
# util-linux setpriv: the kernel kills the engine when STEEL's thread that
# started it dies, even on SIGKILL. It survives unshare's user namespace
# (checked on the owner's machine, 2026-10-25).
DEATH_SIGNAL = ("--pdeathsig", "KILL", "--")
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


def engine_argv(
    unshare: str, python: Path, model: Path, setpriv: str | None = None
) -> list[str]:
    prefix = [setpriv, *DEATH_SIGNAL] if setpriv else []
    return [
        *prefix,
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

    def prepare(self, model: Path) -> bool: ...

    def close(self) -> None: ...


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
        self._lock = threading.Lock()
        self._waiting: tuple[Any, Path] | None = None  # (process, model)
        self._atexit = False

    def _spawn(self, model: Path) -> Any:
        """Start the engine for *model*. It waits for text on its stdin."""
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
            return self._popen(
                engine_argv(unshare, python, model, self._which("setpriv")),
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

    def _feed(self, process: Any, text: str) -> PiperRun:
        try:
            # One line: Piper speaks each input line separately.
            process.stdin.write(" ".join(text.split()).encode("utf-8") + b"\n")
            process.stdin.close()
        except OSError as exc:
            PiperRun(process).stop()
            raise EngineUnavailable("the speech engine stopped at once") from exc
        return PiperRun(process)

    def prepare(self, model: Path) -> bool:
        """Start the engine for *model* now, so the voice loads before the reply.

        Whether one is waiting. A waiting engine for another voice is stopped.
        """
        with self._lock:
            if self._waiting is not None:
                process, waiting_for = self._waiting
                if waiting_for == model and process.poll() is None:
                    return True
                self._discard()
            try:
                process = self._spawn(model)
            except EngineUnavailable:
                return False
            self._waiting = (process, model)
            if not self._atexit:
                atexit.register(self.close)  # no engine outlives a normal exit
                self._atexit = True
            return True

    def start(self, text: str, model: Path) -> PiperRun:
        with self._lock:
            process = None
            if self._waiting is not None:
                waiting, waiting_for = self._waiting
                self._waiting = None
                if waiting_for == model and waiting.poll() is None:
                    process = waiting
                else:  # another voice, or it died: not used
                    PiperRun(waiting).stop()
        if process is not None:
            try:
                return self._feed(process, text)
            except EngineUnavailable:
                pass  # it died just now: start a fresh one below
        return self._feed(self._spawn(model), text)

    def close(self) -> None:
        """Stop a waiting engine, if any."""
        with self._lock:
            self._discard()

    def _discard(self) -> None:
        if self._waiting is not None:
            process, _model = self._waiting
            self._waiting = None
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            PiperRun(process).stop()
