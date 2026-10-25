"""Fakes for the speaking tests. No engine, player, speaker or network is used."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from steel.speak.indicator import SpeakingShown
from steel.speak.voices import VOICES, Voice, voice_dir

CHUNK = bytes(range(256)) * 16  # 4096 bytes of non-silent "audio"


def tone(chunks: int) -> list[bytes]:
    return [CHUNK] * chunks


class FakeRun:
    """The engine's output: scripted chunks, then the end."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = list(chunks if chunks is not None else tone(5))
        self.stopped = 0
        self.reads = 0

    def readable(self, timeout: float) -> bool:
        return True

    def read_into(self, view: memoryview) -> int:
        self.reads += 1
        if not self.chunks:
            return 0
        chunk = self.chunks.pop(0)
        view[: len(chunk)] = chunk
        return len(chunk)

    def stop(self) -> None:
        self.stopped += 1


class FakeEngine:
    def __init__(
        self, chunks: list[bytes] | None = None, fail: Exception | None = None
    ):
        self.chunks = chunks
        self.fail = fail
        self.texts: list[str] = []
        self.models: list[Path] = []
        self.runs: list[FakeRun] = []

    def start(self, text: str, model: Path) -> FakeRun:
        if self.fail is not None:
            raise self.fail
        self.texts.append(text)
        self.models.append(model)
        run = FakeRun(self.chunks)
        self.runs.append(run)
        return run


class FakeSink:
    """A player that keeps what it is given. ``screen`` is read at open."""

    def __init__(
        self,
        *,
        screen: io.StringIO | None = None,
        done_after: int = 0,
        fail_open: Exception | None = None,
    ) -> None:
        self.screen = screen
        self.screen_at_open: str | None = None
        self.opened: list[int] = []
        self.data = bytearray()
        self.buffers: list[Any] = []
        self.finished = 0
        self.stopped = 0
        self.done_after = done_after  # polls of done() before it says yes
        self.fail_open = fail_open
        self.events: list[str] = []

    def open(self, shown: SpeakingShown, rate: int) -> None:
        assert isinstance(shown, SpeakingShown), "opened without the SPEAKING proof"
        if self.fail_open is not None:
            raise self.fail_open
        self.opened.append(rate)
        self.events.append("open")
        if self.screen is not None:
            self.screen_at_open = self.screen.getvalue()

    def write(self, data: memoryview) -> None:
        self.data += bytes(data)
        self.buffers.append(data.obj)

    def finish(self) -> None:
        self.finished += 1

    def done(self) -> bool:
        if self.done_after > 0:
            self.done_after -= 1
            return False
        return True

    def stop(self) -> None:
        self.stopped += 1
        self.events.append("stop")


class FakeKeys:
    """Stops after ``stop_after`` polls (None: never); keeps a typed line."""

    def __init__(self, stop_after: int | None = None, line: str | None = None) -> None:
        self.stop_after = stop_after
        self.line = line
        self.polls = 0
        self.begun = 0

    def begin(self) -> None:
        self.begun += 1

    def stop_requested(self) -> bool:
        self.polls += 1
        return self.stop_after is not None and self.polls > self.stop_after

    def take_line(self) -> str | None:
        line, self.line = self.line, None
        return line


def install_voice(home: Path, voice: Voice) -> None:
    """Files with the pinned sizes (sparse, wrong content: hashes do not match)."""
    directory = voice_dir(home, voice)
    directory.mkdir(parents=True, exist_ok=True)
    for pinned in voice.files():
        with open(directory / pinned.name, "wb") as handle:
            handle.truncate(pinned.size)


def install_defaults(home: Path) -> None:
    for voice in VOICES:
        if voice.default:
            install_voice(home, voice)
