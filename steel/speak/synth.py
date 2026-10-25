"""Piper as a separate program (docs/SPEAK.md section 4: the GPL boundary).

STEEL never imports Piper. It starts ``python -I -m piper`` from Piper's own
virtual environment, inside ``unshare --net`` (a network namespace with only a
loopback that is down), writes the text to its standard input and reads raw
16-bit mono PCM from its standard output. The text is never on the command
line or in the environment, so ``ps`` cannot show it. Without ``unshare`` the
engine is not started at all.

Loading a voice takes about 4 s, so ONE engine per voice stays running for the
session (docs/SPEAK.md section 6, "Kept loaded"): it loads the voice once and
waits on its standard input. Each reply is written to it as one line. Piper's
raw output marks no end between lines, so the end of a reply is an idle gap: no
new audio bytes for ``idle_end`` seconds after audio has started (synthesis is
about 14 times faster than playback). An interrupted reply is dropped by
replacing the engine with a clean one, never by draining it, so stale audio can
never play in the next reply. With ``persistent=False``, or if the long-lived
engine fails, the old way is used: one process per reply, whose end of file is
the end of the reply.

Every engine is stopped by ``close``, at exit, and by the kernel if STEEL itself
dies (``setpriv --pdeathsig``); its stdin also reaches end of file then, which
makes Piper exit.
"""

from __future__ import annotations

import atexit
import os
import re
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
# How long to wait for the FIRST audio of a reply: the voice may still be
# loading (4 s here, much more under memory pressure), but a stuck engine must
# not keep the owner waiting for ever.
FIRST_AUDIO_WAIT = 45.0
# Seconds of silence after a reply's last audio that count as its end, at least;
# a long sentence needs longer (see ``idle_gap``).
DEFAULT_IDLE_END = 1.5
# Synthesis takes about 0.07 s per second of speech (measured); one second of
# speech is about 15 characters. So a sentence of N characters needs about
# N * 0.005 s; the gap is twice that, to be safe.
_GAP_PER_CHAR = 0.01
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# The engine counts as "warm" if the first audio came this soon after the text.
WARM_FIRST_AUDIO = 1.0


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

    # Also on every run: ``died`` (the engine exited mid-reply) and
    # ``engine_seconds`` (text sent to first audio); read with getattr.


class Synthesizer(Protocol):
    def start(self, text: str, model: Path) -> EngineRun: ...

    def prepare(self, model: Path) -> bool: ...

    def close(self) -> None: ...


def idle_gap(text: str, setting: float) -> float:
    """Silence that ends a reply: *setting*, or more for a very long sentence."""
    longest = max((len(part) for part in _SENTENCE_END.split(text)), default=0)
    return max(setting, longest * _GAP_PER_CHAR)


class PiperRun:
    """A reply from a process made for it: its end of file is the end."""

    died = False
    engine_seconds: float | None = None

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


@dataclass
class _Engine:
    """A running engine: its process, its voice, and what it has done."""

    process: Any
    model: Path
    served: int = 0  # replies it has finished cleanly


class PersistentRun:
    """One reply from a long-lived engine. Its output never ends, so the end is
    an idle gap after the last audio byte (docs/SPEAK.md section 6)."""

    died = False  # the engine exited before the reply was over

    def __init__(
        self,
        owner: PiperEngine,
        engine: _Engine,
        *,
        idle_end: float,
        clock: Callable[[], float],
    ) -> None:
        self._owner = owner
        self._engine = engine
        self._idle_end = idle_end
        self._clock = clock
        self._sent = self._last = clock()
        self._heard = False  # any audio yet
        self._ended = False
        self.engine_seconds: float | None = None

    @property
    def clean(self) -> bool:
        """The reply ended by silence after audio: the engine holds nothing."""
        return self._ended and self._heard and not self.died

    def readable(self, timeout: float) -> bool:
        if self._ended:
            return True
        process = self._engine.process
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if ready:
            return True
        if process.poll() is not None:
            self.died = self._ended = True
            return True
        limit = self._idle_end if self._heard else FIRST_AUDIO_WAIT
        if self._clock() - self._last >= limit:
            self._ended = True  # silence: this reply is finished
            return True
        return False

    def read_into(self, buffer: memoryview) -> int:
        if self._ended:
            return 0
        count = int(self._engine.process.stdout.readinto(buffer) or 0)
        if count == 0:  # end of file: the engine exited
            self.died = self._ended = True
            return 0
        self._last = self._clock()
        if not self._heard:
            self._heard = True
            self.engine_seconds = self._last - self._sent
        return count

    def stop(self) -> None:
        self._owner._release(self)


class PiperEngine:
    def __init__(
        self,
        directory: Path,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        which: Callable[[str], str | None] = shutil.which,
        idle_end: float = DEFAULT_IDLE_END,
        persistent: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._directory = directory
        self._popen = popen
        self._which = which
        self._idle_end = idle_end
        self._persistent = persistent
        self._clock = clock
        self._lock = threading.Lock()
        self._current: _Engine | None = None
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

    @staticmethod
    def _write(process: Any, text: str, *, last: bool) -> None:
        """One line: Piper speaks each input line separately. *last* closes
        stdin, which ends a one-reply process."""
        try:
            process.stdin.write(" ".join(text.split()).encode("utf-8") + b"\n")
            if last:
                process.stdin.close()
        except (OSError, ValueError) as exc:
            PiperRun(process).stop()
            raise EngineUnavailable("the speech engine stopped at once") from exc

    @staticmethod
    def _has_unread_audio(process: Any) -> bool:
        """Audio nobody asked for: left over from an earlier reply."""
        ready, _, _ = select.select([process.stdout], [], [], 0)
        return bool(ready)

    def _register_exit(self) -> None:
        if not self._atexit:
            atexit.register(self.close)  # no engine outlives a normal exit
            self._atexit = True

    def _stop_current(self) -> None:
        """Stop the engine, if any. Caller holds the lock."""
        if self._current is not None:
            process = self._current.process
            self._current = None
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            PiperRun(process).stop()

    def _start_current(self, model: Path) -> _Engine:
        """A new long-lived engine for *model*. Caller holds the lock."""
        engine = _Engine(self._spawn(model), model)
        self._current = engine
        self._register_exit()
        return engine

    def prepare(self, model: Path) -> bool:
        """Have an engine for *model* loading (or loaded) now, so the voice is
        ready before the reply exists. A different voice's engine is stopped.

        Whether one is waiting.
        """
        with self._lock:
            engine = self._current
            if (
                engine is not None
                and engine.model == model
                and engine.process.poll() is None
            ):
                return True
            self._stop_current()
            try:
                self._start_current(model)
            except EngineUnavailable:
                return False
            return True

    def start(self, text: str, model: Path) -> PiperRun | PersistentRun:
        """Speak *text* with the engine for *model* (see the module docstring)."""
        if self._persistent:
            run = self._start_persistent(text, model)
            if run is not None:
                return run
        return self._start_one_shot(text, model)

    def _start_one_shot(self, text: str, model: Path) -> PiperRun:
        """The old way, also the fallback: a process for this reply alone. A
        prepared engine for this voice is used; stdin closes after the text."""
        with self._lock:
            engine, self._current = self._current, None
            process = None
            if engine is not None:
                if engine.model == model and engine.process.poll() is None:
                    process = engine.process
                else:  # another voice, or it died: not used
                    PiperRun(engine.process).stop()
        if process is not None:
            try:
                self._write(process, text, last=True)
                return PiperRun(process)
            except EngineUnavailable:
                pass  # it died just now: start a fresh one below
        process = self._spawn(model)
        self._write(process, text, last=True)
        return PiperRun(process)

    def _start_persistent(self, text: str, model: Path) -> PersistentRun | None:
        """The reply on the long-lived engine, or None if that failed."""
        with self._lock:
            engine = self._current
            usable = (
                engine is not None
                and engine.model == model
                and engine.process.poll() is None
                # Audio waiting in the pipe belongs to an earlier reply (it
                # arrived after its idle gap). It cannot be told apart from the
                # new reply's, so the engine is replaced.
                and not self._has_unread_audio(engine.process)
            )
            if not usable:
                self._stop_current()
                try:
                    engine = self._start_current(model)
                except EngineUnavailable:
                    return None
            assert engine is not None
        try:
            self._write(engine.process, text, last=False)
        except EngineUnavailable:
            with self._lock:
                if self._current is engine:
                    self._current = None
            return None
        return PersistentRun(
            self, engine, idle_end=idle_gap(text, self._idle_end), clock=self._clock
        )

    def _release(self, run: PersistentRun) -> None:
        """The reply is over. A clean engine stays loaded for the next reply; any
        other (interrupted, died, silent) is replaced by a new one, which loads
        in the background while the owner reads, so the next reply is not slow
        and no stale audio can reach it."""
        with self._lock:
            engine = run._engine
            if self._current is not engine:
                PiperRun(engine.process).stop()  # closed meanwhile (exit, voice change)
                return
            if run.clean:
                engine.served += 1
                return
            self._stop_current()
            try:
                self._start_current(engine.model)
            except EngineUnavailable:
                pass  # the next reply reports it

    def close(self) -> None:
        """Stop the engine, if any."""
        with self._lock:
            self._stop_current()
