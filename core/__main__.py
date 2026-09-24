"""Offline STEEL prototype. No network, microphone, camera or shell execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memory.store import MemoryStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Optional local SQLite file.")
    parser.add_argument("--allow-persistence", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Exercise temporary memory with invented notes.")
    commands.add_parser("status", help="Show implemented and planned capabilities.")
    remember = commands.add_parser(
        "remember", help="Store an explicitly supplied note."
    )
    remember.add_argument("text")
    remember.add_argument("--ttl-hours", type=float, default=24.0)
    recall = commands.add_parser("recall", help="Search locally stored notes.")
    recall.add_argument("query", nargs="?", default="")
    forget = commands.add_parser("forget", help="Delete one note by its ID.")
    forget.add_argument("id")
    forget.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(
            json.dumps(
                {
                    "implemented": [
                        "offline CLI",
                        "opt-in SQLite memory",
                        "note expiry",
                    ],
                    "planned": [
                        "voice",
                        "vision",
                        "LLM orchestration",
                        "mobile",
                        "security tools",
                    ],
                },
                indent=2,
            )
        )
        return 0
    if args.command == "demo":
        with MemoryStore() as store:
            note = store.remember("Synthetic demo: inspect the lab sensor at 14:00.")
            print(
                json.dumps(
                    {"storage": "temporary RAM", "matches": store.recall("sensor")},
                    indent=2,
                )
            )
            store.forget(note)
            print(json.dumps({"notes_after_forget": len(store.recall())}))
        return 0
    if args.db is None:
        parser.error(
            "Use --db and --allow-persistence for notes across commands; demo uses RAM."
        )
    if args.command == "forget" and not args.confirm:
        parser.error("Deletion requires --confirm and a specific note ID.")
    try:
        with MemoryStore(args.db, allow_persistence=args.allow_persistence) as store:
            if args.command == "remember":
                print(
                    json.dumps(
                        {"id": store.remember(args.text, ttl_hours=args.ttl_hours)}
                    )
                )
            elif args.command == "recall":
                print(json.dumps(store.recall(args.query), indent=2))
            elif args.command == "forget":
                print(json.dumps({"deleted": store.forget(args.id)}))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
