"""Fakes for the Jarvis loop tests. No microphone, speaker, model, window or
network is used; real processes appear only where a test says so, and then a
shell script or a copy of sleep stands in for arecord."""

from __future__ import annotations

import io
import json
import shutil
import stat
from pathlib import Path
from typing import Any, Self

from voice_helpers import FakeTTY

from steel.consent import write_stamp
from steel.home import POLICY_FILE, VOICE_CONSENT_FILE, WAKE_CONSENT_FILE
from steel.jarvis.indicators import ListeningShown
from steel.jarvis.models import wake_dir
from steel.jarvis.wakeword import FRAME_BYTES
from steel.voice.stt import Transcript

# Listening frames and the request's audio, told apart by their bytes.
QUIET = b"\x01\x00" * (FRAME_BYTES // 2)
WAKE = b"WAKE" + b"\x02\x00" * ((FRAME_BYTES - 4) // 2)
REQUEST_CHUNK = b"\x33\x0b" * 1600  # 100 ms of loud "speech" (RMS about 2867)
# Every fake that a loop reads or polls fails after this many calls, so a loop
# that never ends fails its test instead of spinning (tests/conftest.py is the
# backstop with a time limit).
MAX_FAKE_READS = 100_000
JARVIS_GRANTS = {
    "agents": [
        {
            "agent_id": "jarvis",
            "grants": [
                {"capability": "sensor:microphone_always_on", "pattern": "wake_listen"},
                {"capability": "sensor:microphone", "pattern": "microphone_listen"},
                {"capability": "audio:play", "pattern": "speak"},
            ],
        }
    ]
}


def write_grants(home: Path, data: Any = JARVIS_GRANTS) -> None:
    path = home / POLICY_FILE
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    path.chmod(0o600)


def give_consents(home: Path, *, wake: bool = True, voice: bool = True) -> None:
    if wake:
        write_stamp(home / WAKE_CONSENT_FILE)
    if voice:
        write_stamp(home / VOICE_CONSENT_FILE)


def install_fake_models(home: Path) -> None:
    """Files with the pinned SHA-256 cannot be faked, so tests that need
    ``models_ready`` patch it; this only creates the directory."""
    wake_dir(home).mkdir(parents=True, exist_ok=True)


class FakeWakeSource:
    """Scripted 80 ms frames; then the microphone "stops" (b"")."""

    def __init__(
        self,
        frames: list[bytes],
        *,
        screen: io.StringIO | None = None,
        guard: Any = None,
        fail: Exception | None = None,
    ) -> None:
        self.frames = list(frames)
        self.screen = screen
        self.guard = guard
        self.fail = fail
        self.opened = 0
        self.closed = 0
        self.read = 0
        self.screen_at_open: str | None = None
        self.guard_at_open: list[str] | None = None

    def open(self, shown: ListeningShown) -> None:
        assert isinstance(shown, ListeningShown), "opened without both indicators"
        self.opened += 1
        if self.screen is not None:
            self.screen_at_open = self.screen.getvalue()
        if self.guard is not None:
            self.guard_at_open = list(self.guard.sent)

    def read_frame(self) -> bytes:
        self.read += 1
        if self.read > MAX_FAKE_READS:
            raise AssertionError("FakeWakeSource read too often: a loop is spinning")
        if self.fail is not None:
            raise self.fail
        return self.frames.pop(0) if self.frames else b""

    def close(self) -> None:
        self.closed += 1


class FakeEngine:
    """Scores 1.0 for a WAKE frame and 0.0 otherwise; keeps what it saw."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.frames: list[bytes] = []
        self.resets = 0
        self.wiped = 0
        self.fail_on = fail_on

    def score(self, frame: bytes) -> float:
        if self.fail_on is not None and len(self.frames) == self.fail_on:
            raise RuntimeError("engine crashed")
        self.frames.append(frame)
        return 1.0 if frame.startswith(b"WAKE") else 0.0

    def reset(self) -> None:
        self.resets += 1

    def wipe(self) -> None:
        self.wiped += 1


class FakeGuard:
    """The guard client: records what the loop sent; SHOW succeeds unless told."""

    def __init__(self, *, shows: bool = True) -> None:
        self.shows = shows
        self.sent: list[str] = []
        self.started = False
        self.closed = False
        self.pid = None

    def start(self) -> None:
        self.started = True

    def show(self, state: str) -> bool:
        self.sent.append(f"SHOW {state}")
        return self.shows

    def state(self, state: str) -> None:
        self.sent.append(f"STATE {state}")

    def hide(self) -> None:
        self.sent.append("HIDE")

    def beat(self) -> None:
        self.sent.append("BEAT")

    def alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


class FakeKeys:
    """Keys the owner presses: a list consumed one per poll (None = nothing).

    A real poll waits up to *timeout*; with a fake *clock* (anything with a
    ``sleep(seconds)`` method) this one moves the clock by the same amount, so
    a loop that waits for a deadline always reaches it. After *max_polls* polls
    it raises: a loop that spins must fail the test, never hang it."""

    def __init__(
        self,
        keys: list[str | None] | None = None,
        *,
        clock: Any = None,
        max_polls: int = 10_000,
    ) -> None:
        self.keys = list(keys or [])
        self.entered = 0
        self.clock = clock
        self.max_polls = max_polls
        self.polls = 0

    def __enter__(self) -> Self:
        self.entered += 1
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def poll(self, timeout: float = 0.0) -> str | None:
        self.polls += 1
        if self.polls > self.max_polls:
            raise AssertionError(
                f"FakeKeys was polled more than {self.max_polls} times: "
                "a loop is spinning without reaching its end"
            )
        key = self.keys.pop(0) if self.keys else None
        if key is None and self.clock is not None and timeout > 0:
            self.clock.sleep(timeout)  # nothing pressed: the wait took its time
        return key


class FakeRequestSource:
    """The request recording after the wake word: loud chunks, then silence."""

    def __init__(self, chunks: int = 10) -> None:
        self.chunks = [REQUEST_CHUNK] * chunks + [b"\x00\x00" * 1600] * 20
        self.opened = 0
        self.reads = 0

    def open(self, shown: Any) -> None:
        self.opened += 1

    def read(self) -> bytes:
        self.reads += 1
        if self.reads > MAX_FAKE_READS:
            raise AssertionError("FakeRequestSource read too often: a loop is spinning")
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        pass


class FakeTranscriber:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.audio: list[bytes] = []

    def transcribe(self, audio: bytes | bytearray) -> Transcript:
        self.audio.append(bytes(audio))
        return Transcript(text=self.texts.pop(0) if self.texts else "", language="en")


class FakeTerminal:
    def stop_pressed(self) -> bool:
        return False


def stand_in_arecord(directory: Path) -> Path:
    """A copy of sleep named arecord: its process name (comm) is "arecord"."""
    target = directory / "arecord"
    sleep = shutil.which("sleep")
    assert sleep is not None
    shutil.copyfile(sleep, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return target


__all__ = ["FakeTTY"]
