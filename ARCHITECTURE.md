# Architecture

## Phases 1 and 1b: persistent memory, no tools

```mermaid
flowchart LR
    U[steel chat] --> S[ChatSession: RAM history]
    S -->|system prompt + trusted facts + live notes + MEMORY.md/USER.md| G[OpenJarvis guardrails: redact secrets/PII]
    G --> O[Ollama qwen3.5:2b]
    S -->|user text only, on shared EventBus| M[OpenJarvis MemoryService thread]
    M --> X[SteelFactExtractor + InjectionScanner]
    X --> F[SteelFactStore]
    F -->|not forgotten, not a near-duplicate, durable| J[memory_facts.jsonl]
    F -->|time-bound, 7-day TTL| N[steel_notes.sqlite3]
    R[steel remember] --> F
    C[steel memory list / forget / wipe] --> SM[SteelMemory]
    SM --> J
    SM --> N
    SM --> MD[MEMORY.md / USER.md]
    SM -->|wipe only| DB[OpenJarvis DBs that can hold text]
```

| Module | Role |
| --- | --- |
| `steel/cli.py` | `steel` entry point. Checks the pin, sets `OPENJARVIS_NO_UPDATE_CHECK=1`, hardens permissions |
| `steel/pin.py` | Refuses any OpenJarvis other than `1.0.5.dev119+g108d257` (commit `108d257`) |
| `steel/home.py` | Resolves the OpenJarvis home; dir 700, files 600, umask 077 |
| `steel/chat.py` | Direct `engine.generate` calls with no agent and no tools; starts a `MemoryService` on `SteelFactStore` |
| `steel/extractor.py` | `SteelFactExtractor`: sends only the user's message with a stricter prompt; drops answer-like results |
| `steel/similarity.py` | Near-duplicate and related-fact matching (word overlap, English and Spanish, thresholds in one place) |
| `steel/timewords.py` | English and Spanish short-term time expressions |
| `steel/facts.py` | `SteelFactStore(LocalFactStore)`: stable id, `forget(id)`, hash-only forgotten list, duplicate guard, routing of time-bound facts to notes |
| `steel/memory_files.py` | Exact-line ids and removal for `MEMORY.md` / `USER.md` |
| `steel/memory.py` | `SteelMemory`: list (with expiry), remember, forget (with related), wipe and audit across all stores |
| `steel/notes.py`, `steel/notes_cli.py` | The original expiring SQLite notes (one read-only accessor added); hold time-bound facts and `remember --days`; future room-observation store |

### Relationship to OpenJarvis

STEEL imports OpenJarvis from the pinned install and never edits or copies its source. It uses the shared `core` objects (config, `EventBus`, `Message`/`Role`, registries), so anything STEEL registers later lands in the same registries OpenJarvis uses. It reuses `MemoryService`, `FactExtractor`, `InjectionScanner`, `inject_context`, `setup_security` and `get_engine` as they are.

STEEL constructs `MemoryService` itself instead of calling `build_memory_service`, because that helper would create a plain `LocalFactStore` (no forget-by-id, duplicate guard or routing) and OpenJarvis' `FactExtractor`. `SteelFactExtractor` subclasses it and uses only its public `extract` and `system_prompt`. `SteelFactStore.forget` holds the same thread lock and cross-process file lock as upstream `add`/`clear`, and uses LocalFactStore private members. That is why the pin is enforced at runtime.

The routing hook sits in `SteelFactStore.add` because every OpenJarvis path (`add_with_trust`, `add_many*`) ends there, so no OpenJarvis code is edited. Quarantined (untrusted) facts bypass the duplicate guard and are never routed to notes, which carry no trust marker.

### Fact ids

A fact's id is `f-` plus the first 12 hex characters of SHA-256 of its lowercased, stripped text. Upstream already dedupes facts case-insensitively, so the id is unique within the store and stable across restarts without changing the JSONL format. MEMORY.md/USER.md ids hash the exact line. Note ids are `n-` plus the note's UUID.

### What is not here

- No agent loop, no `ToolExecutor`, no registered tools (`[tools] enabled = []`). The `memory_manage` tool is deliberately not used.
- No network server, no voice or vision input.
- Conversation history is not persisted.

## Planned extension points

Phase 2 adds `SteelPolicy`, a permission policy every tool call must pass, before any tool is registered. Voice and vision may later publish events on the same OpenJarvis `EventBus`. The room-observation store will build on the expiring notes. These are plans, not implemented boundaries.
