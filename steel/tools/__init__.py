"""STEEL's tools. None is registered anywhere: build them here, run them with
``steel.security.build_executor`` (docs/POLICY.md)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from openjarvis.tools._stubs import BaseTool

from steel.memory import SteelMemory
from steel.tools.memory_search import MemorySearchFacts, MemorySearchNotes
from steel.tools.microphone import (
    MICROPHONE_TOOL,
    MicrophoneListen,
    PushToTalk,
    Transcriber,
)
from steel.voice.config import VoiceConfig, voice_config
from steel.voice.indicator import Indicator
from steel.voice.source import AudioSource


def get_memory_tools(home: Path) -> list[BaseTool]:
    """The read-only memory tools for the memory in *home*.

    Building them reads and writes nothing; each call to a tool re-reads the
    stores, so it sees changes made after this function ran.
    """
    memory = SteelMemory(home)
    return [MemorySearchFacts(memory), MemorySearchNotes(memory)]


def get_microphone_tool(
    home: Path,
    *,
    config: VoiceConfig | None = None,
    open_source: Callable[[], AudioSource] | None = None,
    transcriber: Transcriber | None = None,
    push_to_talk: PushToTalk | None = None,
    indicator: Indicator | None = None,
) -> MicrophoneListen:
    """The microphone tool, wired to the real terminal, ``arecord`` and Whisper.

    Tests pass fakes for every part. Building the tool opens nothing: the
    microphone is touched only inside ``execute``, after the policy allowed it.
    """
    config = config or voice_config()
    if open_source is None:
        device, seconds = config.device, config.max_seconds

        def open_source() -> AudioSource:
            from steel.voice.mic import ArecordSource

            return ArecordSource(device, seconds)

    if transcriber is None:
        from steel.voice.stt import LocalWhisper, model_dir

        transcriber = LocalWhisper(model_dir(home, config.model), config.languages)
    if push_to_talk is None:
        from steel.voice.terminal import Terminal

        push_to_talk = Terminal()
    return MicrophoneListen(
        home=home,
        config=config,
        open_source=open_source,
        transcriber=transcriber,
        push_to_talk=push_to_talk,
        indicator=indicator or Indicator(),
    )


__all__ = [
    "MICROPHONE_TOOL",
    "MemorySearchFacts",
    "MemorySearchNotes",
    "MicrophoneListen",
    "get_memory_tools",
    "get_microphone_tool",
]
