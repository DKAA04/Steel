# STEEL

**A personal assistant layer on top of [OpenJarvis](https://github.com/open-jarvis/OpenJarvis), starting with persistent, user-controlled memory.**

STEEL is planned as a room-scale assistant that hears, sees and remembers. Today (Phase 1b plus the Phase 2a/2b permission policy and tools, Phase 2c voice input and Phase 3 camera snapshots) it is a text chat with **persistent memory you can list, add to, forget item by item, and wipe**. The chat has **no tools**: the model cannot browse, run code, read files or act outside the conversation. STEEL has two read-only **memory-search tools** (Phase 2b) one **microphone tool** (Phase 2c) and one **camera tool** (Phase 3) behind a deny-by-default permission policy (`SteelPolicy`); **only you can run them, from the command line** (`steel tool call`, `steel voice listen`, `steel chat --voice`, `steel look`). Nothing registers them with OpenJarvis, and the chat model never calls a tool. **Voice is optional, push-to-talk only and fully local**: no wake word, no always-on listening, no cloud speech service. **Vision is optional, one picture per command and fully local**: no video, no always-on camera, no motion detection, no face recognition, nothing identifies people. More tools, the mobile bridge and security tooling are planned, not implemented.

## Requirements

- Linux, Python 3.11–3.13
- OpenJarvis installed at `~/.openjarvis/.venv`, from commit `108d257188cbad85f2e50209d9a61669ea4dec33` (reports version `1.0.5.dev119+g108d257`). STEEL refuses to run against any other version (see `steel/pin.py`).
- The OpenJarvis Rust extension (`import openjarvis_rust` must work).
- [Ollama](https://ollama.com) running locally with `qwen3.5:2b` pulled. For the optional camera: `ffmpeg` and a vision-capable model in Ollama (`qwen3.5:2b` by default, the same as the chat model).

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
enabled = []            # nothing is registered with OpenJarvis (STEEL's tools run only through its own executor)

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
steel doctor                      # pin, Rust extension, config, permissions; warns if memory is tight
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
steel tool list                   # read-only: the tools, what each needs, allowed or denied by your grants
steel tool call memory_search_facts --agent cli --args '{"query": "tea"}'   # ONE call, through the policy, no model
steel notes --db PATH --allow-persistence remember "..."   # original expiring notes
steel voice check                 # read-only: audio devices, speech model, consent, grant. Opens NO microphone
steel voice consent               # read the consent text; type yes to agree (a date in a file you can delete)
steel voice revoke                # withdraw consent (deletes that file)
steel voice listen                # ONE push-to-talk capture through the policy; prints the transcript
steel chat --voice [--speak]      # empty line = speak; you confirm each transcript (Enter) or discard it (Esc)
steel vision check                # read-only: video devices, ffmpeg, vision model, consent, grant. Opens NO camera
steel vision consent              # read the consent text; type yes to agree (a date in a file you can delete)
steel vision revoke               # withdraw camera consent (deletes that file)
steel look ["is the desk tidy?"]  # ONE picture through the policy, described by the local vision model; shown to you only
steel look --save [--hours N]     # ...and, if you press Enter, keep the text as a "room" note (24 h by default, 7 days at most)
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

### Voice input (Phase 2c, optional, local)

Push-to-talk only: the microphone opens when you press Enter, shows a `● REC` line with the seconds elapsed for as long as it is open, and closes when you press Enter again, after at most 30 seconds, or after a pause. The sound is kept in memory only and thrown away after it is turned into text on this computer with `faster-whisper` (model `small`, CPU, `int8`); nothing is sent over the network and no audio file is written. You see the transcript first: Enter sends it through the same path as typed text (guardrails, user-only fact extraction); Esc discards it, and a discarded transcript is never sent or remembered. Everyone speaking near the microphone while it is open is recorded: only use it when that is acceptable.

Setup (nothing happens until you do all of it):

1. `uv pip install --python ~/.openjarvis/.venv/bin/python -e "$HOME/.openjarvis/src[speech]"` and a model directory in `~/.openjarvis/steel_models/` (how it was fetched, sizes and checksums: [docs/VOICE.md](docs/VOICE.md) section 3). `arecord` (alsa-utils) must exist.
2. `steel voice consent` (type `yes`).
3. Write the grant **by hand** (`~/.openjarvis/steel_policy.json`, mode 600; STEEL never writes it). `steel voice check` prints the exact text:
   `{"agents": [{"agent_id": "voice", "grants": [{"capability": "sensor:microphone", "pattern": "microphone_listen"}]}]}`
4. `steel voice check`, then `steel voice listen`. `python scripts/voice_check.py` is a live test (5 s from your real microphone, in a scratch directory that is deleted afterwards).

Spoken replies (`--speak`) need a local `espeak-ng`, which is **not installed** here and has only been tested with a fake. Settings: `STEEL_VOICE_MAX_SECONDS` (default 30, capped at 120), `STEEL_VOICE_SILENCE_SECONDS`, `STEEL_VOICE_SILENCE_RMS`, `STEEL_VOICE_MODEL` (`small`, or `base` for speed), `STEEL_VOICE_LANGUAGE` (`en,es,it` by default, a single code, or `auto`), `STEEL_VOICE_DEVICE`. **What was and was not tested, accuracy in Spanish and Italian (not measured), speed, and the wake word (not implemented):** [docs/VOICE.md](docs/VOICE.md).

### Vision (Phase 3, optional, local)

One picture per command, never video. `steel look` asks you to press Enter (`Press Enter to take one picture, Ctrl-C cancels.`), then prints a `● CAMERA OPEN` line (the camera is not opened if it cannot be shown), takes one frame with `ffmpeg`, closes the camera, makes the picture smaller (at most 448 pixels on the longest side) in memory, and asks a vision model that runs in your local Ollama to describe it. The picture is never written to a file and is wiped afterwards (best effort); the tool returns text only, under a fixed line saying the text is data, not instructions. The description is shown to you and **kept nowhere** unless you add `--save` and press Enter: then it becomes an expiring note with source `room` (24 hours by default, 7 days at most), which `steel wipe` deletes with the other notes. The camera consent file is not memory and `steel wipe` does not delete it (it tells you how to revoke). **Anyone in view is in the picture; be careful with guests.** STEEL does not identify people. A small model can be wrong, and it can be steered by text written in the picture (measured once, [docs/VISION.md](docs/VISION.md) section 4), which is why the description is treated as untrusted data.

Setup (nothing happens until you do all of it):

1. `ffmpeg` and a vision-capable model in Ollama: `qwen3.5:2b` (the chat model) by default, or `ollama pull qwen2.5vl:3b` and `STEEL_VISION_MODEL=qwen2.5vl:3b` (already present on the author's machine; nothing new is installed by STEEL).
2. `steel vision consent` (type `yes`).
3. Write the grant **by hand** (`~/.openjarvis/steel_policy.json`, mode 600; STEEL never writes it). `steel vision check` prints the exact text:
   `{"agents": [{"agent_id": "eyes", "grants": [{"capability": "sensor:camera", "pattern": "camera_look"}]}]}`
4. `steel vision check`, then `steel look`. `python scripts/vision_check.py` is a live test (one real picture, in a scratch directory that is deleted afterwards; it can compare models on the same picture).

**Speed:** about 25 seconds a look with the default `qwen3.5:2b` and about 100 seconds with `qwen2.5vl:3b` on the owner's CPU (the owner's live check, a few looks). Settings: `STEEL_CAMERA_DEVICE` (`/dev/video0` by default), `STEEL_VISION_MODEL`. **What was and was not tested, and always-on watching, motion detection and recognising people (not implemented):** [docs/VISION.md](docs/VISION.md).

### Permission policy and the first tools

`steel/security/` holds `SteelPolicy`, a permission policy every tool call must pass, and `build_executor`, the only place STEEL may construct an OpenJarvis `ToolExecutor`. Everything is denied unless `~/.openjarvis/steel_policy.json` (written by hand, mode 600; absent by default) grants it. The only wildcard a grant may use is `memory:read:*`; a bare `*` makes the grants file invalid. Sensors (`sensor:camera`, `sensor:microphone`) can never be granted by a wildcard. Every decision, allow or deny, is appended to `~/.openjarvis/steel_policy_audit.jsonl` (time, agent id, capability, allow/deny, reason code; never paths, arguments or text; mode 600). `steel wipe` keeps that log unless you pass `--include-audit`.

**The tools** (`steel/tools/`): `memory_search_facts` (needs `memory:read:facts`) and `memory_search_notes` (needs `memory:read:notes`). Each takes a `query` of 1-200 characters, matches words ignoring case, accents and a final plural "s" ("cats" finds "cat"; "boxes" does not find "box"), and returns at most 10 entries and 2000 characters as `id: text` lines under a fixed line saying the text is data, not instructions. They read, never write (not even expiring a note), never call the model and never use the network. The facts tool does not return quarantined or forgotten facts, and never reads notes, `MEMORY.md`, `USER.md` or the forgotten-hash file. **Facts about other people ("User has a sister named Mara") are returned like any other fact** when you grant `memory:read:facts` (the current decision, see [docs/MEMORY_QUALITY.md](docs/MEMORY_QUALITY.md)). Grants are written by you; STEEL never writes `steel_policy.json`. An example file is in [docs/POLICY.md](docs/POLICY.md) section 7.

**What this is not:** it only guards calls that go through a `ToolExecutor` built by `build_executor`. It does not sandbox the process, and a tool's own code can do anything once it runs. Design, rules, limits and the test for each rule: [docs/POLICY.md](docs/POLICY.md).

## Implementation status

| Component | Current state |
| --- | --- |
| Packaging | Implemented: `pyproject.toml`, `steel` entry point, OpenJarvis pinned to one commit |
| Chat | Implemented: `steel chat`, direct model calls, no agent, no tools, history in RAM only |
| Persistent memory | Implemented: OpenJarvis MemoryService + `SteelFactStore`; user-only extraction, duplicate guard, 7-day notes for time-bound facts, `remember`, list, forget (with related), wipe |
| Expiring notes | Implemented: SQLite notes with expiry; hold time-bound facts, `remember --days` and, only after you confirm, `steel look --save` room observations |
| Permission policy | Implemented and tested: `SteelPolicy`, `build_executor`, names-only decision log (rotated at 5 MB), `steel policy show`, `steel doctor` line |
| Tools | Two read-only memory-search tools (run by `steel tool call`), the microphone tool (run by `steel voice listen` / `steel chat --voice`) and the camera tool (run by `steel look`); no tool-calling model is involved and nothing is registered with OpenJarvis. **The chat model cannot call any of them.** No other tool exists |
| Voice input | Implemented and tested with fake audio: push-to-talk, consent file, visible REC line, local Whisper, RAM-only audio. **Never exercised against the real microphone by the test suite**; `scripts/voice_check.py` is the owner's live check. Wake word: **not implemented**. Spoken replies: only an untested-with-the-real-engine `espeak-ng` call |
| Vision | Implemented and tested with a fake camera and a fake vision model: one picture per command, consent file, visible CAMERA OPEN line, local Ollama only, RAM-only picture, text-only result marked as data. **Never exercised against the real camera or a real room by the test suite**; `scripts/vision_check.py` is the owner's live check. Always-on watching, motion detection and recognising people: **not implemented** |
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

STEEL is built on **OpenJarvis** (Apache-2.0, https://github.com/open-jarvis/OpenJarvis). It imports the OpenJarvis core (config, event bus, engine, guardrails, MemoryService, fact extractor, fact store) and does not copy or modify it. One file is adapted from OpenJarvis (`steel/security/policy.py`, from `security/capabilities.py`, with the default inverted to deny); it carries a provenance header and a row in [docs/PROVENANCE.md](docs/PROVENANCE.md). Everything else is imported, not copied. The voice code deliberately does **not** import `openjarvis.speech` (its discovery can pick a cloud service, and its Whisper backend writes the audio to a temp file); it uses `faster-whisper` (MIT) directly with Systran's MIT-licensed model conversions, see [docs/VOICE.md](docs/VOICE.md) section 2 and [docs/PROVENANCE.md](docs/PROVENANCE.md).

The original repository was a directory scaffold and architecture outline. The first offline note store and docs were written with Codex assistance in September 2026. The Phase 1 OpenJarvis integration (packaging, memory layer, chat, tests and docs) was written with Claude Code (Claude Opus 5.5) and the Phase 1b memory-quality work (user-only extraction, duplicate guard, time-bound notes, `remember`, related forget) with Claude Code (Claude Sonnet 5.5), and the Phase 2a permission policy (`steel/security/`, tests, docs/POLICY.md), the notes `source` column and exit-wait cap, the Phase 2b hardening and first memory-search tools (`steel/tools/`, `steel tool`), the Phase 2c push-to-talk voice input (`steel/voice/`, `steel/tools/microphone.py`, `docs/VOICE.md`) and the Phase 3 camera snapshot (`steel/vision/`, `steel/tools/camera.py`, `docs/VISION.md`) with Claude Code (Claude Sonnet 5.5), all in October 2026 and reviewed by the repository owner. Do not present this as a finished room-scale AI system or as deployed client work.

No GDPR compliance, security audit or production-readiness is claimed.
