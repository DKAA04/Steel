"""The ``steel`` command.

steel doctor              check pin, Rust extension, config and permissions
steel chat                chat with automatic memory (no tools)
steel memory list         every remembered entry, with ids
steel memory audit        state of every store that can hold conversation text
steel remember TEXT [--days N]    store a trusted fact, or an expiring note (N <= 7)
steel forget ID [--with-related]  delete one entry (and optionally its neighbours)
steel wipe --confirm [--include-audit]   empty every memory store (the policy log is kept unless --include-audit)
steel policy show         read-only: permission grants and the capability list
steel notes ...           the original expiring-note commands
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from steel.pin import PinError, check_openjarvis_pin


def _prepare() -> None:
    """Steps every command runs before touching OpenJarvis or the disk."""
    # Never ask PyPI for a newer OpenJarvis (no network check at start-up).
    os.environ.setdefault("OPENJARVIS_NO_UPDATE_CHECK", "1")
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
    count = PolicyAuditLog(home / POLICY_AUDIT_FILE).count()
    print(f"Decision log: {home / POLICY_AUDIT_FILE} ({count} decision(s))")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="steel",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check pin, Rust extension, config, permissions.")
    sub.add_parser("chat", help="Chat with automatic memory and no tools.")
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

    if args.command == "wipe" and not args.confirm:
        parser.error("wipe deletes every memory store; add --confirm.")

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
    elif args.command == "chat":
        from steel.chat import open_session, run_repl

        run_repl(open_session(config, home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
