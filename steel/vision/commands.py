"""``steel vision ...`` and ``steel look`` (docs/VISION.md section 6).

``check`` is read only and opens no camera: it lists the video devices from
``/sys`` and ``/dev`` (names and a stat, no node is opened), looks for ``ffmpeg``
on the path, and asks the local Ollama what models it has. It starts no
subprocess. ``look`` needs the consent file and the owner's grant, because it
runs the camera tool through ``build_executor`` as the agent ``eyes``.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjarvis.core.types import ToolCall

from steel.home import POLICY_FILE
from steel.memory import SteelMemory
from steel.security import PolicyError, SteelPolicy, policy_or_deny_all
from steel.security.executor import build_executor
from steel.tools import CAMERA_TOOL, get_camera_tool
from steel.vision.config import (
    DEFAULT_SAVE_HOURS,
    MAX_QUESTION_CHARS,
    MAX_SAVE_HOURS,
    vision_config,
)
from steel.vision.consent import (
    CONSENT_TEXT,
    consent_path,
    read_consent,
    record_consent,
    revoke_consent,
)
from steel.vision.describe import (
    DATA_LINE,
    VisionUnavailable,
    ollama_host,
    require_loopback,
)
from steel.voice.terminal import Terminal

VISION_AGENT = "eyes"
SYSFS_VIDEO = Path("/sys/class/video4linux")
DEV_DIR = Path("/dev")
CANCEL_WAIT_SECONDS = 3.0

EXAMPLE_GRANT = """{
  "agents": [
    {"agent_id": "eyes",
     "grants": [{"capability": "sensor:camera", "pattern": "camera_look"}]}
  ]
}"""


def video_devices(
    sysfs: Path = SYSFS_VIDEO, dev: Path = DEV_DIR
) -> list[tuple[str, str, bool]] | None:
    """(node name, camera name, node exists) for each video device, from /sys.

    Reads the name files in /sys and stats /dev/<node>. No device node is opened.
    None if /sys has no video4linux directory.
    """
    try:
        entries = sorted(p for p in sysfs.iterdir() if p.name.startswith("video"))
    except OSError:
        return None
    found = []
    for entry in entries:
        try:
            name = (entry / "name").read_text(encoding="utf-8").strip()
        except OSError:
            name = "(unnamed)"
        found.append((entry.name, name, (dev / entry.name).exists()))
    return found


def configured_host(home: Path) -> str:
    """``engine.ollama.host`` from the OpenJarvis config.toml, or "" if there is none.

    Read directly: ``load_config()`` tightens the home directory's mode as a side
    effect, and ``steel vision check`` must change nothing.
    """
    try:
        data = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    host = data.get("engine", {}).get("ollama", {}).get("host", "")
    return host if isinstance(host, str) else ""


def installed_models(host: str) -> list[str]:
    """Model names the local Ollama reports (GET /api/tags). Raises VisionUnavailable."""
    from openjarvis.engine.ollama import OllamaEngine

    engine = OllamaEngine(require_loopback(host), timeout=5.0)
    try:
        if not engine.health():
            raise VisionUnavailable("Ollama does not answer on this computer")
        return list(engine.list_models())
    finally:
        engine.close()


def _has_model(models: list[str], wanted: str) -> bool:
    return wanted in models or (":" not in wanted and f"{wanted}:latest" in models)


def vision_check(
    home: Path,
    *,
    host: str = "",
    sysfs: Path | None = None,
    dev: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    list_models: Callable[[str], list[str]] | None = None,
) -> int:
    """Report what the camera path has and lacks. Read only; no camera is opened."""
    config = vision_config()
    sysfs, dev = sysfs or SYSFS_VIDEO, dev or DEV_DIR
    list_models = list_models or installed_models  # looked up now, so tests can swap it
    print("STEEL vision check (read only: the camera is not opened)")
    ready = True

    devices = video_devices(sysfs, dev)
    if devices is None:
        ready = False
        print("video devices: cannot be listed on this system")
    elif not devices:
        ready = False
        print("video devices: none found")
    else:
        for node, name, exists in devices:
            print(
                f"video device: {node} ({name}){'' if exists else ' - no node in /dev'}"
            )
        print(
            "  which node captures is not checked (that would open it); "
            f"STEEL will use {config.device}"
        )
        if not (dev / Path(config.device).name).exists():
            ready = False
            print(f"  {config.device} does not exist")

    ffmpeg = which("ffmpeg")
    print(f"capture program: {ffmpeg or 'ffmpeg NOT found'}")
    ready &= ffmpeg is not None

    try:
        models = list_models(ollama_host(host or configured_host(home)))
        if _has_model(models, config.model):
            print(f"vision model: {config.model} installed in the local Ollama")
        else:
            ready = False
            print(
                f"vision model: {config.model} NOT installed (ollama pull {config.model})"
            )
    except VisionUnavailable as exc:
        ready = False
        print(f"vision model: cannot be checked ({exc})")
    except Exception:  # noqa: BLE001 - any failure to ask Ollama is "not ready"
        ready = False
        print("vision model: cannot be checked (is Ollama running on this computer?)")
    print(
        f"picture size sent: at most {config.max_side} px on the longest side; "
        "expect about 100 to 120 s a look with qwen2.5vl:3b on this CPU"
    )

    stamp = read_consent(home)
    if stamp:
        print(
            f"consent: present (recorded {stamp}); withdraw with: steel vision revoke"
        )
    else:
        ready = False
        print("consent: MISSING (run: steel vision consent)")

    try:
        policy = SteelPolicy.from_file(home / POLICY_FILE)  # no audit: read only
        allowed, reason = policy.preview(VISION_AGENT, "sensor:camera", CAMERA_TOOL)
        grant = (
            f"agent '{VISION_AGENT}' may use {CAMERA_TOOL}"
            if allowed
            else f"NOT granted to agent '{VISION_AGENT}' ({reason})"
        )
    except PolicyError as exc:
        allowed, grant = False, f"grants file INVALID, everything is denied ({exc})"
    print(f"grant: {grant}")
    if not allowed:
        ready = False
        print(f"  to grant it, write {home / POLICY_FILE} by hand (mode 600):")
        print("  " + EXAMPLE_GRANT.replace("\n", "\n  "))

    print("ready: " + ("yes" if ready else "no"))
    return 0 if ready else 1


def vision_consent(
    home: Path,
    *,
    input_fn: Callable[[str], str] | None = None,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
) -> int:
    """Show the consent text; record consent only on an explicit typed ``yes``."""
    print(CONSENT_TEXT)
    existing = read_consent(home)
    if existing:
        print(f"\n(Consent was already recorded at {existing}.)")
    if not is_terminal():
        # Not at a keyboard: a script or pipe must not consent for the owner.
        print(
            "\nConsent must be given at a terminal. Nothing was recorded.",
            file=sys.stderr,
        )
        return 1
    try:
        answer = (input_fn or input)("> ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        print("Nothing was recorded.")
        return 1
    stamp = record_consent(home)
    print(f"Consent recorded at {stamp} in {consent_path(home)}.")
    print("Withdraw it with: steel vision revoke")
    print(
        "The camera also needs your grant in steel_policy.json (see: steel vision check)."
    )
    return 0


def vision_revoke(home: Path) -> int:
    if revoke_consent(home):
        print(
            f"Consent withdrawn ({consent_path(home).name} deleted). "
            "The camera will refuse until you consent again."
        )
    else:
        print("No camera consent was recorded; nothing to withdraw.")
    return 0


def no_consent() -> int:
    print(
        "steel: no camera consent is recorded, so the camera stays closed.\n"
        "Read and agree with: steel vision consent",
        file=sys.stderr,
    )
    return 1


def _executor(home: Path, tool: Any) -> Any:
    policy, problem = policy_or_deny_all(home)
    if problem is not None:
        print(
            f"steel: grants file invalid ({problem}); nothing is granted.",
            file=sys.stderr,
        )
    return build_executor([tool], policy=policy, agent_id=VISION_AGENT)


def look_once(executor: Any, tool: Any, question: str | None) -> Any:
    """One tool call. Ctrl-C asks the capture to stop and waits for it to end."""
    arguments = json.dumps({"question": question}) if question else "{}"
    call = ToolCall(id="eyes", name=CAMERA_TOOL, arguments=arguments)
    try:
        return executor.execute(call)
    except KeyboardInterrupt:
        tool.cancel()
        tool.wait_idle(CANCEL_WAIT_SECONDS)
        raise


def room_note(description: str, taken_at: float) -> str:
    """The text of a saved room note: the data line first, on one line.

    Notes are listed one per line, so line breaks become spaces.
    """
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(taken_at))
    flat = " ".join(description.split())
    return f"{DATA_LINE} Taken {when}: {flat}"


def look(
    home: Path,
    question: str | None,
    *,
    save: bool = False,
    hours: float = DEFAULT_SAVE_HOURS,
    memory: SteelMemory | None = None,
    tool: Any = None,
    terminal: Terminal | None = None,
    ollama_host_config: str = "",
    clock: Callable[[], float] = time.time,
) -> int:
    """``steel look``: one snapshot through the executor; prints the description.

    With *save*, asks the owner (Enter/Esc) and only then stores the text as a
    room note. Nothing is stored otherwise.
    """
    if question is not None and not 0 < len(question.strip()) <= MAX_QUESTION_CHARS:
        print(
            f"steel: the question must be 1 to {MAX_QUESTION_CHARS} characters.",
            file=sys.stderr,
        )
        return 2
    if not 0 < hours <= MAX_SAVE_HOURS:
        print(
            f"steel: --hours must be above 0 and at most {MAX_SAVE_HOURS:g}.",
            file=sys.stderr,
        )
        return 2
    if read_consent(home) is None:
        return no_consent()
    try:
        tool = tool or get_camera_tool(
            home, ollama_host=ollama_host_config or configured_host(home)
        )
    except VisionUnavailable as exc:
        print(f"steel: {exc}.", file=sys.stderr)
        return 1
    executor = _executor(home, tool)
    try:
        result = look_once(executor, tool, question)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    if not result.success:
        print(f"Not run: {result.content}", file=sys.stderr)
        return 1
    print(result.content)
    meta = result.metadata
    print(
        f"(sent to the model at {meta.get('image_size')}; camera open "
        f"{meta.get('capture_seconds')} s; model {meta.get('model_seconds')} s; "
        f"{meta.get('model')})",
        file=sys.stderr,
    )
    if not save:
        return 0
    if meta.get("flagged"):
        print(
            "This description looks like it contains instructions (text in the "
            "picture?), so it will not be offered for saving.",
            file=sys.stderr,
        )
        return 0
    memory = memory or SteelMemory(home)
    terminal = terminal or Terminal()
    # The description line after the data line is what the owner is agreeing to keep.
    print(f"Enter = save for {hours:g} hours, Esc = discard")
    if not terminal.confirm():
        print("Not saved.")
        return 0
    text = result.content.split("\n", 1)[1] if "\n" in result.content else ""
    entry_id = memory.remember_room(room_note(text, clock()), hours=hours)
    print(f"Saved as {entry_id} (room note, expires in {hours:g} hours).")
    return 0
