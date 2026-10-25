"""Slash commands in ``steel chat`` (docs/INJECTION.md rules 3 to 7).

Only a line the owner TYPES that starts with "/" is a command (rule 3): the
chat loops call ``SlashCommands.handle`` with the line they read from the
keyboard, and nothing else. Model replies, tool results and voice transcripts
are never parsed. Each command is one call through ``build_executor`` as the
agent "chat" with the owner's hand-written grants (rule 6). The result is shown
first, prepared by ``steel.tooldata`` (rule 1), and added to the conversation
only if the owner presses Enter and the result is not flagged (rule 4). One
line runs at most one tool call (rule 5), and a session has a cap on calls and
a gap between sensor uses (rule 7).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from openjarvis.core.types import ToolCall
from openjarvis.tools._stubs import BaseTool

from steel.security import PolicyError, SteelPolicy, policy_or_deny_all
from steel.security.executor import build_executor
from steel.tooldata import prepare

CHAT_AGENT = "chat"
CANCEL_WAIT_SECONDS = 3.0


@dataclass(frozen=True)
class Command:
    tool: str
    argument: str  # the tool's one argument
    usage: str  # what follows the command word
    required: bool  # must the argument be given?
    what: str


COMMANDS: dict[str, Command] = {
    "/search": Command(
        "memory_search_facts", "query", "QUERY", True, "search the stored facts"
    ),
    "/notes": Command(
        "memory_search_notes", "query", "QUERY", True, "search your expiring notes"
    ),
    "/room": Command(
        "memory_search_room",
        "query",
        "QUERY",
        True,
        "search the room notes (saved camera descriptions; untrusted text)",
    ),
    "/look": Command(
        "camera_look",
        "question",
        "[question]",
        False,
        "take ONE picture and describe it (needs camera consent)",
    ),
}
SENSOR_TOOLS = frozenset({"camera_look"})

HELP = "\n".join(
    [
        "Slash commands (you type them; the model never can):",
        *(
            f"  {name + ' ' + c.usage:<18} {c.what} ({c.tool})"
            for name, c in COMMANDS.items()
        ),
        f"  {'/help':<18} this list",
        f"  {'/quit':<18} leave",
        (
            "Each one runs through the permission policy as agent 'chat'; you "
            "write its grants by hand (docs/INJECTION.md)."
        ),
        (
            "A result is shown to you first. Enter adds it to the conversation as "
            "data, Esc does not; a result that looks like instructions is never added."
        ),
    ]
)


@dataclass(frozen=True)
class ChatLimits:
    max_calls: int = 20  # tool calls per chat session
    sensor_gap: float = 5.0  # seconds between the end of one look and the next


def _bounded(raw: str, low: float, high: float) -> float | None:
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or not low <= value <= high:
        return None
    return value


def chat_limits(env: Mapping[str, str] | None = None) -> ChatLimits:
    """$STEEL_CHAT_TOOL_LIMIT (1-100) and $STEEL_CHAT_SENSOR_GAP (1-600 s).

    Anything else falls back to the default: a typo must not lift the limits.
    """
    env = os.environ if env is None else env
    base = ChatLimits()
    calls = _bounded(env.get("STEEL_CHAT_TOOL_LIMIT", ""), 1, 100)
    gap = _bounded(env.get("STEEL_CHAT_SENSOR_GAP", ""), 1, 600)
    return ChatLimits(
        max_calls=int(calls)
        if calls is not None and calls.is_integer()
        else base.max_calls,
        sensor_gap=gap if gap is not None else base.sensor_gap,
    )


class Keys(Protocol):
    """The owner's terminal: Enter/Esc, and "this line was the key press"."""

    def confirm(self) -> bool: ...

    def arm(self) -> None: ...


@dataclass
class SlashCommands:
    home: Path
    session: Any  # steel.chat.ChatSession
    executor: Any
    tools: dict[str, BaseTool]
    policy: SteelPolicy
    problem: PolicyError | None  # the grants file was invalid: all denied
    keys: Keys
    limits: ChatLimits
    clock: Callable[[], float] = time.monotonic
    calls: int = 0
    last_sensor_end: float | None = None
    unavailable: dict[str, str] = field(default_factory=dict)

    def handle(self, line: str) -> bool:
        """Run *line* if it is a slash command; False if it is not one at all.

        *line* must be what the owner typed, never model or tool text.
        """
        if not line.startswith("/"):
            return False
        word, _, rest = line.partition(" ")
        rest = rest.strip()
        if word == "/help":
            print(HELP)
            return True
        command = COMMANDS.get(word)
        if command is None:
            print(f"Unknown command {word[:30]}. Nothing was sent to the model.")
            print(HELP)
            return True
        if command.required and not rest:
            print(f"Usage: {word} {command.usage}")
            return True
        if command.tool in self.unavailable:
            print(f"Not available: {self.unavailable[command.tool]}")
            return True
        if not self._within_limits(command.tool):
            return True
        self.calls += 1
        arguments = json.dumps({command.argument: rest} if rest else {})
        self._run(word, command.tool, arguments)
        return True

    def _within_limits(self, tool: str) -> bool:
        if self.calls >= self.limits.max_calls:
            print(
                f"This chat reached its limit of {self.limits.max_calls} tool calls; "
                "start a new steel chat to run more."
            )
            return False
        if tool in SENSOR_TOOLS and self.last_sensor_end is not None:
            waited = self.clock() - self.last_sensor_end
            if waited < self.limits.sensor_gap:
                print(
                    f"Please wait {math.ceil(self.limits.sensor_gap - waited)} s "
                    "before the next picture."
                )
                return False
        return True

    def _run(self, word: str, tool: str, arguments: str) -> None:
        sensor = tool in SENSOR_TOOLS
        if sensor:
            self.keys.arm()  # the typed command is the owner's key press
        call = ToolCall(id=f"chat-{self.calls}", name=tool, arguments=arguments)
        try:
            result = self.executor.execute(call)
        except KeyboardInterrupt:
            live = self.tools.get(tool)
            if live is not None and hasattr(live, "cancel"):
                live.cancel()
                live.wait_idle(CANCEL_WAIT_SECONDS)
            print("\nCancelled.")
            return
        finally:
            if sensor:
                self.last_sensor_end = self.clock()
        if not result.success:
            print(f"Not run: {result.content}")
            if result.content.startswith("Capability '"):
                print(
                    f"To allow it, grant it to agent '{CHAT_AGENT}' by hand in "
                    "steel_policy.json (docs/INJECTION.md rule 6)."
                )
            return
        data = prepare(tool, result.content)
        print(f"--- {word} result (shown to you; the model has not seen it) ---")
        print(data.text)
        print("---")
        if data.flagged:
            print(
                "This result looks like it contains instructions, so it was not "
                "added to the conversation (you can still read it above)."
            )
            return
        if not data.redacted:
            print(
                "This result could not be checked for secrets, so it was not "
                "added to the conversation."
            )
            return
        print("Add this to the conversation? Enter = yes, Esc = no")
        if self.keys.confirm() and self.session.add_tool_data(data):
            print("Added as data; the model sees it with your next message.")
        else:
            print("Not added.")


def banner(slash: SlashCommands) -> str:
    """One line: which commands the grants allow. Asked with preview: not logged."""
    if slash.problem is not None:
        return (
            "Slash commands: the grants file is invalid, so every one is denied "
            f"({slash.problem}). /help lists them."
        )
    allowed, refused = [], []
    for word, command in COMMANDS.items():
        tool = slash.tools.get(command.tool)
        ok = tool is not None and all(
            slash.policy.preview(CHAT_AGENT, cap, command.tool)[0]
            for cap in tool.spec.required_capabilities
        )
        if not ok:
            refused.append(word)
            continue
        note = ""
        if command.tool == "camera_look":
            from steel.vision.consent import has_consent

            note = "" if has_consent(slash.home) else " (camera consent missing)"
        allowed.append(f"{word} allowed{note}")
    if not allowed:
        return (
            f"Slash commands: none granted to agent '{CHAT_AGENT}' yet "
            "(docs/INJECTION.md). /help lists them."
        )
    parts = list(allowed)
    if refused:
        parts.append(", ".join(refused) + " not granted")
    return f"Slash commands for agent '{CHAT_AGENT}': " + "; ".join(parts) + ". /help"


def default_tools(home: Path, terminal: Any) -> tuple[list[BaseTool], dict[str, str]]:
    """The memory tools and the camera tool, wired to the real terminal.

    Building them opens and contacts nothing. The camera tool's start key is the
    chat's own terminal, so ``arm`` (the typed /look) counts as the key press.
    """
    from steel.tools import get_camera_tool, get_memory_tools
    from steel.vision.commands import configured_host
    from steel.vision.describe import VisionUnavailable
    from steel.vision.startkey import PictureKey

    tools = list(get_memory_tools(home))
    unavailable: dict[str, str] = {}
    try:
        tools.append(
            get_camera_tool(
                home,
                ollama_host=configured_host(home),
                start_key=PictureKey(terminal),
            )
        )
    except VisionUnavailable as exc:
        unavailable["camera_look"] = f"{exc}."
    return tools, unavailable


def build_slash(
    home: Path,
    session: Any,
    keys: Keys,
    *,
    tools: list[BaseTool] | None = None,
    limits: ChatLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SlashCommands:
    """The slash commands of one chat session (tests pass fake tools and keys)."""
    unavailable: dict[str, str] = {}
    if tools is None:
        tools, unavailable = default_tools(home, keys)
    policy, problem = policy_or_deny_all(home)
    if problem is not None:
        print(
            f"steel: grants file invalid ({problem}); nothing is granted.",
            file=sys.stderr,
        )
    executor = build_executor(tools, policy=policy, agent_id=CHAT_AGENT)
    return SlashCommands(
        home=home,
        session=session,
        executor=executor,
        tools={tool.spec.name: tool for tool in tools},
        policy=policy,
        problem=problem,
        keys=keys,
        limits=limits or chat_limits(),
        clock=clock,
        unavailable=unavailable,
    )
