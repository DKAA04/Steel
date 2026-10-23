# STEEL

**A personal assistant layer on top of [OpenJarvis](https://github.com/open-jarvis/OpenJarvis), starting with persistent, user-controlled memory.**

STEEL is planned as a room-scale assistant that hears, sees and remembers. Today (Phase 1b plus the Phase 2a permission policy) it is a text chat with **persistent memory you can list, add to, forget item by item, and wipe**. It has **no tools**: it cannot browse, run code, read files or act outside the conversation. A deny-by-default permission policy (`SteelPolicy`) and the only allowed way to build a tool executor exist and are tested, but **no tool is registered and nothing uses them yet**. Voice, vision, tools, the mobile bridge and security tooling are planned, not implemented.

## Requirements

- Linux, Python 3.11–3.13
- OpenJarvis installed at `~/.openjarvis/.venv`, from commit `108d257188cbad85f2e50209d9a61669ea4dec33` (reports version `1.0.5.dev119+g108d257`). STEEL refuses to run against any other version (see `steel/pin.py`).
- The OpenJarvis Rust extension (`import openjarvis_rust` must work).
- [Ollama](https://ollama.com) running locally with `qwen3.5:2b` pulled.

## Install

```bash
# STEEL goes into the OpenJarvis venv without reinstalling OpenJarvis.
uv pip install --python ~/.openjarvis/.venv/bin/python --no-deps -e .
# Dev tools (tests, lint, types) come from the [dev] extra in pyproject.toml.
uv pip install --python ~/.openjarvis/.venv/bin/python "pytest>=8,<10" "ruff>=0.11,<1" "mypy>=1.15,<2"
export PATH="$HOME/.openjarvis/.venv/bin:$PATH"
```

`--no-deps` is used because OpenJarvis is already installed (editable) at the pinned commit. On a fresh machine, `pip install -e ".[dev]"` would fetch OpenJarvis from the pinned git commit, but the Rust extension must still be built as the OpenJarvis docs describe. This path has not been tested.

## OpenJarvis configuration

STEEL expects these settings in `~/.openjarvis/config.toml` (`steel doctor` checks them):

```toml
[tools]
enabled = []            # no tools are registered (the policy exists, see below)

[analytics]
enabled = false         # no PostHog usage analytics

[security]
profile = "personal"    # local guardrails: redact secrets/PII, rate limit

[memory]
enabled = true          # automatic fact extraction
extraction_model = "qwen3.5:2b"

[traces]
enabled = false         # traces.db would store raw query and result text
```

Also put this in your shell profile so plain `jarvis` commands skip the PyPI update check too (`steel` sets it for itself):

```bash
export OPENJARVIS_NO_UPDATE_CHECK=1
```

## Use

```bash
steel doctor                      # pin, Rust extension, config, permissions
steel chat                        # chat; facts are extracted in the background
steel memory list                 # every remembered entry, with id and expiry
steel memory audit                # state of every store that can hold chat text
steel remember "User is vegetarian"            # trusted durable fact
steel remember "Dentist Friday 10:00" --days 3 # expiring note (1-7 days)
steel forget f-1a2b3c4d5e6f       # delete one entry, list related ones
steel forget f-1a2b3c4d5e6f --with-related     # ...and delete the related ones too
steel wipe --confirm              # empty every memory store (keeps the policy log)
steel wipe --confirm --include-audit   # ...and delete the policy decision log too
steel policy show                 # read-only: permission grants (none by default) and capabilities
steel notes --db PATH --allow-persistence remember "..."   # original expiring notes
```

Every `steel` command sets `~/.openjarvis` to mode 700 and the files directly inside it to 600. Text you pass on the command line can end up in your shell history.

### How memory works

- **Only your messages are read.** After each reply the extractor sees what *you* typed, never STEEL's answer, so STEEL cannot turn its own words into "facts about you".
- **Near-duplicates are skipped.** A new fact is dropped if 80% of its words are already in an existing fact (English and Spanish; case, accents and punctuation ignored; negations and numbers must match). No model call is involved. Thresholds are in `steel/similarity.py`.
- **Short-lived facts expire.** Facts with a time word ("tomorrow", "next weekend", "mañana", "este fin de semana"...) are stored as 7-day notes, not durable facts. Month- and year-scale words ("next month") are kept durable.
- **Forget blocks the same text, not the same idea.** `steel forget` removes the entry and remembers a hash of its text so the same wording is not stored again. `--with-related` also removes entries that share words with it. A reworded or translated version of the same idea will be stored again. See [SECURITY.md](SECURITY.md).
- **`steel remember`** stores your own statement as a trusted fact (or as an expiring note with `--days`), even if you forgot the same text earlier.

### Where memory lives

| Id prefix | File in `~/.openjarvis` | What writes it |
| --- | --- | --- |
| `f-` | `memory_facts.jsonl` | OpenJarvis MemoryService during `steel chat` (model-extracted facts), and `steel remember` (trusted) |
| `n-` | `steel_notes.sqlite3` | Time-bound facts from `steel chat`, `steel remember --days`, and `steel notes`. Expiring notes; the same store is kept for future room observations |
| `m-` | `MEMORY.md` | You (by hand) or other OpenJarvis commands; STEEL only reads it |
| `u-` | `USER.md` | You (by hand) or other OpenJarvis commands; STEEL only reads it |

`steel_forgotten.json` holds hashes of forgotten texts (not the texts).

`steel wipe` also deletes the OpenJarvis databases that can hold conversation text when other `jarvis` commands run: `traces.db`, `sessions.db`, `agents.db`, `memory.db` and `audit.db`. See [SECURITY.md](SECURITY.md) for what each holds and what is not erased.

### How well extraction works

Facts are extracted by the small local model `qwen3.5:2b` and it is not perfect. On the 12 invented test exchanges Phase 1b stores no junk and no stale plans, but it **missed one real fact** (a preference about answer style), can answer in the wrong language, is slower (4–6 s per message in the background), and re-learns an idea if you say it in other words after you forgot it. Check `steel memory list` from time to time. Measured results, including what got worse, are in [docs/MEMORY_QUALITY.md](docs/MEMORY_QUALITY.md). Facts about other people you mention ("my sister Mara") are stored like any other; there is no per-person handling yet, by decision, to be revisited when a tool can read memory.

### Permission policy (no tools yet)

`steel/security/` holds `SteelPolicy`, a permission policy every future tool call must pass, and `build_executor`, the only place STEEL may construct an OpenJarvis `ToolExecutor`. Everything is denied unless `~/.openjarvis/steel_policy.json` (written by hand, mode 600; absent by default) grants it. Sensors (`sensor:camera`, `sensor:microphone`) can never be granted by a wildcard. Every decision, allow or deny, is appended to `~/.openjarvis/steel_policy_audit.jsonl` (time, agent id, capability, allow/deny, reason code; never paths, arguments or text; mode 600). `steel wipe` keeps that log unless you pass `--include-audit`.

**What this is not:** it only guards calls that go through a `ToolExecutor` built by `build_executor`. It does not sandbox the process, and a tool's own code can do anything once it runs. Design, rules, limits and the test for each rule: [docs/POLICY.md](docs/POLICY.md).

## Implementation status

| Component | Current state |
| --- | --- |
| Packaging | Implemented: `pyproject.toml`, `steel` entry point, OpenJarvis pinned to one commit |
| Chat | Implemented: `steel chat`, direct model calls, no agent, no tools, history in RAM only |
| Persistent memory | Implemented: OpenJarvis MemoryService + `SteelFactStore`; user-only extraction, duplicate guard, 7-day notes for time-bound facts, `remember`, list, forget (with related), wipe |
| Expiring notes | Implemented: SQLite notes with expiry; hold time-bound facts and `remember --days`, and are kept for future room observations |
| Permission policy | Implemented and tested: `SteelPolicy`, `build_executor`, names-only decision log, `steel policy show`, `steel doctor` line. **Not connected to anything: no tool exists** |
| Tools | Planned (Phase 2b). None is registered |
| Voice / vision | Planned; no microphones, cameras or identity recognition are connected |
| API / Flutter bridge | Planned; no listening service |
| Cybersecurity tools | Planned; no scanner or offensive module is shipped |

[Architecture](ARCHITECTURE.md) · [Security](SECURITY.md) · [Roadmap](docs/ROADMAP.md) · [Provenance](docs/PROVENANCE.md)

## Development

```bash
ruff check steel tests && ruff format --check steel tests
mypy steel
pytest -q        # never calls the live model
python scripts/quality_check.py /tmp/steel-qc   # live model, scratch dir; optional
```

## Credits and provenance

STEEL is built on **OpenJarvis** (Apache-2.0, https://github.com/open-jarvis/OpenJarvis). It imports the OpenJarvis core (config, event bus, engine, guardrails, MemoryService, fact extractor, fact store) and does not copy or modify it. One file is adapted from OpenJarvis (`steel/security/policy.py`, from `security/capabilities.py`, with the default inverted to deny); it carries a provenance header and a row in [docs/PROVENANCE.md](docs/PROVENANCE.md). Everything else is imported, not copied.

The original repository was a directory scaffold and architecture outline. The first offline note store and docs were written with Codex assistance in September 2026. The Phase 1 OpenJarvis integration (packaging, memory layer, chat, tests and docs) was written with Claude Code (Claude Opus 5.5) and the Phase 1b memory-quality work (user-only extraction, duplicate guard, time-bound notes, `remember`, related forget) with Claude Code (Claude Sonnet 5.5), and the Phase 2a permission policy (`steel/security/`, tests, docs/POLICY.md) and the notes `source` column and exit-wait cap with Claude Code (Claude Sonnet 5.5), all in October 2026 and reviewed by the repository owner. Do not present this as a finished room-scale AI system or as deployed client work.

No GDPR compliance, security audit or production-readiness is claimed.
