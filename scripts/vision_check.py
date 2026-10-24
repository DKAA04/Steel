"""Live vision check: take ONE picture with the REAL camera and describe it.

    python scripts/vision_check.py                      # model qwen2.5vl:3b
    python scripts/vision_check.py --models qwen3.5:2b
    python scripts/vision_check.py --models qwen2.5vl:3b,qwen3.5:2b --question "is the desk tidy?"

For the owner to run, in a terminal. It is not part of pytest, and no test runs
it against a real camera (tests/test_vision_check_script.py runs it with fakes).

What it does, in a scratch directory that is deleted at the end:
  1. shows the consent text and asks you to type "yes" (consent for this run only);
  2. writes a consent file and a grants file INTO THE SCRATCH DIRECTORY, never into
     your real ~/.openjarvis (your real grants file stays yours to write by hand);
  3. runs the real stack: the CAMERA OPEN line, ffmpeg on /dev/video0, the policy
     executor (agent "eyes", tool camera_look) and your local Ollama;
  4. prints how long the camera was open, the size of the picture the model got,
     and for each model its time and its description of the same picture.

The picture stays in RAM and is wiped; no file is written. Nothing leaves this
computer: Ollama must be on localhost.

Time budget, measured on the author's machine (Ryzen 5 5500U, CPU only, busy) with
SYNTHETIC images and no camera: qwen2.5vl:3b took 90 to 120 s per picture, almost
all of it reading the image; qwen3.5:2b took 14 to 26 s. Yours will differ. Models
are loaded one after the other and Ollama keeps them in RAM for about 5 minutes:
on 14 GiB, running both filled the swap, so if memory is tight compare them in two
runs (and `ollama stop <model>` in between).

Good output looks like: "camera closed after" about 1 s, a picture size of at most
448 px on its longest side, a description that matches what the camera can see, and
"scratch directory deleted". If it fails, the message says which step: /dev/video0
may be the wrong node (try STEEL_CAMERA_DEVICE=/dev/video1), busy, or not 640x480.
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
from dataclasses import replace
from pathlib import Path
from typing import Any

from steel.pin import PinError, check_openjarvis_pin

SCRATCH_PREFIX = "steel-vision-check-"


class CompareDescriber:
    """Sends the same picture to each model in turn; returns the first answer.

    A model that fails is recorded and skipped, so one missing model does not hide
    the others. If every model fails the error is raised.
    """

    def __init__(self, describers: list[tuple[str, Any]]) -> None:
        self.describers = describers  # (name, Describer)
        self.rows: list[dict[str, Any]] = []

    def describe(self, image_b64: str, question: str) -> str:
        first: str | None = None
        last_error: Exception | None = None
        for name, describer in self.describers:
            started = time.perf_counter()
            try:
                text = describer.describe(image_b64, question)
            except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                last_error = exc
                self.rows.append(
                    {
                        "model": name,
                        "seconds": time.perf_counter() - started,
                        "error": type(exc).__name__,
                    }
                )
                continue
            self.rows.append(
                {"model": name, "seconds": time.perf_counter() - started, "text": text}
            )
            first = text if first is None else first
        if first is None:
            raise last_error or RuntimeError("no model answered")
        return first


def _parse(argv: list[str] | None) -> argparse.Namespace:
    from steel.vision.config import MAX_QUESTION_CHARS, vision_config

    parser = argparse.ArgumentParser(description="Live vision check (real camera).")
    parser.add_argument(
        "--models",
        default=vision_config().model,
        help="comma-separated Ollama model names, e.g. qwen2.5vl:3b,qwen3.5:2b",
    )
    parser.add_argument(
        "--question", default=None, help="optional question about the room"
    )
    args = parser.parse_args(argv)
    args.models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not args.models:
        parser.error("--models needs at least one model name")
    for name in args.models:
        if vision_config({"STEEL_VISION_MODEL": name}).model != name:
            parser.error(f"not a usable model name: {name!r}")
    if (
        args.question is not None
        and not 0 < len(args.question.strip()) <= MAX_QUESTION_CHARS
    ):
        parser.error(f"--question must be 1 to {MAX_QUESTION_CHARS} characters")
    return args


def run(
    argv: list[str] | None = None,
    *,
    ask: Callable[[str], str] = input,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
    open_source: Callable[[], Any] | None = None,
    indicator: Any = None,
    list_models: Callable[[str], list[str]] | None = None,
    describer_for: Callable[[Any, str], Any] | None = None,
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
    from steel.tools import CAMERA_TOOL, get_camera_tool
    from steel.vision.commands import (
        VISION_AGENT,
        configured_host,
        installed_models,
        look_once,
    )
    from steel.vision.config import vision_config
    from steel.vision.consent import CONSENT_TEXT, record_consent
    from steel.vision.describe import (
        OllamaDescriber,
        VisionUnavailable,
        clean_description,
        ollama_host,
    )

    base = vision_config()
    host = ollama_host(configured_host(steel_home()))
    try:
        models = (list_models or installed_models)(host)
    except VisionUnavailable as exc:
        echo(f"Cannot check the models: {exc}.")
        return 1
    except Exception:  # noqa: BLE001 - any failure to ask Ollama stops the check
        echo("Cannot reach Ollama on this computer. Is it running?")
        return 1
    missing = [m for m in args.models if m not in models]
    if missing:
        echo(f"Model(s) not installed in Ollama: {', '.join(missing)}")
        echo("Installed: " + (", ".join(models) or "(none)"))
        return 1

    echo(CONSENT_TEXT)
    echo("")
    echo(
        "This is a LIVE check with your real camera. The consent below is for this "
        "run only and is stored in a scratch directory that is deleted at the end."
    )
    if not is_terminal():
        echo("Run this in a terminal, not from a pipe or script. No picture was taken.")
        return 1
    try:
        answer = ask("> ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        echo("No picture was taken.")
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
                            "agent_id": VISION_AGENT,
                            "grants": [
                                {"capability": "sensor:camera", "pattern": CAMERA_TOOL}
                            ],
                        }
                    ]
                }
            )
        )
        grants.chmod(0o600)

        # One model-wait per model, so the executor's limit grows with the list.
        config = replace(
            base,
            model=args.models[0],
            model_timeout=base.model_timeout * len(args.models),
        )
        make = describer_for or (
            lambda cfg, name: OllamaDescriber(replace(cfg, model=name), host)
        )
        compare = CompareDescriber([(name, make(config, name)) for name in args.models])
        tool = get_camera_tool(
            scratch,
            config=config,
            open_source=open_source,
            describer=compare,
            indicator=indicator,
        )
        executor = build_executor(
            [tool], policy=load_home_policy(scratch), agent_id=VISION_AGENT
        )
        echo("\nLooking now: the camera opens once, for about a second.")
        started = time.perf_counter()
        try:
            result = look_once(executor, tool, args.question)
        except KeyboardInterrupt:
            echo("\nCancelled.")
            return 130
        total = time.perf_counter() - started
        if not result.success:
            echo(f"Not run: {result.content}")
            return 1
        echo("")
        echo(
            f"camera open for {result.metadata.get('capture_seconds')} s; "
            f"picture sent to the model: {result.metadata.get('image_size')} "
            f"(at most {config.max_side} px on the longest side)"
        )
        for row in compare.rows:
            if "error" in row:
                echo(
                    f"[{row['model']}] FAILED after {row['seconds']:.1f} s ({row['error']})"
                )
                continue
            echo(f"[{row['model']}] {row['seconds']:.1f} s")
            echo(f"[{row['model']}] description: {clean_description(row['text'])}")
        if result.metadata.get("flagged"):
            echo("note: the description looks like it contains instructions")
        echo(f"whole call (camera + models): {total:.1f} s")
    finally:
        files = sorted(p.name for p in scratch.rglob("*") if p.is_file())
        shutil.rmtree(scratch, ignore_errors=True)
        gone = not scratch.exists()
        echo(
            f"scratch directory {'deleted' if gone else 'NOT deleted: ' + str(scratch)}"
            f" (it held only: {', '.join(files) or 'nothing'}; no picture was written)"
        )
        if not gone:
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(run())
