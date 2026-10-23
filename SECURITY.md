# Security and privacy boundaries

STEEL is an early personal prototype for one trusted local user. Phase 1 has **persistent memory and no tools**. It has no microphone or camera access, no shell execution, no file access by the model and no listening server. It talks to a local Ollama model over `localhost`.

## What is stored, and where

Everything lives under the OpenJarvis home (`~/.openjarvis`, or `$OPENJARVIS_HOME`). Every `steel` command sets that directory to mode **700**, its top-level files to **600**, and its own umask to 077. Subdirectories (`src/`, `.venv/`) are not changed, and symbolic links are not followed.

| File | Holds | Written by `steel chat`? | Wiped by `steel wipe`? |
| --- | --- | --- | --- |
| `memory_facts.jsonl` | Model-extracted facts about you, and facts from `steel remember`, in plain text | Yes | Yes |
| `memory_facts.jsonl.lock` | Nothing (empty lock file) | Yes | Yes |
| `steel_forgotten.json` | Hashes of facts you forgot, never the text | On `steel forget` | Yes |
| `MEMORY.md`, `USER.md` | Lines you or other OpenJarvis commands wrote | No (read only) | Reset to header |
| `steel_notes.sqlite3` | Expiring notes, each labelled with a source (`user`: `steel remember --days`, `steel notes`; `time_bound`: extracted from your messages; `room`: reserved, nothing writes it) | Yes (time-bound facts) | Yes |
| `audit.db` | Security findings: pattern names (e.g. "email") and timestamps; boundary-guard alerts keep an **80-character preview** of the text | Yes (pattern names only; tested) | Yes |
| `traces.db` | Raw queries and model results | No; disabled in config | Yes |
| `sessions.db` | Full chat messages of cross-channel sessions | No | Yes |
| `agents.db` | Managed-agent messages, tasks and checkpoints | No | Yes |
| `memory.db` | Document chunks indexed for retrieval | No | Yes |
| `telemetry.db` | Token counts, latency, energy, model name. No message text | No | No |

The conversation itself is kept in RAM only and is never written to disk by STEEL. A test (`tests/test_steel.py::FileAuditTest`) runs a chat session against a temporary home and fails if any file changes that is not in the list above.

Wiping `audit.db` also deletes its tamper-evident security history. This is deliberate: it can contain text previews.

## Known limits

- **Not encrypted.** Facts, notes and memory files are plain text on disk. Do not store passwords, tokens, health or client data.
- **Facts are guessed by a small model.** `qwen3.5:2b` reads only your messages (never STEEL's replies), but it can still misstate things (for example "lives in" for "from"), miss facts, switch language, and store vague fragments. Check `steel memory list` regularly. See [docs/MEMORY_QUALITY.md](docs/MEMORY_QUALITY.md).
- **Forget blocks the same text, not the same idea.** `steel forget` removes the entry and records its hash so the extractor cannot store the same text again (ignoring case and surrounding spaces; punctuation or accent changes are a different hash). A reworded or translated version is new text and will be stored again; this was measured live (English rewording and a Spanish version both came back). `--with-related` also deletes entries sharing words with the target and hashes each of them, but it can miss vague or differently worded neighbours.
- **Near-duplicate skipping is word overlap.** A new fact is skipped when 80% of its content words are already in an existing fact (`steel/similarity.py`). It does not understand synonyms or other languages, and a fact that only repeats words of an older one is dropped even if you meant it. `steel remember` is checked the same way and reports the existing entry.
- **Leaving `steel chat` waits at most 30 seconds** (`STEEL_EXTRACT_WAIT_SECONDS`, `0` = do not wait) for the background fact extraction. If it is still running, STEEL says so and the facts it had not finished are lost; they are not retried.
- **Time words decide what expires.** Facts containing a short-term expression are stored as 7-day notes instead of durable facts. If the model drops the time word from its wording, the fact is stored durably. Notes are included in STEEL's prompt while they are alive and are purged when the store is next read, not by a background job.
- **Other people.** Facts you state about other people ("my sister Mara...") are stored as plain text like any other fact, are copied into prompts, and are found by `forget` only through shared words. There is no per-person handling: this was decided on purpose and is revisited when a tool can first read memory (docs/MEMORY_QUALITY.md).
- **Command-line text is visible to your shell.** `steel remember "..."` stores your text in your shell history. Use `steel chat` for anything sensitive, and avoid storing secrets at all.
- **The forgotten list is a hash, not secrecy.** Someone who can read `steel_forgotten.json` cannot read the text back, but can confirm a guess (hash the guess and compare). `steel wipe` deletes the list.
- **Oldest facts are evicted silently.** OpenJarvis' `LocalFactStore` keeps at most `max_facts` facts (default **1000**). When a new fact goes over the cap, the oldest ones are dropped without any warning or log message.
- **Guardrails redact before the model.** In the `personal` profile, e-mail addresses, keys and similar patterns are redacted in model input and output, so facts containing them are stored redacted.
- **Injection defence is partial.** OpenJarvis' injection scanner drops exchanges with high/critical findings and quarantines suspicious facts, which are never put back into prompts. Its scanning fails open if the scanner errors.
- **Deletion is not forensic erasure.** Files are rewritten or unlinked. Copies can survive in filesystem snapshots, SSD remapping, swap, backups, editor history or shell history.
- **Other OpenJarvis commands are outside STEEL's control.** `jarvis chat`, `jarvis serve` and agents use plain `LocalFactStore` and can write the databases above. `steel wipe` deletes them, but STEEL does not prevent them.
- **`~/.openjarvis/anon_id`** (an anonymous install id) exists from installation. Analytics are disabled, so it is not sent; delete it if you want.
- The OpenJarvis `memory_manage` tool is **not** registered: its `remove` deletes every line containing the given text.
- `SteelFactStore` depends on private members of OpenJarvis' `LocalFactStore` at the pinned commit. `steel` refuses to run against another OpenJarvis version.

## Before anything new is connected

Tools, voice, vision and remote access each need a reviewed consent and authorization design first. Phase 2 starts with the `SteelPolicy` permission policy before any tool is registered.

Do not publish real data in issues. Report problems with invented examples. No legal compliance, independent audit or certification is claimed.
