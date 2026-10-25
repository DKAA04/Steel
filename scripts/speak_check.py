"""Live speaking check: one sentence per installed language through your REAL speakers.

    python scripts/speak_check.py

For the owner to run, in a terminal, with the volume at a comfortable level.
It is not part of pytest, and no test runs it against a real speaker
(tests/test_speak_check_script.py runs it with fakes). Run
`python scripts/speak_setup.py` first.

What it does:
  1. makes a scratch directory with a grants file that allows only agent "voice"
     to use the tool "speak" (audio:play); your real grants file is not read or
     changed, and the decision log goes to the scratch directory;
  2. for each language with an installed voice (your real
     ~/.openjarvis/steel_models/piper-voices, read only), synthesizes the sentence
     into memory with the real engine (separate program, no network), measures it
     and throws the audio away: synthesis time, first audio, length, real-time
     factor;
  3. then speaks the same sentence through the policy executor and aplay, with
     the SPEAKING line on screen (Enter or Ctrl-C stops it);
  4. deletes the scratch directory.

Good output: for each language a real-time factor well under 1 (about 0.6 to 0.85
for the whole run on the author's machine, almost all of it the 3.5 s voice
load), "Spoken (..)" and audible, understandable speech. If nothing is heard,
check the desktop's output device (it may be HDMI) and `steel speak check`.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjarvis.core.types import ToolCall

from steel.home import POLICY_FILE, steel_home
from steel.security import load_home_policy
from steel.security.executor import build_executor
from steel.speak.config import speak_config
from steel.speak.playback import CHUNK_BYTES
from steel.speak.synth import PiperEngine, Synthesizer, engine_dir
from steel.speak.voices import voice_dir, voice_for
from steel.tools import SPEAK_TOOL, get_speak_tool

SENTENCES = {
    "en": "Good evening. This is STEEL, speaking on this computer only.",
    "es": "Buenas tardes. Soy STEEL y hablo solo en este ordenador.",
    "it": "Buonasera. Sono STEEL e parlo solo su questo computer.",
}
GRANT = {
    "agents": [
        {
            "agent_id": "voice",
            "grants": [{"capability": "audio:play", "pattern": SPEAK_TOOL}],
        }
    ]
}


def measure(
    engine: Synthesizer,
    text: str,
    model: Path,
    rate: int,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Synthesize into memory, time it, and wipe the audio. Plays nothing."""
    started = clock()
    run = engine.start(text, model)
    audio = bytearray()
    first = None
    buffer = bytearray(CHUNK_BYTES)
    try:
        while True:
            if not run.readable(0.1):
                continue
            count = run.read_into(memoryview(buffer))
            if not count:
                break
            if first is None:
                first = clock() - started
            audio += buffer[:count]
    finally:
        run.stop()
    wall = clock() - started
    seconds = len(audio) / (2 * rate)
    audio[:] = bytes(len(audio))
    buffer[:] = bytes(len(buffer))
    return {
        "synthesis_s": round(wall, 2),
        "first_audio_s": round(first or 0.0, 2),
        "audio_s": round(seconds, 2),
        "real_time_factor": round(wall / seconds, 3) if seconds else None,
    }


def main(
    *, home: Path | None = None, engine: Synthesizer | None = None, tool: Any = None
) -> int:
    home = home or steel_home()  # read only: voices
    config = speak_config()
    engine = engine or PiperEngine(
        engine_dir(),
        idle_end=config.idle_end_seconds,
        persistent=config.persistent,
        keep_voices=config.keep_voices,
        voice_idle_seconds=config.voice_idle_minutes * 60.0,
    )
    scratch = Path(tempfile.mkdtemp(prefix="steel-speak-check-"))
    try:
        (scratch / POLICY_FILE).write_text(json.dumps(GRANT))
        (scratch / POLICY_FILE).chmod(0o600)
        speak = tool or get_speak_tool(home, engine=engine)
        executor = build_executor(
            [speak], policy=load_home_policy(scratch), agent_id="voice"
        )
        spoken = 0
        for language, sentence in SENTENCES.items():
            voice = voice_for(home, language)
            if voice is None:
                print(
                    json.dumps(
                        {"language": language, "voice": None, "note": "not installed"}
                    )
                )
                continue
            model = voice_dir(home, voice) / voice.model.name
            timing = measure(engine, sentence, model, voice.sample_rate)
            print(json.dumps({"language": language, "voice": voice.name, **timing}))
            call = ToolCall(
                id=language, name=SPEAK_TOOL, arguments=json.dumps({"text": sentence})
            )
            result = executor.execute(call)
            print(f"  {result.content}")
            spoken += result.success
        print(f"spoken: {spoken} of {len(SENTENCES)} languages")
        return 0 if spoken else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        print("scratch directory deleted")


if __name__ == "__main__":
    raise SystemExit(main())
