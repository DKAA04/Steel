"""STEEL's tools. None is registered anywhere: build them here, run them with
``steel.security.build_executor`` (docs/POLICY.md)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from openjarvis.tools._stubs import BaseTool

from steel.memory import SteelMemory
from steel.speak.config import SpeakConfig, speak_config
from steel.speak.indicator import SpeakingIndicator
from steel.speak.player import AudioSink
from steel.speak.synth import Synthesizer
from steel.tools.camera import CAMERA_TOOL, CameraLook, StartKey
from steel.tools.memory_search import (
    MemorySearchFacts,
    MemorySearchNotes,
    MemorySearchRoom,
)
from steel.tools.microphone import (
    MICROPHONE_TOOL,
    MicrophoneListen,
    PushToTalk,
    Transcriber,
)
from steel.tools.speak import SPEAK_TOOL, Speak, StopKeys
from steel.vision.config import VisionConfig, vision_config
from steel.vision.describe import Describer
from steel.vision.indicator import CameraIndicator
from steel.vision.source import FrameSource
from steel.vision.startkey import PictureKey
from steel.voice.config import VoiceConfig, voice_config
from steel.voice.indicator import Indicator
from steel.voice.source import AudioSource


def get_memory_tools(home: Path) -> list[BaseTool]:
    """The read-only memory tools for the memory in *home*.

    Building them reads and writes nothing; each call to a tool re-reads the
    stores, so it sees changes made after this function ran.
    """
    memory = SteelMemory(home)
    return [
        MemorySearchFacts(memory),
        MemorySearchNotes(memory),
        MemorySearchRoom(memory),
    ]


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

        transcriber = LocalWhisper(
            model_dir(home, config.model), config.languages, hint=config.hint
        )
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


def get_camera_tool(
    home: Path,
    *,
    config: VisionConfig | None = None,
    open_source: Callable[[int], FrameSource] | None = None,
    describer: Describer | None = None,
    indicator: CameraIndicator | None = None,
    start_key: StartKey | None = None,
    ollama_host: str = "",
) -> CameraLook:
    """The camera tool, wired to the real terminal, ``ffmpeg`` and a local Ollama.

    Tests pass fakes for every part. Building the tool opens nothing and contacts
    nothing: the camera is touched only inside ``execute``, after the policy
    allowed it. *ollama_host* is the OpenJarvis config value (may be empty).
    """
    config = config or vision_config()
    if open_source is None:
        # The tool passes the warm-up: the default first, more for the one retry.
        device, size, timeout = (
            config.device,
            config.capture_size,
            config.capture_timeout,
        )

        def open_source(warmup: int) -> FrameSource:
            from steel.vision.camera import FfmpegCameraSource

            return FfmpegCameraSource(device, size, warmup, timeout)

    if describer is None:
        from steel.vision.describe import OllamaDescriber
        from steel.vision.describe import ollama_host as resolve_host

        describer = OllamaDescriber(config, resolve_host(ollama_host))
    return CameraLook(
        home=home,
        config=config,
        open_source=open_source,
        describer=describer,
        indicator=indicator or CameraIndicator(),
        start_key=start_key or PictureKey(),
    )


def get_speak_tool(
    home: Path,
    *,
    keys: StopKeys | None = None,
    config: SpeakConfig | None = None,
    engine: Synthesizer | None = None,
    open_sink: Callable[[], AudioSink] | None = None,
    indicator: SpeakingIndicator | None = None,
    clock: Callable[[], float] | None = None,
) -> Speak:
    """The speak tool, wired to Piper (separate process), aplay and the terminal.

    Building it starts and opens nothing: the engine and the speaker are
    touched only inside ``execute``, after the policy allowed it.
    """
    from steel.speak.keys import TypedLineStop
    from steel.speak.player import AplaySink
    from steel.speak.synth import PiperEngine, engine_dir

    config = config or speak_config()
    return Speak(
        home=home,
        config=config,
        engine=engine
        or PiperEngine(
            engine_dir(),
            idle_end=config.idle_end_seconds,
            persistent=config.persistent,
            keep_voices=config.keep_voices,
            voice_idle_seconds=config.voice_idle_minutes * 60.0,
        ),
        open_sink=open_sink or AplaySink,
        indicator=indicator or SpeakingIndicator(),
        keys=keys or TypedLineStop(),
        **({"clock": clock} if clock is not None else {}),
    )


__all__ = [
    "CAMERA_TOOL",
    "MICROPHONE_TOOL",
    "SPEAK_TOOL",
    "CameraLook",
    "MemorySearchFacts",
    "MemorySearchNotes",
    "MemorySearchRoom",
    "MicrophoneListen",
    "Speak",
    "get_camera_tool",
    "get_memory_tools",
    "get_microphone_tool",
    "get_speak_tool",
]
