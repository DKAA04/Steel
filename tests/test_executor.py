"""build_executor: the only ToolExecutor STEEL builds, and what it refuses.

Uses fake harmless tools. No real tool exists in STEEL.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

from steel.security import PolicyAuditLog, PolicyError, SteelPolicy
from steel.security.executor import SteelToolExecutor, build_executor


class FakeTool(BaseTool):
    """Does nothing but count how often it was really run."""

    tool_id = "fake"

    def __init__(
        self,
        name: str = "harmless",
        caps: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._spec = ToolSpec(
            name=name,
            description="A fake tool for tests.",
            required_capabilities=["tool:invoke"] if caps is None else caps,
            metadata=metadata or {},
        )
        self.calls: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, **params: Any) -> ToolResult:
        self.calls.append(params)
        return ToolResult(tool_name=self._spec.name, content="done")


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=json.dumps(arguments))


class ExecutorCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(os.path.realpath(self._tmp.name))
        self.log_path = self.root / "audit.jsonl"
        self.policy = SteelPolicy(audit=PolicyAuditLog(self.log_path))

    def log(self) -> list[dict[str, str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(x) for x in self.log_path.read_text().splitlines()]


class DenyThroughRealExecutorTest(ExecutorCase):
    def test_a_call_with_no_grant_is_denied_and_the_tool_never_runs(self) -> None:
        tool = FakeTool()
        executor = build_executor([tool], policy=self.policy, agent_id="room")
        result = executor.execute(call("harmless"))
        self.assertFalse(result.success)
        self.assertIn("denied", result.content)
        self.assertEqual(tool.calls, [])
        self.assertEqual(
            [(e["decision"], e["reason"]) for e in self.log()],
            [("deny", "unknown_agent")],
        )

    def test_the_same_tool_runs_once_it_is_granted(self) -> None:
        tool = FakeTool()
        self.policy.grant("room", "tool:invoke", "harmless")
        executor = build_executor([tool], policy=self.policy, agent_id="room")
        result = executor.execute(call("harmless", x=1))
        self.assertTrue(result.success)
        self.assertEqual(result.content, "done")
        self.assertEqual(tool.calls, [{"x": 1}])
        self.assertEqual(self.log()[-1]["decision"], "allow")

    def test_a_grant_for_another_agent_or_tool_does_not_help(self) -> None:
        tool = FakeTool()
        self.policy.grant("kitchen", "tool:invoke", "harmless")
        self.policy.grant("room", "tool:invoke", "some_other_tool")
        executor = build_executor([tool], policy=self.policy, agent_id="room")
        self.assertFalse(executor.execute(call("harmless")).success)
        self.assertEqual(tool.calls, [])

    def test_every_declared_capability_must_be_granted(self) -> None:
        tool = FakeTool(caps=["tool:invoke", "net:outbound"])
        self.policy.grant("room", "tool:invoke")
        executor = build_executor([tool], policy=self.policy, agent_id="room")
        self.assertFalse(executor.execute(call("harmless")).success)
        self.assertEqual(tool.calls, [])
        self.policy.grant("room", "net:outbound")
        self.assertTrue(executor.execute(call("harmless")).success)

    def test_a_denial_is_published_on_the_event_bus(self) -> None:
        seen: list[Any] = []
        bus = EventBus()
        bus.subscribe(EventType.CAPABILITY_DENIED, seen.append)
        executor = build_executor(
            [FakeTool()], policy=self.policy, agent_id="room", bus=bus
        )
        executor.execute(call("harmless"))
        self.assertEqual(len(seen), 1)

    def test_the_policy_receives_agent_capability_and_tool_name_in_that_order(
        self,
    ) -> None:
        # Pins the ToolExecutor interface quoted in docs/POLICY.md section 1.
        seen: list[tuple[Any, ...]] = []

        class Spy(SteelPolicy):
            def check(self, agent_id: str, capability: str, resource: str = "") -> bool:
                seen.append((agent_id, capability, resource))
                return super().check(agent_id, capability, resource)

        spy = Spy(audit=PolicyAuditLog(self.log_path))
        build_executor([FakeTool()], policy=spy, agent_id="room").execute(
            call("harmless")
        )
        self.assertEqual(seen, [("room", "tool:invoke", "harmless")])

    def test_unknown_tool_and_bad_arguments_never_run_a_tool(self) -> None:
        tool = FakeTool()
        self.policy.grant("room", "tool:invoke")
        executor = build_executor([tool], policy=self.policy, agent_id="room")
        self.assertFalse(executor.execute(call("missing")).success)
        self.assertFalse(
            executor.execute(
                ToolCall(id="1", name="harmless", arguments="{oops")
            ).success
        )
        self.assertEqual(tool.calls, [])


class RegistrationRefusalTest(ExecutorCase):
    def refuses(self, tools: list[BaseTool], **kwargs: Any) -> str:
        options: dict[str, Any] = {"policy": self.policy, "agent_id": "room"}
        options.update(kwargs)
        with self.assertRaises(PolicyError) as caught:
            build_executor(tools, **options)
        return str(caught.exception)

    def test_it_refuses_to_run_without_a_policy(self) -> None:
        self.refuses([FakeTool()], policy=None)

    def test_it_refuses_a_policy_that_is_not_a_steel_policy(self) -> None:
        class OpenPolicy:  # like OpenJarvis' default: allows everything
            def check(self, *args: Any) -> bool:
                return True

        self.refuses([FakeTool()], policy=OpenPolicy())

    def test_it_refuses_a_policy_without_an_audit_log(self) -> None:
        self.refuses([FakeTool()], policy=SteelPolicy())

    def test_it_refuses_a_tool_without_declared_capabilities(self) -> None:
        message = self.refuses([FakeTool(caps=[])])
        self.assertIn("harmless", message)
        self.assertIn("no capabilities", message)

    def test_it_refuses_unknown_legacy_and_malformed_capabilities(self) -> None:
        for caps in (
            ["memory:delete"],
            ["file:read"],
            ["tool:invoke", "network:fetch"],
            [5],
            ["*"],
        ):
            self.refuses([FakeTool(caps=caps)])  # type: ignore[arg-type]

    def test_it_refuses_a_file_tool_without_path_params(self) -> None:
        for metadata in (
            None,
            {},
            {"path_params": []},
            {"path_params": "path"},
            {"path_params": [1]},
        ):
            self.refuses([FakeTool(caps=["fs:read"], metadata=metadata)])

    def test_it_refuses_bad_names_duplicates_and_agent_ids(self) -> None:
        self.refuses([FakeTool(name="bad name")])
        self.refuses([FakeTool(name="a"), FakeTool(name="a")])
        for agent in ("", "a b", "../x", "x" * 65):
            self.refuses([FakeTool()], agent_id=agent)

    def test_a_refused_registration_builds_no_executor(self) -> None:
        with self.assertRaises(PolicyError):
            build_executor([FakeTool(caps=[])], policy=self.policy, agent_id="room")
        self.assertEqual(self.log(), [])


class StockExecutorGapsTest(ExecutorCase):
    """Facts about OpenJarvis' own ToolExecutor that docs/POLICY.md relies on."""

    def test_stock_executor_runs_a_tool_with_no_capabilities_under_a_strict_policy(
        self,
    ) -> None:
        tool = FakeTool(caps=[])
        stock = ToolExecutor([tool], capability_policy=self.policy, agent_id="room")
        self.assertTrue(stock.execute(call("harmless")).success)
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(self.log(), [])  # the policy was never even asked

    def test_stock_executor_without_a_policy_checks_nothing(self) -> None:
        tool = FakeTool(caps=["shell:exec"])
        self.assertTrue(ToolExecutor([tool]).execute(call("harmless")).success)

    def test_stock_executor_with_steel_policy_still_denies_file_tools(self) -> None:
        allowed = self.root / "ok"
        allowed.mkdir()
        self.policy.grant("room", "fs:read", str(allowed))
        tool = FakeTool(
            name="reader", caps=["fs:read"], metadata={"path_params": ["path"]}
        )
        stock = ToolExecutor([tool], capability_policy=self.policy, agent_id="room")
        result = stock.execute(call("reader", path=str(allowed / "f.txt")))
        self.assertFalse(result.success)
        self.assertEqual(tool.calls, [])
        self.assertEqual(self.log()[-1]["reason"], "path_not_checked")


class PathToolTest(ExecutorCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok = self.root / "ok"
        self.ok.mkdir()
        (self.root / "other").mkdir()
        (self.ok / "link").symlink_to(self.root / "other")
        self.tool = FakeTool(
            name="reader", caps=["fs:read"], metadata={"path_params": ["path"]}
        )
        self.policy.grant("room", "fs:read", str(self.ok))
        self.executor = build_executor([self.tool], policy=self.policy, agent_id="room")

    def run_with(self, **arguments: Any) -> ToolResult:
        return self.executor.execute(call("reader", **arguments))

    def test_a_path_inside_the_grant_runs(self) -> None:
        self.assertTrue(self.run_with(path=str(self.ok / "f.txt")).success)
        self.assertEqual(len(self.tool.calls), 1)

    def test_paths_outside_traversing_or_through_symlinks_are_denied(self) -> None:
        for path in (
            "/etc/passwd",
            str(self.root / "other" / "f"),
            f"{self.ok}/../other/f",
            str(self.ok / "link" / "f"),
            "relative.txt",
            f"{self.ok}/f\x00.txt",
            str(self.root / "ok2" / "f"),
        ):
            result = self.run_with(path=path)
            self.assertFalse(result.success, path)
            self.assertNotIn(str(self.root), result.content)  # never echoes the path
        self.assertEqual(self.tool.calls, [])

    def test_missing_or_mistyped_path_argument_is_denied(self) -> None:
        for arguments in (
            {},
            {"path": None},
            {"path": 5},
            {"path": [str(self.ok)]},
            {"other": str(self.ok)},
        ):
            self.assertFalse(self.run_with(**arguments).success, arguments)
        self.assertFalse(
            self.executor.execute(
                ToolCall(id="1", name="reader", arguments="{oops")
            ).success
        )
        self.assertFalse(
            self.executor.execute(
                ToolCall(id="1", name="reader", arguments="[1]")
            ).success
        )
        self.assertEqual(self.tool.calls, [])

    def test_a_write_tool_needs_a_write_grant_and_every_path_argument_is_checked(
        self,
    ) -> None:
        mover = FakeTool(
            name="mover",
            caps=["fs:read", "fs:write"],
            metadata={"path_params": ["src", "dst"]},
        )
        self.policy.grant("room", "fs:write", str(self.ok))
        executor = build_executor([mover], policy=self.policy, agent_id="room")
        good = {"src": str(self.ok / "a"), "dst": str(self.ok / "b")}
        self.assertTrue(executor.execute(call("mover", **good)).success)
        bad = {"src": str(self.ok / "a"), "dst": "/etc/cron.d/x"}
        self.assertFalse(executor.execute(call("mover", **bad)).success)
        self.assertEqual(len(mover.calls), 1)

    def test_the_verification_does_not_leak_to_the_next_call(self) -> None:
        self.assertTrue(self.run_with(path=str(self.ok / "f")).success)
        # Outside the executor nothing is verified any more.
        self.assertFalse(self.policy.check("room", "fs:read", "reader"))


def _executor_names(tree: ast.AST) -> list[str]:
    """Code (not comments or docstrings) that names ToolExecutor."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.endswith("ToolExecutor"):
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr.endswith("ToolExecutor"):
            found.append(node.attr)
        elif isinstance(node, ast.alias) and node.name.endswith("ToolExecutor"):
            found.append(node.name)
    return found


class OnlyPlaceTest(unittest.TestCase):
    def test_steel_names_a_tool_executor_only_in_executor_py(self) -> None:
        source = Path(__file__).resolve().parent.parent / "steel"
        offenders = []
        for path in source.rglob("*.py"):
            if path.name == "executor.py" and path.parent.name == "security":
                continue
            if _executor_names(ast.parse(path.read_text(encoding="utf-8"))):
                offenders.append(str(path.relative_to(source)))
        self.assertEqual(offenders, [])
        # The scan does see a real use, so an empty result is not a blind spot.
        self.assertTrue(
            _executor_names(ast.parse("from x import ToolExecutor\nToolExecutor([])"))
        )
        self.assertFalse(
            _executor_names(ast.parse('"""ToolExecutor in a docstring"""'))
        )
        self.assertTrue(issubclass(SteelToolExecutor, ToolExecutor))


# Methods the pinned ToolExecutor has that never run a tool. A new method is not
# on this list, so it fails the test below until someone has read it.
HARMLESS_TOOL_EXECUTOR_METHODS = frozenset(
    {
        "__init__",
        "begin_session",
        "available_tools",
        "get_openai_tools",
        "_json_safe_metadata",
    }
)


def _methods_touching_tool_execute(cls: type) -> dict[str, list[int]]:
    """Method name -> lines where the method uses ``.execute`` or the tool runner.

    A bare ``tool.execute`` counts, not only ``tool.execute(...)``: the stock
    executor hands the function to a thread pool (``submit(tool.execute, ...)``)
    and never calls it directly.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        lines = [
            sub.lineno
            for sub in ast.walk(node)
            if (isinstance(sub, ast.Attribute) and sub.attr == "execute")
            or (isinstance(sub, ast.Name) and sub.id == "_TOOL_RUNNER")
            or (isinstance(sub, ast.Constant) and sub.value == "execute")
        ]
        if lines:
            found[node.name] = lines
    return found


class NoOtherWayToRunAToolTest(unittest.TestCase):
    """If OpenJarvis adds a second method that runs tools, the policy check in
    ``execute`` could be bypassed through it. This fails when that happens."""

    def test_only_execute_runs_a_tool(self) -> None:
        touching = _methods_touching_tool_execute(ToolExecutor)
        self.assertEqual(
            sorted(touching),
            ["execute"],
            "another ToolExecutor method reaches tool.execute; read it and decide",
        )

    def test_every_other_method_is_a_known_harmless_one(self) -> None:
        own = {
            name
            for name, member in vars(ToolExecutor).items()
            if inspect.isfunction(member) or isinstance(member, staticmethod)
        }
        unknown = own - HARMLESS_TOOL_EXECUTOR_METHODS - {"execute"}
        self.assertEqual(unknown, set(), "ToolExecutor has a method nobody reviewed")

    def test_the_scan_sees_each_way_of_reaching_a_tool(self) -> None:
        # Positive controls: the scan finds a call, a bare reference and a
        # string lookup, so an empty result is not a blind spot.
        class Calls:
            def go(self, tool: BaseTool) -> None:
                tool.execute()

        class Passes:
            def go(self, tool: BaseTool, pool: Any) -> None:
                pool.submit(tool.execute)

        class Looks:
            def go(self, tool: BaseTool) -> None:
                getattr(tool, "execute")  # noqa: B009

        class Harmless:
            def go(self, tool: BaseTool) -> str:
                return tool.spec.name

        for cls in (Calls, Passes, Looks):
            self.assertEqual(list(_methods_touching_tool_execute(cls)), ["go"], cls)
        self.assertEqual(_methods_touching_tool_execute(Harmless), {})

    def test_our_subclass_adds_no_other_way_to_run_a_tool(self) -> None:
        # SteelToolExecutor reaches the tool only through super().execute.
        touching = _methods_touching_tool_execute(SteelToolExecutor)
        self.assertEqual(sorted(touching), ["execute"])
        public = {
            name
            for name in vars(SteelToolExecutor)
            if not name.startswith("_") and name != "execute"
        }
        self.assertEqual(public, set())


if __name__ == "__main__":
    unittest.main()
