"""The ``steel`` command.

steel doctor              check pin, Rust extension, config, permissions and memory pressure
steel chat                chat with automatic memory; /search /notes /room /look run tools you grant (the model cannot)
steel memory list         every remembered entry, with ids
steel memory audit        state of every store that can hold conversation text
steel remember TEXT [--days N]    store a trusted fact, or an expiring note (N <= 7)
steel forget ID [--with-related]  delete one entry (and optionally its neighbours)
steel wipe --confirm [--include-audit]   empty every memory store (the policy log is kept unless --include-audit)
steel policy show         read-only: permission grants and the capability list
steel tool list [--agent ID]      the tools, what each needs, allowed or denied (read-only)
steel tool call NAME --agent ID [--args JSON]   run ONE tool call through the policy (no model)
steel voice check|consent|revoke|listen   push-to-talk voice input (docs/VOICE.md); check is read-only
steel chat --voice [--speak]   chat where an empty line means "speak"
steel chat --speak        replies are also read aloud (local Piper; docs/SPEAK.md)
steel chat --timings      one line of stage times after each turn (docs/LATENCY.md)
steel speak check|say "TEXT"   spoken replies: check is read-only, say speaks one sentence
steel vision check|consent|revoke   one-snapshot camera (docs/VISION.md); check is read-only
steel look ["question"] [--save] [--hours N]   ONE picture, described by the local vision model
steel notes ...           the original expiring-note commands
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from steel.pin import PinError, check_openjarvis_pin

if TYPE_CHECKING:
    from steel.chat import ChatSession


def _prepare() -> None:
    """Steps every command runs before touching OpenJarvis or the disk."""
    # Never ask PyPI for a newer OpenJarvis (no network check at start-up).
    os.environ.setdefault("OPENJARVIS_NO_UPDATE_CHECK", "1")
    # No onnxruntime telemetry, in this process or any it starts (steel/offline.py).
    from steel.offline import telemetry_off

    telemetry_off()
    # Fail loudly on any OpenJarvis build other than the pinned one.
    check_openjarvis_pin()


def _doctor() -> int:
    from openjarvis.core.config import load_config

    from steel.home import steel_home
    from steel.pin import OPENJARVIS_COMMIT

    ok = True
    print(f"OpenJarvis pin: OK ({OPENJARVIS_COMMIT[:12]})")
    try:
        import openjarvis_rust  # noqa: F401 - import check only

        print("Rust extension: OK (openjarvis_rust imports)")
    except ImportError as exc:
        ok = False
        print(f"Rust extension: MISSING ({exc})")
    config = load_config()
    checks = {
        "[analytics] enabled = false": not config.analytics.enabled,
        "[traces] enabled = false": not config.traces.enabled,
        "[security] profile = personal": config.security.profile == "personal",
        "[tools] enabled is empty": not config.tools.enabled,
        "[memory] enabled = true": config.memory.enabled,
        "OPENJARVIS_NO_UPDATE_CHECK=1": os.environ.get("OPENJARVIS_NO_UPDATE_CHECK")
        == "1",
    }
    for label, passed in checks.items():
        ok &= passed
        print(f"{label}: {'OK' if passed else 'NO'}")
    home = steel_home()
    mode = oct(home.stat().st_mode & 0o777) if home.exists() else "absent"
    print(f"Home {home}: mode {mode}")
    policy_ok, policy_lines = _policy_lines(home)
    ok &= policy_ok
    print("\n".join(policy_lines))
    # A warning only: a busy machine is not a broken setup.
    from steel.meminfo import memory_report

    print("\n".join(memory_report()))
    return 0 if ok else 1


def _policy_lines(home: Path) -> tuple[bool, list[str]]:
    """Doctor lines for the permission policy. Never writes the audit log."""
    from steel.home import POLICY_FILE
    from steel.security.policy import PolicyError, SteelPolicy

    try:
        policy = SteelPolicy.from_file(home / POLICY_FILE)  # no audit: read only
    except PolicyError as exc:
        # Fail closed: no policy loaded means every call is denied.
        return False, [f"policy: INVALID file, everything is denied ({exc})"]
    # Probe: an agent and a capability nobody granted must be refused.
    probe_denied = not policy.check("doctor-probe", "memory:write", "probe") and (
        not policy.check("doctor-probe", "not:a-capability", "probe")
    )
    grants = sum(len(a.grants) for a in policy.agents().values())
    lines = [
        "policy: deny-by-default active" if probe_denied else "policy: NOT denying",
        f"policy grants: {grants} in {POLICY_FILE}",
    ]
    # Warnings, not failures: nothing here makes a decision looser. They say that
    # a grant may not mean what the file looks like it means.
    advice = {
        "moved": "has moved (grant_moved): it allows nothing",
        "missing": "does not exist: it allows nothing until it does",
        "link": "goes through a symbolic link (resolved at load): moving the link "
        "changes what is granted",
    }
    for agent_id, grant, kind in policy.grant_warnings():
        lines.append(
            f"policy WARNING: {grant.capability} grant for '{agent_id}' on "
            f"{grant.pattern} {advice[kind]}"
        )
    return probe_denied, lines


def _policy_show(home: Path) -> int:
    """``steel policy show``: print grants and vocabulary, change nothing."""
    from steel.home import POLICY_AUDIT_FILE, POLICY_FILE
    from steel.security.audit import PolicyAuditLog
    from steel.security.policy import LEGACY_NAMES, VOCABULARY, PolicyError, SteelPolicy

    path = home / POLICY_FILE
    try:
        policy = SteelPolicy.from_file(path)
    except PolicyError as exc:
        print(f"Policy file {path}: INVALID ({exc}).")
        print("Nothing is loaded, so every tool call is denied.")
        return 1
    print(f"Policy file: {path} ({'present' if path.exists() else 'absent'})")
    log = PolicyAuditLog(home / POLICY_AUDIT_FILE)
    rotated = len(log.rotated_files())
    extra = f", {rotated} rotated file(s)" if rotated else ""
    print(f"Decision log: {log.path} ({log.count()} decision(s){extra})")
    print("Default: deny. A call is allowed only by a grant below.")
    agents = policy.agents()
    print("Agents and grants:" if agents else "Agents and grants: (none)")
    for name, agent in agents.items():
        print(f"  {name}")
        for grant in agent.grants:
            print(f"    grant {grant.capability}  pattern {grant.pattern}")
            if grant.resolved and grant.resolved != grant.pattern:
                print(f"          resolved to {grant.resolved}")
        for denied in agent.deny:
            print(f"    deny  {denied}")
        if not agent.grants and not agent.deny:
            print("    (no grants: everything denied)")
    print("Capabilities:")
    for name, meaning in VOCABULARY.items():
        print(f"  {name:<18} {meaning}")
    print("Never grantable (OpenJarvis names):")
    for old, new in LEGACY_NAMES.items():
        print(f"  {old:<18} use {new}")
    return 0


def _kept_consents(home: Path) -> list[str]:
    """Lines for ``steel wipe``: the consent files are not memory, so wipe keeps them."""
    from steel.vision.consent import consent_path as camera_path
    from steel.voice.consent import consent_path as voice_path

    lines = []
    if os.path.lexists(camera_path(home)):
        lines.append("camera consent: kept (withdraw with: steel vision revoke)")
    if os.path.lexists(voice_path(home)):
        lines.append("voice consent: kept (withdraw with: steel voice revoke)")
    return lines


def _tool_list(home: Path, agent: str | None) -> int:
    """``steel tool list``: tools, needed capabilities, and what the grants allow.

    Read only. The policy is loaded without an audit log, so this cannot write
    one: asking is not a tool call.
    """
    from steel.home import POLICY_FILE
    from steel.security.policy import PolicyError, SteelPolicy
    from steel.tools import get_memory_tools

    try:
        policy = SteelPolicy.from_file(home / POLICY_FILE)
    except PolicyError as exc:
        print(f"Policy file {home / POLICY_FILE}: INVALID ({exc}).")
        print("Nothing is loaded, so every tool call is denied.")
        policy = SteelPolicy()
    agents = [agent] if agent else list(policy.agents())
    if not agents:
        print("No agents in the grants file: every call is denied.")
    for tool in get_memory_tools(home):
        spec = tool.spec
        print(spec.name)
        print(f"  needs: {', '.join(spec.required_capabilities)}")
        print(f"  {spec.description}")
        for name in agents:
            verdicts = [
                policy.preview(name, cap, spec.name)
                for cap in spec.required_capabilities
            ]
            failed = next((reason for ok, reason in verdicts if not ok), "")
            print(f"  agent {name}: " + (f"denied ({failed})" if failed else "allowed"))
    return 0


def _tool_call(home: Path, name: str, agent_id: str, arguments: str) -> int:
    """``steel tool call``: one call through build_executor. No model is involved.

    The decision is written to the audit log (names only, never the arguments
    or the result). An invalid grants file means an empty policy: the call is
    denied, and the denial is logged.
    """
    from openjarvis.core.types import ToolCall

    from steel.home import POLICY_AUDIT_FILE
    from steel.security import (
        PolicyAuditLog,
        PolicyError,
        SteelPolicy,
        load_home_policy,
    )
    from steel.security.executor import build_executor
    from steel.tools import get_memory_tools

    tools = get_memory_tools(home)
    if name not in {tool.spec.name for tool in tools}:
        known = ", ".join(tool.spec.name for tool in tools)
        print(f"steel: unknown tool '{name}'. Tools: {known}", file=sys.stderr)
        return 2
    try:
        policy = load_home_policy(home)
    except PolicyError as exc:
        print(
            f"steel: grants file invalid ({exc}); nothing is granted.", file=sys.stderr
        )
        policy = SteelPolicy(audit=PolicyAuditLog(home / POLICY_AUDIT_FILE))
    try:
        executor = build_executor(tools, policy=policy, agent_id=agent_id)
    except PolicyError as exc:
        print(f"steel: {exc}", file=sys.stderr)
        return 2
    result = executor.execute(ToolCall(id="cli", name=name, arguments=arguments))
    if result.success:
        print(result.content)
        return 0
    print(f"Not run: {result.content}", file=sys.stderr)
    return 1


def _open_chat(config: Any, home: Path) -> ChatSession:
    """open_session, then load the model in the background (docs/LATENCY.md 8)."""
    from steel.chat import open_session
    from steel.warm import keep_alive_setting, num_ctx_setting, start_warmup

    for _value, problem in (keep_alive_setting(), num_ctx_setting()):
        if problem:
            print(f"steel: {problem}", file=sys.stderr)
    session = open_session(config, home)
    # Built here, in the main thread: it reads the memory files.
    session.warmup = start_warmup(
        session.engine, session.model, session.warmup_messages()
    )  # never waits
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="steel",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check pin, Rust extension, config, permissions.")
    chat = sub.add_parser("chat", help="Chat with automatic memory and no tools.")
    chat.add_argument(
        "--voice",
        action="store_true",
        help="Push-to-talk: an empty line records one utterance (needs consent and a grant).",
    )
    chat.add_argument(
        "--speak",
        action="store_true",
        help="Read replies aloud with the local Piper engine (needs the "
        "audio:play grant for agent voice; docs/SPEAK.md).",
    )
    chat.add_argument(
        "--timings",
        action="store_true",
        help="After each turn, print one line of stage times (numbers only; "
        "docs/LATENCY.md).",
    )
    mem = sub.add_parser("memory", help="Inspect memory.")
    mem.add_argument("action", choices=["list", "audit"])
    remember = sub.add_parser(
        "remember", help="Store a fact you state yourself (trusted)."
    )
    remember.add_argument("text")
    remember.add_argument(
        "--days",
        type=float,
        help="Keep it only this many days (up to 7) as an expiring note.",
    )
    forget = sub.add_parser("forget", help="Delete exactly one entry by id.")
    forget.add_argument("id")
    forget.add_argument(
        "--with-related",
        action="store_true",
        help="Also delete related facts (they are always listed first).",
    )
    wipe = sub.add_parser("wipe", help="Empty every memory store.")
    wipe.add_argument("--confirm", action="store_true")
    wipe.add_argument(
        "--include-audit",
        action="store_true",
        help="Also delete the permission-decision log (kept by default).",
    )
    pol = sub.add_parser("policy", help="Inspect the permission policy (read only).")
    pol.add_argument("action", choices=["show"])
    tool = sub.add_parser("tool", help="List or run tools through the policy.")
    tool_sub = tool.add_subparsers(dest="tool_action", required=True)
    tool_list = tool_sub.add_parser("list", help="Tools and what the grants allow.")
    tool_list.add_argument("--agent", help="Show one agent id instead of the file's.")
    tool_call = tool_sub.add_parser("call", help="Run one tool call, no model.")
    tool_call.add_argument("name")
    tool_call.add_argument("--agent", required=True, help="Agent id the grants name.")
    tool_call.add_argument(
        "--args", default="{}", help='JSON object, e.g. \'{"query": "tea"}\''
    )
    voice = sub.add_parser("voice", help="Push-to-talk voice input (local only).")
    voice.add_argument(
        "action",
        choices=["check", "consent", "revoke", "listen"],
        help="check: read-only report; consent / revoke: manage consent; "
        "listen: one push-to-talk capture.",
    )
    speak = sub.add_parser("speak", help="Spoken replies (local Piper engine).")
    speak.add_argument(
        "action",
        choices=["check", "say"],
        help="check: read-only report; say: speak one short sentence.",
    )
    speak.add_argument("text", nargs="?", help="With say: the sentence (200 max).")
    vision = sub.add_parser("vision", help="One-snapshot camera (local only).")
    vision.add_argument(
        "action",
        choices=["check", "consent", "revoke"],
        help="check: read-only report; consent / revoke: manage camera consent.",
    )
    look = sub.add_parser(
        "look", help="Take ONE picture and describe it with the local vision model."
    )
    look.add_argument("question", nargs="?", help="Optional question about the room.")
    look.add_argument(
        "--save",
        action="store_true",
        help="Offer to keep the description as an expiring room note (you confirm).",
    )
    look.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="With --save: how long the note lives (default 24, at most 168).",
    )
    notes = sub.add_parser("notes", help="Original expiring-note commands.")
    notes.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.command == "notes":
        # The original note CLI is unchanged and needs no OpenJarvis.
        from steel.notes_cli import main as notes_main

        return notes_main(args.rest)

    try:
        _prepare()
    except PinError as exc:
        print(f"steel: {exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        return _doctor()

    if args.command == "policy":
        from steel.home import steel_home

        return _policy_show(steel_home())

    if args.command == "tool" and args.tool_action == "list":
        from steel.home import steel_home

        return _tool_list(steel_home(), args.agent)

    if args.command == "voice" and args.action == "check":
        from steel.home import steel_home
        from steel.voice.commands import voice_check

        return voice_check(steel_home())  # read only: no harden(), no microphone

    if args.command == "vision" and args.action == "check":
        from steel.home import steel_home
        from steel.vision.commands import vision_check

        return vision_check(steel_home())  # read only: no harden(), no camera

    if args.command == "speak" and (args.action == "say") != bool(args.text):
        parser.error("say needs one TEXT; check takes none.")

    if args.command == "speak" and args.action == "check":
        from steel.home import steel_home
        from steel.speak.commands import speak_check

        return speak_check(steel_home())  # read only: plays and starts nothing

    if args.command == "wipe" and not args.confirm:
        parser.error("wipe deletes every memory store; add --confirm.")

    if args.command == "look" and args.hours != 24.0 and not args.save:
        parser.error("--hours needs --save.")

    from openjarvis.core.config import load_config

    from steel.home import harden
    from steel.memory import SteelMemory

    # Private home: dir 700, files 600, umask 077 for anything created later.
    home = harden()
    config = load_config()
    memory = SteelMemory(home, max_facts=config.memory.max_facts)

    if args.command == "memory" and args.action == "list":
        entries = memory.entries()
        for entry in entries:
            print(f"{entry.id}\t{entry.store}\t{entry.text}")
        if not entries:
            print("(no memory)")
    elif args.command == "memory":
        for name, state in memory.audit().items():
            print(f"{name}: {state}")
    elif args.command == "remember":
        try:
            added = memory.remember(args.text, days=args.days)
        except ValueError as exc:
            parser.error(str(exc))
        if added.outcome == "duplicate":
            print(f"Already remembered as {added.entry_id}; nothing added.")
        elif added.outcome == "note":
            print(f"Remembered as {added.entry_id} (expires in {args.days:g} days).")
        else:
            print(f"Remembered as {added.entry_id} (trusted).")
    elif args.command == "forget":
        result = memory.forget(args.id, with_related=args.with_related)
        if not result:
            print(f"No entry with id {args.id}.", file=sys.stderr)
            return 1
        print(f"Forgot {args.id}.")
        for entry in result.removed[1:]:
            print(f"Also forgot {entry.id}\t{entry.text}")
        if result.related:
            print("Related entries still stored (add --with-related to remove them):")
            for entry in result.related:
                print(f"  {entry.id}\t{entry.text}")
    elif args.command == "wipe":
        removed = memory.wipe(include_audit=args.include_audit)
        print("Removed: " + (", ".join(removed) if removed else "nothing"))
        for name, state in memory.audit().items():
            print(f"{name}: {state}")
        if args.include_audit:
            print(f"policy log: {memory.policy_log_state()}")
        else:
            print(
                f"policy log: kept, {memory.policy_log_state()} "
                "(--include-audit deletes it)"
            )
        if not memory.is_empty():
            print("Some store is not empty.", file=sys.stderr)
            return 1
        print("All memory stores are empty.")
        for line in _kept_consents(home):
            print(line)
    elif args.command == "tool":
        return _tool_call(home, args.name, args.agent, args.args)
    elif args.command == "voice":
        from steel.voice import commands as voice_commands

        if args.action == "consent":
            return voice_commands.voice_consent(home)
        if args.action == "revoke":
            return voice_commands.voice_revoke(home)
        return voice_commands.voice_listen(home)
    elif args.command == "speak":
        from steel.speak.commands import speak_say

        return speak_say(home, args.text)
    elif args.command == "vision":
        from steel.vision import commands as vision_commands

        if args.action == "consent":
            return vision_commands.vision_consent(home)
        return vision_commands.vision_revoke(home)
    elif args.command == "look":
        from steel.vision import commands as vision_commands

        return vision_commands.look(
            home,
            args.question,
            save=args.save,
            hours=args.hours,
            memory=memory,
        )
    elif args.command == "chat" and args.voice:
        from steel.voice import commands as voice_commands
        from steel.voice.consent import has_consent

        if not has_consent(home):  # before loading a model: nothing to wait for
            return voice_commands.no_consent()
        return voice_commands.run_voice_repl(
            _open_chat(config, home), home, speak=args.speak, timings=args.timings
        )
    elif args.command == "chat":
        from steel.chat import run_repl
        from steel.slash import build_slash
        from steel.voice.terminal import Terminal

        speaker = None
        if args.speak:
            from steel.speak.commands import ChatSpeaker

            speaker = ChatSpeaker(home)
        session = _open_chat(config, home)
        run_repl(
            session,
            build_slash(home, session, Terminal()),
            speaker,
            timings=args.timings,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
