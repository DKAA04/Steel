"""Live voice check: record a few seconds from the REAL microphone and transcribe it.

    python scripts/voice_check.py                    # 5 seconds, model "small"
    python scripts/voice_check.py --models small,base --seconds 8

For the owner to run, in a terminal. It is not part of pytest, and no test runs
it against a real microphone (tests/test_voice_check_script.py runs it with fakes).

What it does, in a scratch directory that is deleted at the end:
  1. shows the consent text and asks you to type "yes" (consent for this run only);
  2. writes a consent file and a grants file INTO THE SCRATCH DIRECTORY, never into
     your real ~/.openjarvis (your real grants file stays yours to write by hand);
  3. runs the real stack: your Enter key, the REC line, arecord, the policy
     executor (agent "voice", tool microphone_listen) and local Whisper;
  4. prints the transcript, the time each step took, and how loud you were, so
     you can judge the silence threshold and compare models on your own voice
     (say one sentence each in English, Spanish and Italian, in three runs).

The audio stays in RAM and is wiped; no file is written. Models are read from
your real ~/.openjarvis/steel_models and never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from steel.pin import PinError, check_openjarvis_pin

MIN_SECONDS, MAX_SECONDS = 1.0, 30.0
SCRATCH_PREFIX = "steel-voice-check-"


class CompareTranscriber:
    """Runs the same in-memory audio through each model; returns the first result.

    The audio is the buffer the tool passes in, and the tool wipes it right after.
    """

    def __init__(self, models: list[tuple[str, Any]]) -> None:
        self.models = models  # (name, LocalWhisper)
        self.rows: list[dict[str, Any]] = []
        self.loudest = 0

    def transcribe(self, audio: bytes | bytearray) -> Any:
        from steel.voice.recorder import rms
        from steel.voice.source import CHUNK_BYTES

        data = bytes(audio)
        self.loudest = max(
            (rms(data[i : i + CHUNK_BYTES]) for i in range(0, len(data), CHUNK_BYTES)),
            default=0,
        )
        first = None
        for name, model in self.models:
            started = time.perf_counter()
            model.load()
            loaded = time.perf_counter()
            transcript = model.transcribe(audio)
            finished = time.perf_counter()
            self.rows.append(
                {
                    "model": name,
                    "load": loaded - started,
                    "transcribe": finished - loaded,
                    "text": transcript.text,
                    "language": transcript.language,
                }
            )
            first = first or transcript
        return first


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live voice check (real microphone).")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument(
        "--models",
        default="small",
        help="comma-separated model names in ~/.openjarvis/steel_models, e.g. small,base",
    )
    args = parser.parse_args(argv)
    if not MIN_SECONDS <= args.seconds <= MAX_SECONDS:
        parser.error(f"--seconds must be between {MIN_SECONDS:g} and {MAX_SECONDS:g}")
    args.models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not args.models:
        parser.error("--models needs at least one model name")
    return args


def run(
    argv: list[str] | None = None,
    *,
    models_home: Path | None = None,
    ask: Callable[[str], str] = input,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
    open_source: Callable[[], Any] | None = None,
    push_to_talk: Any = None,
    indicator: Any = None,
    whisper_options: dict[str, Any] | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    """The whole check. Every real part can be replaced, which is how it is tested."""
    args = _parse(argv)
    os.environ.setdefault("OPENJARVIS_NO_UPDATE_CHECK", "1")
    try:
        check_openjarvis_pin()
    except PinError as exc:
        echo(f"steel: {exc}")
        return 2

    from steel.home import steel_home
    from steel.security import load_home_policy
    from steel.security.executor import build_executor
    from steel.tools import MICROPHONE_TOOL, get_microphone_tool
    from steel.voice.commands import VOICE_AGENT, listen_once
    from steel.voice.config import VoiceConfig, voice_config
    from steel.voice.consent import CONSENT_TEXT, record_consent
    from steel.voice.stt import LocalWhisper, model_dir

    base = voice_config()
    models_home = models_home or steel_home()
    missing = [m for m in args.models if not model_dir(models_home, m).is_dir()]
    if missing:
        echo(
            f"Model(s) not found in {models_home / 'steel_models'}: {', '.join(missing)}"
        )
        echo("See docs/VOICE.md section 3 for how they were fetched.")
        return 1

    echo(CONSENT_TEXT)
    echo("")
    echo(
        "This is a LIVE check with your real microphone. The consent below is for "
        "this run only and is stored in a scratch directory that is deleted at the end."
    )
    if not is_terminal():
        echo("Run this in a terminal, not from a pipe or script. Nothing was recorded.")
        return 1
    try:
        answer = ask("> ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        echo("Nothing was recorded.")
        return 1

    scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX))  # mode 700
    code = 0
    try:
        record_consent(scratch)
        grants = scratch / "steel_policy.json"
        grants.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "agent_id": VOICE_AGENT,
                            "grants": [
                                {
                                    "capability": "sensor:microphone",
                                    "pattern": MICROPHONE_TOOL,
                                }
                            ],
                        }
                    ]
                }
            )
        )
        grants.chmod(0o600)

        languages = base.languages
        compare = CompareTranscriber(
            [
                (
                    name,
                    LocalWhisper(
                        model_dir(models_home, name),
                        languages,
                        hint=base.hint,
                        **(whisper_options or {}),
                    ),
                )
                for name in args.models
            ]
        )
        # Record exactly the requested time unless Enter is pressed first: the
        # silence cut-offs are pushed beyond it.
        config = VoiceConfig(
            max_seconds=args.seconds,
            silence_seconds=args.seconds + 1,
            startup_silence_seconds=args.seconds + 1,
            model=args.models[0],
            languages=languages,
            device=base.device,
        )
        tool = get_microphone_tool(
            scratch,
            config=config,
            transcriber=compare,
            open_source=open_source,
            push_to_talk=push_to_talk,
            indicator=indicator,
        )
        executor = build_executor(
            [tool], policy=load_home_policy(scratch), agent_id=VOICE_AGENT
        )
        echo(f"\nSpeak for up to {args.seconds:g} seconds after you press Enter.")
        started = time.perf_counter()
        try:
            result = listen_once(executor, tool)
        except KeyboardInterrupt:
            echo("\nCancelled.")
            return 130
        total = time.perf_counter() - started
        if not result.success:
            echo(f"Not run: {result.content}")
            return 1
        echo("")
        echo(
            f"recorded {result.metadata.get('seconds')} s "
            f"(stopped by: {result.metadata.get('stopped_by')})"
        )
        echo(
            f"loudest 100 ms level: {compare.loudest} "
            f"(speech counts above {config.silence_rms}; a quiet room is far below)"
        )
        for row in compare.rows:
            echo(
                f"[{row['model']}] model load {row['load']:.2f} s, transcription "
                f"{row['transcribe']:.2f} s, language {row['language']}"
            )
            echo(f"[{row['model']}] transcript: {row['text'] or '(nothing heard)'}")
        echo(f"whole call (key wait + recording + models): {total:.2f} s")
    finally:
        files = sorted(p.name for p in scratch.rglob("*") if p.is_file())
        shutil.rmtree(scratch, ignore_errors=True)
        gone = not scratch.exists()
        echo(
            f"scratch directory {'deleted' if gone else 'NOT deleted: ' + str(scratch)}"
            f" (it held only: {', '.join(files) or 'nothing'}; no audio was written)"
        )
        if not gone:
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(run())
