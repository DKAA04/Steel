# SteelPolicy: permission policy for tool calls (design)

Status: **design, written before any code.** This document says what `steel/security/policy.py` and `steel/security/executor.py` must do. A statement here is only "implemented" once a named test proves it; the test table at the end is filled in when the code exists. **No tool is registered in STEEL.**

Sources read in full, at OpenJarvis commit `108d257188cbad85f2e50209d9a61669ea4dec33`: `src/openjarvis/security/capabilities.py`, `src/openjarvis/tools/_stubs.py` (this is where `ToolExecutor` is defined).

## 1. What `ToolExecutor` expects (quoted from the source)

Constructor (`tools/_stubs.py`):

```python
def __init__(
    self,
    tools: List[BaseTool],
    bus: Optional[EventBus] = None,
    *,
    interactive: bool = False,
    confirm_callback: Optional[Callable[[str], bool]] = None,
    default_timeout: float = 30.0,
    capability_policy: Optional[Any] = None,
    agent_id: str = "",
    boundary_guard: Optional[Any] = None,
    rate_limiter: Optional[Any] = None,
) -> None:
```

The only place the policy is used (`ToolExecutor.execute`):

```python
required_capabilities = list(tool.spec.required_capabilities)
if self._capability_policy is not None:
    from openjarvis.security.capabilities import canonical_tool_capabilities

    for cap in canonical_tool_capabilities(tool):
        cap_value = cap.value if hasattr(cap, "value") else cap
        if cap_value not in required_capabilities:
            required_capabilities.append(cap_value)

if self._capability_policy is not None:
    for cap in required_capabilities:
        if not self._capability_policy.check(
            self._agent_id,
            cap,
            tool_call.name,
        ):
            ...
            return ToolResult(..., success=False)
```

So the interface is:

| | |
| --- | --- |
| Method | `check` |
| Arguments | `(agent_id: str, capability: str, resource: str)`, passed **positionally**, in that order |
| Return | truthy = allowed, falsy = denied. STEEL returns a real `bool` |
| `agent_id` | the executor's constructor argument, `""` by default |
| `capability` | one string from the tool's `ToolSpec.required_capabilities` plus the OpenJarvis floor from `canonical_tool_capabilities` |
| `resource` | **the tool's name** (`tool_call.name`), not a file path, URL or argument value |
| Denial | `ToolResult(success=False)` with text `Capability '<cap>' denied for agent '<id>' on tool '<name>'.` and a `CAPABILITY_DENIED` event on the bus |

### Four facts in that code that shape this design

1. **No policy means no check.** If `capability_policy is None` the whole block is skipped. `build_executor` must therefore refuse to run without a policy.
2. **A tool with no required capabilities is never checked.** The loop runs zero times, so a tool whose `ToolSpec.required_capabilities` is empty runs even under a strict policy. `canonical_tool_capabilities` does not help: for a non-OpenJarvis tool whose name is not in `DEFAULT_TOOL_CAPABILITIES` it returns `[]` ("Third-party tools remain governed by their ToolSpec"). `build_executor` must therefore refuse a tool that declares no capabilities.
3. **`resource` is the tool name, so the executor cannot enforce file-path rules by itself.** A policy can decide "may this tool use `fs:read`", but not "may it read `/etc/passwd`": the path is inside `tool_call.arguments`, which the policy never sees. STEEL's executor wrapper (section 5) checks declared path arguments itself before calling `ToolExecutor.execute`.
4. **Everything else is optional and off.** `boundary_guard`, `rate_limiter`, `confirm_callback` are `None` unless passed. Taint tracking and the output injection scanner are built into `ToolExecutor` but only act on data that looks like PII or secrets and on non-local tools. None of them replaces the permission policy.

What OpenJarvis' own `CapabilityPolicy` does that STEEL must not copy:

- **Open by default** (`default_deny=False`): an agent with no policy is allowed everything.
- **`_default` fallback agent**: an unknown agent inherits the grants of an agent called `_default`.
- **Glob capabilities and `fnmatch` resource patterns** (`*`, `?`, `[seq]`), with no path normalisation.
- **Rust implementation** used by `check()` (the Python `_check_python` is "kept for reference only").

STEEL ports the pure-Python `_check_python` structure (per-agent grants, explicit denials win, then grants, then default) and the strict policy-file validation of `_load_file`, with the default inverted to **deny**.

## 2. Capability vocabulary

Each capability has exactly one name. A name that is not in this table is **unknown and denied**.

| Capability | Meaning | Origin |
| --- | --- | --- |
| `memory:read:facts` | read durable facts (`memory_facts.jsonl`, `MEMORY.md`, `USER.md`), including facts about other people | STEEL |
| `memory:read:notes` | read expiring notes (`steel_notes.sqlite3`), any `source` | STEEL |
| `memory:write` | add facts or notes | OpenJarvis name, kept |
| `sensor:camera` | read the camera | STEEL |
| `sensor:microphone` | read the microphone | STEEL |
| `net:outbound` | open network connections | STEEL |
| `fs:read` | read files (needs a path grant) | STEEL |
| `fs:write` | write files (needs a path grant) | STEEL |
| `shell:exec` | run commands or code | STEEL |
| `tool:invoke` | the minimum declaration for a tool that touches nothing else | OpenJarvis name, kept |
| `channel:send` | send to a messaging channel | OpenJarvis name, kept |
| `schedule:create` | create or change scheduled tasks | OpenJarvis name, kept |
| `system:admin` | manage agents and channels | OpenJarvis name, kept |

**Legacy OpenJarvis names that STEEL renames or splits are recognised but never grantable.** They are denied with reason `legacy_name`:

| OpenJarvis name | Use instead |
| --- | --- |
| `file:read`, `file:write` | `fs:read`, `fs:write` |
| `network:fetch` | `net:outbound` |
| `code:execute` | `shell:exec` |
| `memory:read` | `memory:read:facts` and/or `memory:read:notes` |

Why: `canonical_tool_capabilities` adds this floor to any OpenJarvis built-in tool (`file_read` always needs `file:read`). If those names were grantable, a coarse `file:read` grant would satisfy the executor without any path rule. Denying them means an OpenJarvis built-in can only run if STEEL wraps it in its own tool that declares the STEEL names.

`DEFAULT_TOOL_CAPABILITIES` is otherwise **not** used: STEEL does not register OpenJarvis built-in tools. Safe-by-inventory entries such as `calculator` have an empty list and would be refused by the "must declare capabilities" rule.

## 3. Rules

### 3.1 Deny by default (fail closed)

A call is allowed only if a grant matches. Each of these is a **deny**:

| Situation | Reason code |
| --- | --- |
| `agent_id` empty, not a string, or not `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}` | `bad_agent_id` |
| agent has no entry in the policy | `unknown_agent` |
| capability not a string, or not in the vocabulary | `unknown_capability` |
| capability is a legacy OpenJarvis name | `legacy_name` |
| resource empty, not a string, or not a valid tool name `[A-Za-z0-9_.-]{1,64}` | `bad_resource` |
| agent has the capability in its `deny` list | `explicit_deny` |
| no grant for this capability and resource | `no_grant` |
| only a wildcard grant exists for a sensor | `wildcard_sensor` |
| path not allowed by any grant | `path_outside_grants` |
| path malformed, relative, contains `..`, or has a NUL/control character | `bad_resource` |
| `fs:*` asked through `check()` without a verified path | `path_not_checked` |
| any exception inside the decision code | `error` |
| the decision could not be written to the audit log | `audit_failed` (an allow becomes a deny) |

An exception is caught inside `check()` and turned into `False`; it never propagates as "allowed".

### 3.2 Grants

A grant is `(agent_id, capability, pattern)`, loaded from `~/.openjarvis/steel_policy.json` (mode 600, hand-edited; there is no `steel policy grant` command). The file format mirrors OpenJarvis':

```json
{"agents": [{"agent_id": "room", "grants": [{"capability": "memory:read:notes", "pattern": "*"}], "deny": []}]}
```

- **No file = no grants.** A malformed file, a symlink, or a file writable by group/others means **no policy is loaded and every call is denied**. `steel policy show` and `steel doctor` report that state.
- **Capability field**: an exact vocabulary name, or a family wildcard `memory:*`, or `*`. Wildcards exist so a grant can cover a family, but see 3.3. Legacy names and unknown names are rejected when the file is loaded.
- **Pattern for non-path capabilities**: `*` (any tool) or one exact tool name. There is no glob syntax (no `?`, no `[seq]`), so a pattern means what it says. It is matched against `resource`, which `ToolExecutor` fills with the tool name.
- **Pattern for `fs:read` / `fs:write`**: one absolute directory or file path (not `/`, no `..`). See 3.4.
- **`deny`**: capability names (exact, family or `*`) that always win over any grant for that agent.
- Grants are **per agent id**; there is no `_default` agent and no inheritance.

### 3.3 Sensors

`sensor:camera` and `sensor:microphone` are never covered by a wildcard capability (`*`, `sensor:*`), and never by a resource pattern of `*`. Each needs a grant that names the capability exactly **and** one exact tool name:

```json
{"capability": "sensor:camera", "pattern": "camera_snapshot"}
```

`sensor:*`, and a sensor grant with pattern `*`, are rejected when the file is loaded (the whole file is then refused). A bare `*` grant loads, but simply does not cover sensors; `check()` skips wildcard grants for sensors even if one were added in code (defence in depth), and the decision is `wildcard_sensor` when that was the only grant.

### 3.4 Paths (`fs:read`, `fs:write`)

Wildcard capability grants (`*`, `fs:*`) never cover path capabilities, because a wildcard has no path to match: each path grant names `fs:read` or `fs:write` exactly, with an absolute path.

To check a path (`SteelPolicy.check_path(agent_id, capability, path)`):

1. The path must be a string, absolute, without NUL or control characters. Relative paths and `~` are denied (nothing is expanded).
2. **Any `..` component is rejected outright**, even if it would stay inside the grant.
3. The path is resolved with `os.path.realpath` **after** that, so every symlink is followed before matching. A not-yet-existing file (a write) is resolved through its existing parent.
4. The grant path is resolved the same way at check time.
5. The match is by path **components**: the resolved path equals the grant or lies under it (`Path.is_relative_to`). Never a string prefix: grant `/home/a` does not match `/home/ab/x`.
6. A symlink inside a granted directory that points outside is therefore denied.

Because `ToolExecutor` only passes the tool name, STEEL's executor wrapper does this check (section 5). `SteelPolicy.check()` answers `fs:*` only while that wrapper has verified the path for the current call; otherwise `path_not_checked`. A plain `ToolExecutor` given a `SteelPolicy` therefore denies every file tool.

### 3.5 Audit log

Every decision, allow or deny, from `check()` and `check_path()`, is appended as one JSON line to `~/.openjarvis/steel_policy_audit.jsonl`:

```json
{"ts": "2026-10-23T12:00:00+00:00", "agent_id": "room", "capability": "memory:read:notes", "decision": "allow", "reason": "granted"}
```

- Exactly those five fields. **Never the resource** (tool name, path, arguments) and never user text or tool output.
- Values that came from the caller and are not validated are not echoed: an agent id that fails the pattern is logged as `(invalid)`, a capability outside the vocabulary as `(unknown)`.
- Created with mode 600 (`O_CREAT|O_APPEND|O_NOFOLLOW`, then `fchmod 600`).
- **No log, no action**: if the line cannot be written, an allow is turned into a deny (`audit_failed`).
- `steel wipe` **keeps** this file; `steel wipe --confirm --include-audit` deletes it. It holds no text, only names and reason codes.
- It is a plain file: not encrypted, not tamper-evident, not rotated (it grows without limit).

## 4. What this policy does NOT do

- **It is not a sandbox.** It decides only for calls that go through `ToolExecutor` built by `build_executor`. A tool's `execute()` is ordinary Python in the STEEL process; once it runs, nothing here limits what it does. Tools are trusted code and must be reviewed as such.
- **Anything in the process can bypass it** by importing `ToolExecutor` or calling a library directly. A test fails if STEEL source constructs a `ToolExecutor` anywhere except `executor.py`, but that is a check on STEEL's own code, not enforcement.
- **Path checks are time-of-check.** A symlink swapped between the check and the tool opening the file is not caught.
- **Path checks apply only to arguments the tool declares as paths** (`path_params`, top-level string arguments). A tool that builds paths from other input, or opens files by other means, is not covered. A tool declaring `fs:*` without `path_params` is refused at registration.
- **The resource pattern for other capabilities is the tool name**, not what the tool does with its arguments. `net:outbound` does not restrict hosts; `memory:read:notes` does not restrict which notes; `shell:exec` does not restrict commands. Finer rules need finer tools.
- **Tool timeouts do not stop a tool.** `ToolExecutor` abandons a slow tool but its thread keeps running.
- **Events carry data.** `ToolExecutor` publishes tool arguments and up to 10 kB of each result on the event bus. STEEL subscribes nothing that stores them today; that must be re-checked when tools exist.
- **The policy file is the user's.** Anyone who can write `steel_policy.json` (same user, root) can grant anything. STEEL only refuses a file that is a symlink or writable by group/others.
- **No user confirmation flow.** `requires_confirmation` tools simply fail in `build_executor` executors (no callback is wired).
- **The audit log can be edited or deleted** by anyone who can write the home directory.

## 5. Executor factory

`steel/security/executor.py::build_executor(tools, *, policy, agent_id, bus=None)` is the **only place STEEL constructs a `ToolExecutor`**. It returns a thin subclass of `ToolExecutor` and refuses to build if:

- `policy` is missing or not a `SteelPolicy` (an OpenJarvis `CapabilityPolicy` is open by default and is refused);
- `agent_id` fails the id pattern;
- two tools share a name, or a name is not `[A-Za-z0-9_.-]{1,64}`;
- a tool's `spec.required_capabilities` is empty, not a list of strings, or contains a name outside the vocabulary or a legacy name;
- a tool declares `fs:read` or `fs:write` but `spec.metadata["path_params"]` is not a non-empty list of argument names.

The subclass overrides `execute`: for tools with path capabilities it parses the arguments, requires every declared `path_params` argument to be present and a string, runs `check_path` for each, and only then lets `ToolExecutor.execute` run (which calls `check` for the capabilities). It adds no other behaviour.

## 6. Open questions

Recorded for the owner; the code takes the conservative side of each.

1. A bare `*` grant covers `shell:exec`, `net:outbound`, `memory:write` and `system:admin` (but never sensors or paths). Should wildcards be limited to one family (`memory:*`)?
2. Legacy OpenJarvis names are denied rather than aliased. Is that the wanted behaviour?
3. The audit log is not rotated.

## 7. Evidence

Filled in with the implementation: each rule above is listed with the test that proves it. Until then nothing in this document is claimed as implemented.
