"""The ``steel`` command.

steel doctor              check pin, Rust extension, config and permissions
steel chat                chat with automatic memory (no tools)
steel memory list         every remembered entry, with ids
steel memory audit        state of every store that can hold conversation text
steel forget ID           delete exactly one entry
steel wipe --confirm      empty every memory store
steel notes ...           the original expiring-note commands
"""

from __future__ import annotations

import argparse
import os
import sys

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
    return 0 if ok else 1


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
    forget = sub.add_parser("forget", help="Delete exactly one entry by id.")
    forget.add_argument("id")
    wipe = sub.add_parser("wipe", help="Empty every memory store.")
    wipe.add_argument("--confirm", action="store_true")
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
    elif args.command == "forget":
        if not memory.forget(args.id):
            print(f"No entry with id {args.id}.", file=sys.stderr)
            return 1
        print(f"Forgot {args.id}.")
    elif args.command == "wipe":
        removed = memory.wipe()
        print("Removed: " + (", ".join(removed) if removed else "nothing"))
        for name, state in memory.audit().items():
            print(f"{name}: {state}")
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
