# Security and privacy boundaries

STEEL is an early personal prototype for one trusted local user. Phase 1 has **persistent memory and no tools**. It has no microphone or camera access, no shell execution, no file access by the model and no listening server. It talks to a local Ollama model over `localhost`.

## What is stored, and where

Everything lives under the OpenJarvis home (`~/.openjarvis`, or `$OPENJARVIS_HOME`). Every `steel` command sets that directory to mode **700**, its top-level files to **600**, and its own umask to 077. Subdirectories (`src/`, `.venv/`) are not changed, and symbolic links are not followed.

| File | Holds | Written by `steel chat`? | Wiped by `steel wipe`? |
| --- | --- | --- | --- |
| `memory_facts.jsonl` | Model-extracted facts about you, in plain text | Yes | Yes |
| `memory_facts.jsonl.lock` | Nothing (empty lock file) | Yes | Yes |
| `steel_forgotten.json` | Hashes of facts you forgot, never the text | On `steel forget` | Yes |
| `MEMORY.md`, `USER.md` | Lines you or other OpenJarvis commands wrote | No (read only) | Reset to header |
| `steel_notes.sqlite3` | Expiring notes | No (`steel notes` only) | Yes |
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
- **Facts are guessed by a small model.** `qwen3.5:2b` sometimes records things you never said, often taken from STEEL's own replies. Check `steel memory list` regularly. See [docs/MEMORY_QUALITY.md](docs/MEMORY_QUALITY.md).
- **Forget blocks exact repeats only.** `steel forget` removes the fact and records its hash so the extractor cannot store the same text again (ignoring case and surrounding spaces). A reworded version is new text and will be stored. Other facts that mention the same thing are not touched.
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
