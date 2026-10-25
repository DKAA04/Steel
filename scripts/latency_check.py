"""Live latency check of the chat model (needs Ollama + the chat model). Not a test.

Usage:  python scripts/latency_check.py SCRATCH_DIR [--keep-loaded]

For the OWNER to run; an assistant must not run it. It plays no sound and
opens no microphone or camera.

Sends three short, fixed prompts (no personal text) through the same path
``steel chat`` uses (``open_session``: the OpenJarvis engine from your
config.toml, wrapped in the same guardrails) and prints, per prompt, Ollama's
load time, prompt time, the time before the first token, generation time,
tokens per second and the PROCESSOR column of ``ollama ps`` (CPU, GPU or a
split). The first prompt is COLD: the script runs ``ollama stop MODEL``
first, so the model must be loaded again. ``--keep-loaded`` skips that.

Everything stays in SCRATCH_DIR (never your real ~/.openjarvis): the
OpenJarvis audit log of this run, and nothing else. The memory service is
off, so no fact is extracted and the numbers are the chat model's alone; in
a real ``steel chat`` the fact extraction runs on the same model after each
message (docs/LATENCY.md section 3).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Short, fixed and impersonal: the numbers, not the answers, are the point.
PROMPTS = (
    "Say hello in five words.",
    "Name three colours.",
    "What is two plus two? Answer in one short sentence.",
)
_STOP_WAIT = 15.0


def processor_of(ps_output: str, model: str) -> str:
    """The PROCESSOR column of ``ollama ps`` for *model* ("" if not loaded)."""
    lines = ps_output.splitlines()
    if not lines or "PROCESSOR" not in lines[0]:
        return ""
    header = lines[0]
    start = header.index("PROCESSOR")
    later = [header.find(name, start) for name in ("CONTEXT", "UNTIL")]
    end = min([i for i in later if i > start], default=len(header) + 100)
    for line in lines[1:]:
        if line.split() and line.split()[0] == model:
            return line[start:end].strip()
    return ""


def ollama(*args: str) -> str:
    """Run the ollama CLI (a list, no shell); its output, or "" if it fails."""
    program = shutil.which("ollama")
    if program is None:
        return ""
    try:
        done = subprocess.run(
            [program, *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout


def stop_model(model: str) -> bool:
    """``ollama stop`` and wait until ``ollama ps`` no longer lists it."""
    ollama("stop", model)
    deadline = time.monotonic() + _STOP_WAIT
    while time.monotonic() < deadline:
        if not processor_of(ollama("ps"), model):
            return True
        time.sleep(0.5)
    return False


def memory_line() -> str:
    from steel.meminfo import MEMINFO, read_meminfo

    try:
        values = read_meminfo(MEMINFO.read_text())
    except OSError:
        return "memory: unknown"
    gib = 1024 * 1024
    return (
        f"memory: {values.get('MemAvailable', 0) / gib:.1f} GiB available, "
        f"swap {(values.get('SwapTotal', 0) - values.get('SwapFree', 0)) / gib:.1f}"
        f" of {values.get('SwapTotal', 0) / gib:.1f} GiB used"
    )


def row(label: str, timing: Any, processor: str, chars: int) -> dict[str, Any]:
    def r(value: float | None) -> float | None:
        return None if value is None else round(value, 2)

    return {
        "prompt": label,
        "wall_s": r(timing.total),
        "load_s": r(timing.load),
        "prompt_eval_s": r(timing.prompt),
        "first_token_s": r(timing.first_token),
        "generation_s": r(timing.generate),
        "tokens": timing.tokens,
        "tokens_per_s": r(timing.tokens_per_second),
        "processor": processor or "not listed",
        "reply_chars": chars,
    }


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        raise SystemExit(__doc__)
    scratch = Path(sys.argv[1]).resolve()
    keep_loaded = "--keep-loaded" in sys.argv
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["OPENJARVIS_HOME"] = str(scratch)  # audit.db stays here
    os.environ["OPENJARVIS_NO_UPDATE_CHECK"] = "1"

    from openjarvis.core.config import load_config

    from steel.chat import open_session

    config = load_config(Path.home() / ".openjarvis" / "config.toml")
    config.memory.enabled = False  # the chat model's numbers alone
    model = config.intelligence.default_model
    print(f"model {model}; Ollama {ollama('--version').strip() or 'not found'}")
    print(memory_line())
    for name in ("OLLAMA_KEEP_ALIVE", "STEEL_OLLAMA_KEEP_ALIVE"):
        print(f"{name} in this shell: {os.environ.get(name, '(not set)')}")
    if not keep_loaded:
        print("cold start: ollama stop " + model)
        if not stop_model(model):
            print(
                "  the model is still listed by ollama ps; the first prompt may be warm"
            )
    session = open_session(config, scratch)
    for index, prompt in enumerate(PROMPTS):
        label = "cold" if index == 0 and not keep_loaded else f"warm {index}"
        reply = session.ask(prompt)
        assert session.last_timing is not None
        processor = processor_of(ollama("ps"), model)
        print(json.dumps(row(label, session.last_timing, processor, len(reply))))
    print(memory_line())
    print("--- ollama ps")
    print(ollama("ps").rstrip())


if __name__ == "__main__":
    main()
