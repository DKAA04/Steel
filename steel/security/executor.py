"""The only place STEEL builds an OpenJarvis ``ToolExecutor``.

``build_executor`` refuses everything the stock ToolExecutor would let through
silently (docs/POLICY.md section 1):

- no policy at all (the executor then skips every capability check);
- a tool that declares no capabilities (the executor then checks nothing);
- a file tool without declared path arguments (the executor only sees the
  tool's name, never the path it is about to open).

No tool is registered anywhere in STEEL yet.
"""

from __future__ import annotations

import json
from typing import Any

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor

from steel.security.policy import (
    LEGACY_NAMES,
    PATH_CAPS,
    VOCABULARY,
    PolicyError,
    SteelPolicy,
    paths_verified,
    valid_agent_id,
    valid_tool_name,
)


def _path_params(tool: BaseTool) -> list[str] | None:
    """Names of the arguments that hold file paths (``spec.metadata``), or None."""
    names = tool.spec.metadata.get("path_params")
    if (
        isinstance(names, list)
        and names
        and all(isinstance(n, str) and n for n in names)
    ):
        return list(names)
    return None


def _path_caps(tool: BaseTool) -> list[str]:
    return [c for c in tool.spec.required_capabilities if c in PATH_CAPS]


class SteelToolExecutor(ToolExecutor):
    """ToolExecutor that also checks file-path arguments before running a tool."""

    def __init__(
        self,
        tools: list[BaseTool],
        bus: EventBus | None,
        policy: SteelPolicy,
        agent_id: str,
    ) -> None:
        super().__init__(tools, bus, capability_policy=policy, agent_id=agent_id)
        self._steel_policy = policy

    def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self._tools.get(tool_call.name)
        path_caps = _path_caps(tool) if tool is not None else []
        if tool is None or not path_caps:
            return super().execute(tool_call)  # nothing path-like to check
        # The stock executor only passes the tool NAME to the policy, so the
        # paths are checked here, before anything runs.
        try:
            params: Any = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except ValueError:
            params = None
        names = _path_params(tool)
        if not isinstance(params, dict) or names is None:
            return self._refuse(tool_call, path_caps[0], "Invalid arguments.")
        for cap in path_caps:
            for name in names:
                # A missing argument is passed on as None, which is a deny.
                if not self._steel_policy.check_path(
                    self._agent_id, cap, params.get(name)
                ):
                    return self._refuse(tool_call, cap)
        with paths_verified(self._agent_id, path_caps):
            return super().execute(tool_call)

    def _refuse(
        self, tool_call: ToolCall, capability: str, detail: str = ""
    ) -> ToolResult:
        """A denial shaped like the stock one. It never repeats the path."""
        if self._bus:
            self._bus.publish(
                EventType.CAPABILITY_DENIED,
                {
                    "agent_id": self._agent_id,
                    "capability": capability,
                    "tool": tool_call.name,
                },
            )
        return ToolResult(
            tool_name=tool_call.name,
            content=(
                f"Capability '{capability}' denied for agent '{self._agent_id}'"
                f" on tool '{tool_call.name}'. {detail}".rstrip()
            ),
            success=False,
        )


def build_executor(
    tools: list[BaseTool],
    *,
    policy: SteelPolicy | None,
    agent_id: str,
    bus: EventBus | None = None,
) -> SteelToolExecutor:
    """Build a ToolExecutor that every call must pass the policy to run.

    Raises PolicyError if the policy, the agent id or any tool is not acceptable.
    """
    if not isinstance(policy, SteelPolicy):
        # Also refuses OpenJarvis' CapabilityPolicy, which is open by default.
        raise PolicyError("a SteelPolicy is required to run tools")
    if policy.audit is None:
        raise PolicyError("the policy needs an audit log: no log, no tool calls")
    if not valid_agent_id(agent_id):
        raise PolicyError("agent_id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
    seen: set[str] = set()
    for tool in tools:
        name = tool.spec.name
        if not valid_tool_name(name) or name in seen:
            raise PolicyError(
                "tool names must be unique and match [A-Za-z0-9_.-]{1,64}"
            )
        seen.add(name)
        declared = tool.spec.required_capabilities
        if not isinstance(declared, list) or not declared:
            raise PolicyError(f"tool '{name}' declares no capabilities; refusing it")
        for cap in declared:
            if not isinstance(cap, str):
                raise PolicyError(f"tool '{name}' declares a malformed capability")
            if cap in LEGACY_NAMES:
                raise PolicyError(f"tool '{name}' uses the legacy name '{cap}'")
            if cap not in VOCABULARY:
                raise PolicyError(f"tool '{name}' declares an unknown capability")
        if _path_caps(tool) and _path_params(tool) is None:
            raise PolicyError(
                f"tool '{name}' uses files but declares no metadata['path_params']"
            )
    return SteelToolExecutor(tools, bus, policy, agent_id)
