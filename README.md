# STEEL

**A personal assistant layer on top of [OpenJarvis](https://github.com/open-jarvis/OpenJarvis), starting with persistent, user-controlled memory.**

STEEL is planned as a room-scale assistant that hears, sees and remembers. Today (Phase 1b plus the Phase 2a/2b permission policy and tools, Phase 2c voice input, Phase 3 camera snapshots, Phase 4 slash commands, Phase 5 spoken replies and the Phase 5c Jarvis loop) it is a text chat with **persistent memory you can list, add to, forget item by item, and wipe**. **The chat model cannot run any tool**: it cannot browse, run code, read files or act outside the conversation, and nothing it writes is ever run. STEEL has three read-only **memory-search tools**, one **microphone tool**, one **camera tool**, a **speak tool** and an always-on **wake-word tool** behind a deny-by-default permission policy (`SteelPolicy`); **only you can run them**: from the command line (`steel tool call`, `steel voice listen`, `steel chat --voice`, `steel look`, `steel jarvis`) or by typing a **slash command in `steel chat`** (`/search`, `/notes`, `/room`, `/look`). You see each result first and decide whether the model may see it. Nothing registers the tools with OpenJarvis. **Voice is optional and fully local**: push-to-talk, plus a hands-free wake word ("hey jarvis") **only** while you run `steel jarvis` in the foreground of a terminal (Phase 5c, not yet run live by the owner); no background service, nothing at login, no cloud speech service. **Vision is optional, one picture per command and fully local**: no video, no always-on camera, no motion detection, no face recognition, nothing identifies people. More tools, the mobile bridge and security tooling are planned, not implemented.

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
steel chat                        # chat; facts are extracted in the background; slash commands below
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
steel chat --speak                # replies are also read aloud by a local engine (opt-in, needs your grant)
steel speak check                 # read-only: engine, voices, player, grant. Plays nothing, starts nothing
steel speak say "Testing."        # ONE short sentence through the policy, out loud
steel vision check                # read-only: video devices, ffmpeg, vision model, consent, grant. Opens NO camera
steel vision consent              # read the consent text; type yes to agree (a date in a file you can delete)
steel vision revoke               # withdraw camera consent (deletes that file)
steel look ["is the desk tidy?"]  # ONE picture through the policy, described by the local vision model; shown to you only
steel look --save [--hours N]     # ...and, if you press Enter, keep the text as a "room" note (24 h by default, 7 days at most)
steel jarvis check                # read-only: models, consents, grants, limits, memory. Opens NO microphone
steel jarvis consent              # read the ALWAYS-ON consent text; type yes to agree (separate from voice consent)
steel jarvis revoke               # withdraw always-on consent (deletes that file)
steel jarvis [--no-speak] [--remember-voice] [--timings]   # hands-free: say "hey jarvis", then your request
steel jarvis stop                 # from ANOTHER terminal: kill a running (or stuck) loop and its microphone
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

Spoken replies are described in the next section. Settings: `STEEL_VOICE_MAX_SECONDS` (default 30, capped at 120), `STEEL_VOICE_SILENCE_SECONDS`, `STEEL_VOICE_SILENCE_RMS`, `STEEL_VOICE_MODEL` (`small`, or `base` for speed), `STEEL_VOICE_LANGUAGE` (`en,es,it` by default, a single code, or `auto`), `STEEL_VOICE_DEVICE`, `STEEL_VOICE_HINT` (words Whisper should expect, such as your name: `STEEL_VOICE_HINT="STEEL, Ada"`; default `STEEL`, which is always kept). **What was and was not tested, accuracy in Spanish and Italian (not measured), and speed:** [docs/VOICE.md](docs/VOICE.md). The wake word exists only as the separate `steel jarvis` loop (below).

### Spoken replies (Phase 5a, optional, local)

`steel chat --speak` (or `steel chat --voice --speak`) reads each reply aloud after printing it. It is off unless you pass `--speak`, and it starts with: "Replies are read aloud through your speakers; anyone nearby can hear them." While a reply plays, a `▶ SPEAKING` line is on screen; Enter, or typing your next message and pressing Enter, stops it at once (and what you typed is sent next); Ctrl-C stops it too. The engine is **Piper** (`piper-tts` 1.8.0, GPL-3.0-or-later), run as a separate program from its own environment (`~/.local/share/steel/piper`), inside `unshare --net` so it has no network at all, with the text on its standard input; STEEL never imports it. The audio goes through pipes to `aplay`; no file is written. Only the assistant's own reply is spoken, never a tool result, a room note or camera text, and never text the injection scanner flags; it is redacted like tool results, markdown, links and code are removed, and at most 600 characters are read (`STEEL_SPEAK_MAX_CHARS`, 50 to 4000), then "The rest is on screen." The language (English, Spanish or Italian, English when unsure) picks the voice: `en_US-john-medium` (US male, public-domain recordings), `es_ES-carlfm-x_low` and `it_IT-riccardo-x_low` (male, lower quality, chosen for their licences). Loading a voice takes about 4 s, so the chat starts the engine for the voice of your message's language **before** it asks the model (Phase 5b) and, since Phase 5b-2, **keeps it loaded for the whole session** (about 142 MiB of RAM per voice); since Phase 5b-3 the engines of the **last two voices used** stay loaded (`STEEL_SPEAK_KEEP_VOICES`, 1 to 3), so English, Spanish, English pays one voice load per language, and one unused for 30 minutes is stopped (`STEEL_SPEAK_VOICE_IDLE_MINUTES`, 5 to 240): each reply is one line to the running engine, and a reply is finished when no new audio has arrived for 1.5 s (`STEEL_SPEAK_IDLE_END_SECONDS`, 0.5 to 5). Stopping a reply replaces the engine with a clean one, so none of the old audio can play in the next reply. Measured into memory with the real engine: 0.05 to 0.44 s to first audio once loaded, against 4.38 s cold; **not yet heard with a real speaker** ([docs/SPEAK.md](docs/SPEAK.md) section 2.3 and 6; `STEEL_SPEAK_PERSISTENT=0` goes back to one process per reply). Only with the grant; your message only chooses the voice and never reaches the engine; the engine is stopped when the chat ends, and killed by the kernel if STEEL dies.

Setup, once (the only step that uses the network): `python scripts/speak_setup.py` installs the engine from a hash-locked requirements file and downloads the pinned voices, checking every SHA-256, into `~/.openjarvis/steel_models/piper-voices/`. Then write the grant by hand, `{"capability": "audio:play", "pattern": "speak"}` for the agent `voice` ([docs/POLICY.md](docs/POLICY.md) section 7.4), and run `steel speak check`. `python scripts/speak_check.py` is the live check: one sentence per language through your real speakers. The owner reported on 2026-10-25 that spoken replies work on this machine; the assistant that wrote the code has never played a sound. Licences, measurements, the GPL boundary and what is not tested: [docs/SPEAK.md](docs/SPEAK.md).

### Speed (Phase 5b)

* `steel chat --timings` prints one line of numbers after each turn: model time, Ollama's load and prompt time (their sum is the time before the first token), generation time, tokens per second and, with `--speak`, the time to the first audio and the seconds played. Never any text; nothing is logged or stored.
* `steel chat` loads the model in the background as soon as it starts, with one request that carries the **system prompt and your memory exactly as the first message will** plus the fixed word `ready` (no message of yours; one token to generate), so the first message neither waits for a cold load nor re-reads the prompt. That memory goes only to the Ollama on this computer, through the same guardrails as every turn. The prompt is never held up.
* **One context window:** `STEEL_OLLAMA_NUM_CTX` (2048 to 32768, default 4096) is set on every request STEEL sends to a local Ollama (chat, warm-up, fact extraction, vision). OpenJarvis asks for 16384 unless told otherwise, and Ollama reloads a model whenever the context changes between requests. STEEL drops the oldest history so the prompt fits.
* `STEEL_OLLAMA_KEEP_ALIVE=30m` (from `1s` to `24h`) asks Ollama to keep the model loaded that long after each of STEEL's requests; unset, Ollama's default (5 minutes) applies. The machine-wide setting is `OLLAMA_KEEP_ALIVE` in the Ollama service, which you set yourself (docs/LATENCY.md section 6). Warm-up and keep-alive apply only to an Ollama on this computer.
* The reply is **not streamed**: OpenJarvis' guardrails redact the model's output only on the non-streaming path, so streaming would show unredacted text (docs/LATENCY.md section 4).
* **`--timings` also shows how the warm-up went** (`warm-up: done in 4.2 s`, `failed (timeout)` or `skipped (not-local)`, once, between turns), `waiting for the warm-up to finish` and `queued behind warm-up 2.0 s` when you typed too early, and `prompt tokens 312 evaluated of ~340`. `engine: warm` means the voice engine was already running with its voice loaded; `loading` means it was started for that reply. The base prompt is 23 percent shorter (docs/LATENCY.md sections 10 to 12). Whether turn 1 reuses the warm-up's cached prompt is **not established**: the model may not allow it (section 10.2).
* `python scripts/latency_check.py /tmp/steel-latency` is your measurement: a cold and two warm prompts, Ollama's numbers and whether it uses the CPU or the GPU. What to look for, and why Ollama may not use the integrated GPU (`OLLAMA_IGPU_ENABLE`): [docs/LATENCY.md](docs/LATENCY.md).

### Hands-free: `steel jarvis` (Phase 5c, optional, local, ALWAYS-ON while it runs)

**Status: implemented and tested with fakes; not yet run live by the owner.** While `steel jarvis` runs, the microphone is **always open**: everything said near the computer, by anyone, is analysed on this computer for the wake word "hey jarvis". Nothing is recorded, transcribed or stored before the wake word; after it, the next few seconds are recorded, transcribed, shown to you with a 3 s countdown (Esc cancels, Enter sends) and answered by the same chat as typed lines, then spoken if you granted it. It runs only in the foreground of a terminal you started; there is no background service and nothing starts at login. **Tell the people around you, and do not run it where that is not appropriate.** Design, risks and the test for each claim: [docs/JARVIS.md](docs/JARVIS.md).

1. Push-to-talk voice set up first (above): the request after the wake word is recorded by the same tool and needs the voice consent.
2. `python scripts/wake_setup.py` fetches the three wake-word model files (3.7 MB, pinned SHA-256) into `~/.openjarvis/steel_models/wake/`. **They are CC BY-NC-SA 4.0: personal, non-commercial use only.**
3. `steel jarvis consent`: its own consent, which says ALWAYS ON (the voice consent does not count for it).
4. Write the grants **by hand** in `~/.openjarvis/steel_policy.json` (mode 600; STEEL never writes it). `steel jarvis check` prints the exact text:

```json
{"agents": [{"agent_id": "jarvis", "grants": [
  {"capability": "sensor:microphone_always_on", "pattern": "wake_listen"},
  {"capability": "sensor:microphone", "pattern": "microphone_listen"},
  {"capability": "audio:play", "pattern": "speak"}
]}]}
```

5. `steel jarvis check`, then `steel jarvis`. Before the microphone opens, a **red window** appears in the top-right corner of the screen ("STEEL MICROPHONE ON") and a status line in the terminal; if either cannot be shown, nothing is opened.

**To stop:** `q` (quit), `m` (mute: the microphone closes; `m` again to listen), Ctrl-C, or `steel jarvis stop` from another terminal (works while the loop is stuck). A watchdog kills the microphone if the loop stops sending heartbeats for 10 s, and if the red window's helper process goes away (or loses its window) while the microphone is on, the microphone closes at once and the loop ends with a message. Limits: the session ends after `STEEL_JARVIS_MAX_MINUTES` (60, at most 240), after `STEEL_JARVIS_IDLE_MINUTES` (20) without a wake word, and after more than `STEEL_JARVIS_MAX_WAKES_PER_HOUR` (30) wake words in an hour. Other settings: `STEEL_JARVIS_THRESHOLD` (0.5, from 0.3 to 0.95), `STEEL_JARVIS_CONFIRM_SECONDS` (3, 0 to 10; 0 sends at once), `STEEL_JARVIS_STT_MODEL` (`base` or `small`). Nothing you say is spoken back as a command: no slash command, camera or tool can be started by voice, and what is said after the wake word is not turned into memory unless you pass `--remember-voice`.

`python scripts/jarvis_check.py --minutes 10` is your live check: it listens with the real microphone, asks after each wake word whether you said it, and reports false triggers per hour, CPU and memory. It keeps no audio and writes no results; while it runs, a private pidfile (`~/.openjarvis/steel_jarvis.pid`, mode 600, process ids and start times only) lets `steel jarvis stop` end it from another terminal, and it is removed on every exit. On synthetic audio the engine used about 38 ms of CPU per second of audio and about 100 MiB of memory (docs/JARVIS.md section 1.2).

### Vision (Phase 3, optional, local)

One picture per command, never video. `steel look` asks you to press Enter (`Press Enter to take one picture, Ctrl-C cancels.`), then prints a `● CAMERA OPEN` line (the camera is not opened if it cannot be shown), takes one frame with `ffmpeg`, closes the camera, makes the picture smaller (at most 448 pixels on the longest side) in memory, and asks a vision model that runs in your local Ollama to describe it. The picture is never written to a file and is wiped afterwards (best effort); the tool returns text only, under a fixed line saying the text is data, not instructions. The description is shown to you and **kept nowhere** unless you add `--save` and press Enter: then it becomes an expiring note with source `room` (24 hours by default, 7 days at most), which `steel wipe` deletes with the other notes. The camera consent file is not memory and `steel wipe` does not delete it (it tells you how to revoke). **Anyone in view is in the picture; be careful with guests.** STEEL does not identify people. A small model can be wrong, and it can be steered by text written in the picture (measured once, [docs/VISION.md](docs/VISION.md) section 4), which is why the description is treated as untrusted data.

Setup (nothing happens until you do all of it):

1. `ffmpeg` and a vision-capable model in Ollama: `qwen3.5:2b` (the chat model) by default, or `ollama pull qwen2.5vl:3b` and `STEEL_VISION_MODEL=qwen2.5vl:3b` (already present on the author's machine; nothing new is installed by STEEL).
2. `steel vision consent` (type `yes`).
3. Write the grant **by hand** (`~/.openjarvis/steel_policy.json`, mode 600; STEEL never writes it). `steel vision check` prints the exact text:
   `{"agents": [{"agent_id": "eyes", "grants": [{"capability": "sensor:camera", "pattern": "camera_look"}]}]}`
4. `steel vision check`, then `steel look`. `python scripts/vision_check.py` is a live test (one real picture, in a scratch directory that is deleted afterwards; it can compare models on the same picture).

**Speed:** about 25 seconds a look with the default `qwen3.5:2b` and about 100 seconds with `qwen2.5vl:3b` on the owner's CPU (the owner's live check, a few looks). Settings: `STEEL_CAMERA_DEVICE` (`/dev/video0` by default), `STEEL_VISION_MODEL`. **What was and was not tested, and always-on watching, motion detection and recognising people (not implemented):** [docs/VISION.md](docs/VISION.md).

### Slash commands in `steel chat` (Phase 4)

```text
/search QUERY     search the stored facts          (memory_search_facts, needs memory:read:facts)
/notes QUERY      search your expiring notes       (memory_search_notes, needs memory:read:notes)
/room QUERY       search the saved room notes      (memory_search_room,  needs memory:read:room)
/look [question]  take ONE picture and describe it (camera_look, needs sensor:camera and camera consent)
/help             the list;  /quit  leave
```

To leave `steel chat` (and `steel chat --voice`): type `/quit`, type `exit` or `quit` alone on the line (any case), or press Ctrl-D. `exit now` or `quit?` is an ordinary message, and a spoken "quit" is only text (`tests/test_chat_leave.py`).

Only a line **you type** that starts with `/` is a command. The model's replies, tool results and voice transcripts are never read as commands, and the model cannot start a tool. Each command is one call through the permission policy as the agent `chat`, so you write its grants by hand (an example is in [docs/INJECTION.md](docs/INJECTION.md) rule 6; the grants for `steel look` and `steel chat --voice` belong to other agents and do not count here). At the start, `steel chat` prints one line saying which commands your grants allow. The result is cleaned (no escape codes), redacted (keys, emails, phone numbers), capped at 2,000 characters and shown to you; then `Add this to the conversation? Enter = yes, Esc = no`. Only on Enter does the model see it, as a block marked as data. A result that looks like instructions is shown but never added. **A tool result never reaches memory**: facts are still extracted only from what you type or confirm by voice. Limits: 20 tool calls per chat (`STEEL_CHAT_TOOL_LIMIT`, 1 to 100) and 5 s between two looks (`STEEL_CHAT_SENSOR_GAP`, 1 to 600). In chat, typing `/look` and pressing Enter is the key press that takes the picture; a look in chat is never saved as a room note. Room notes you saved with `steel look --save` are not part of the chat prompt: the model sees one only after you run `/room` and press Enter. **This is mitigation, not a guarantee**: the model can still be misled by data you chose to add. Threat model, rules and the test for each: [docs/INJECTION.md](docs/INJECTION.md).

### Permission policy and the first tools

`steel/security/` holds `SteelPolicy`, a permission policy every tool call must pass, and `build_executor`, the only place STEEL may construct an OpenJarvis `ToolExecutor`. Everything is denied unless `~/.openjarvis/steel_policy.json` (written by hand, mode 600; absent by default) grants it. The only wildcard a grant may use is `memory:read:*`; a bare `*` makes the grants file invalid. Sensors (`sensor:camera`, `sensor:microphone`) and room notes (`memory:read:room`, camera text) can never be granted by a wildcard. Every decision, allow or deny, is appended to `~/.openjarvis/steel_policy_audit.jsonl` (time, agent id, capability, allow/deny, reason code; never paths, arguments or text; mode 600). `steel wipe` keeps that log unless you pass `--include-audit`.

**The tools** (`steel/tools/`): `memory_search_facts` (needs `memory:read:facts`), `memory_search_notes` (needs `memory:read:notes`; your own notes, not room notes) and `memory_search_room` (needs `memory:read:room`; the camera descriptions you saved with `steel look --save`, which are untrusted text). Each takes a `query` of 1-200 characters, matches words ignoring case, accents and a final plural "s" ("cats" finds "cat"; "boxes" does not find "box"), and returns at most 10 entries and 2000 characters as `id: text` lines under a fixed line saying the text is data, not instructions. They read, never write (not even expiring a note), never call the model and never use the network. The facts tool does not return quarantined or forgotten facts, and never reads notes, `MEMORY.md`, `USER.md` or the forgotten-hash file. **Facts about other people ("User has a sister named Mara") are returned like any other fact** when you grant `memory:read:facts` (the current decision, see [docs/MEMORY_QUALITY.md](docs/MEMORY_QUALITY.md)). Grants are written by you; STEEL never writes `steel_policy.json`. An example file is in [docs/POLICY.md](docs/POLICY.md) section 7.

**What this is not:** it only guards calls that go through a `ToolExecutor` built by `build_executor`. It does not sandbox the process, and a tool's own code can do anything once it runs. Design, rules, limits and the test for each rule: [docs/POLICY.md](docs/POLICY.md).

## Implementation status

| Component | Current state |
| --- | --- |
| Packaging | Implemented: `pyproject.toml`, `steel` entry point, OpenJarvis pinned to one commit |
| Chat | Implemented: `steel chat`, direct model calls (not streamed), no agent, history in RAM only. The model cannot run tools; you can, with typed slash commands (Phase 4), and you decide whether it sees each result. Since Phase 5b: model warm-up at start, optional keep-alive and `--timings`; since Phase 5b-2 one 4096-token context for every request and a warm prompt prefix (tested with fakes; the owner measured the Phase 5b warm-up and keep-alive live, the Phase 5b-2 speed-up is not measured yet) |
| Persistent memory | Implemented: OpenJarvis MemoryService + `SteelFactStore`; user-only extraction, duplicate guard, 7-day notes for time-bound facts, `remember`, list, forget (with related), wipe |
| Expiring notes | Implemented: SQLite notes with expiry; hold time-bound facts, `remember --days` and, only after you confirm, `steel look --save` room observations |
| Permission policy | Implemented and tested: `SteelPolicy`, `build_executor`, names-only decision log (rotated at 5 MB), `steel policy show`, `steel doctor` line |
| Tools | Three read-only memory-search tools (run by `steel tool call` and the chat's `/search`, `/notes`, `/room`), the microphone tool (run by `steel voice listen` / `steel chat --voice`), the camera tool (run by `steel look` and `/look`) the speak tool (run by `--speak`, `steel speak say` and `steel jarvis`, with the reply text only) and the wake-word tool `wake_listen` (run by `steel jarvis`; a status and a score, never audio or text); no tool-calling model is involved and nothing is registered with OpenJarvis. **The chat model cannot call any of them**; model-initiated tool calls are not implemented (docs/INJECTION.md section 4). No other tool exists |
| Voice input | Implemented and tested with fake audio: push-to-talk, consent file, visible REC line, local Whisper, RAM-only audio. **The test suite never touches the real microphone**; the owner ran the live check (`scripts/voice_check.py`) on this machine. Wake word: only in `steel jarvis` (next row) |
| Spoken replies | Implemented and tested with a fake engine and a fake player (Phase 5a): opt-in `--speak`, `audio:play` grant, local Piper in a separate process with no network, SPEAKING line, RAM-only audio; live-checked by the owner (reported 2026-10-25). Since Phase 5b the engine is started before the reply exists, and since Phase 5b-2 it stays loaded for the session and a reply ends by silence (measured into memory with the real engine, not yet heard with a real speaker) |
| Hands-free (Jarvis loop) | Implemented and tested with fakes and stand-in processes (Phase 5c): foreground `steel jarvis` only, its own always-on consent, `sensor:microphone_always_on` granted by hand, a red window and a terminal line before the microphone opens, `steel jarvis stop`, a watchdog, limits, nothing kept before the wake word, no tools by voice. **Not yet run live by the owner** (`scripts/jarvis_check.py`); the wake-word model files are non-commercial (CC BY-NC-SA 4.0) |
| Vision | Implemented and tested with a fake camera and a fake vision model: one picture per command, consent file, visible CAMERA OPEN line, local Ollama only, RAM-only picture, text-only result marked as data. **The test suite never touches the real camera**; the owner ran the live check (`scripts/vision_check.py`) on this machine. A black first picture is retried once; `steel look` waits for Enter. Always-on watching, motion detection and recognising people: **not implemented** |
| API / Flutter bridge | Planned; no listening service |
| Cybersecurity tools | Planned; no scanner or offensive module is shipped |

[Architecture](ARCHITECTURE.md) · [Security](SECURITY.md) · [Roadmap](docs/ROADMAP.md) · [Provenance](docs/PROVENANCE.md)

## Development

```bash
ruff check steel tests && ruff format --check steel tests
mypy steel
timeout 600 pytest -q   # never calls the live model; every test phase has a 60 s limit (STEEL_TEST_TIMEOUT)
python scripts/quality_check.py /tmp/steel-qc   # live model, scratch dir; optional
python scripts/speak_check.py                   # live speakers; optional, the owner's
python scripts/latency_check.py /tmp/steel-lat  # live model timings, scratch dir; the owner's
python scripts/jarvis_check.py --minutes 10     # live always-on microphone; the owner's
```

## Licence

STEEL's own code and documents are licensed under the Apache License 2.0 ([LICENSE](LICENSE), "Copyright 2026 DKAA04"). [NOTICE](NOTICE) lists what STEEL builds on and uses: OpenJarvis (Apache-2.0; the ported file is in [docs/PROVENANCE.md](docs/PROVENANCE.md)), faster-whisper and the Whisper models (MIT), onnxruntime (MIT), Piper (GPL-3.0-or-later, run as a separate program and not distributed with STEEL) and the voices with their licences, including the M-AILABS notice for the Italian voice, and openWakeWord (Apache-2.0; `steel/jarvis/wakeword.py` is a port, see [docs/PROVENANCE.md](docs/PROVENANCE.md)).

**The wake-word model files used by `steel jarvis` are non-commercial.** openWakeWord's pre-trained models are licensed CC BY-NC-SA 4.0: you may use them for personal, non-commercial purposes; selling STEEL with them or using them in paid work is not allowed (NOTICE section 4). They are downloaded by `scripts/wake_setup.py` and are not in this repository.

**Downloaded models and voices are not part of this repository** and keep their own licences: the Whisper models, the Ollama models, Piper's engine environment, the Piper voices and the wake-word models are fetched onto your machine by you or by the setup scripts. This is a record of the terms, not legal advice.

## Credits and provenance

STEEL is built on **OpenJarvis** (Apache-2.0, https://github.com/open-jarvis/OpenJarvis). It imports the OpenJarvis core (config, event bus, engine, guardrails, MemoryService, fact extractor, fact store) and does not copy or modify it. One file is adapted from OpenJarvis (`steel/security/policy.py`, from `security/capabilities.py`, with the default inverted to deny); it carries a provenance header and a row in [docs/PROVENANCE.md](docs/PROVENANCE.md). One file is adapted from **openWakeWord** (Apache-2.0, https://github.com/dscripka/openWakeWord): `steel/jarvis/wakeword.py`, its streaming feature code, also with a header and a row; the openWakeWord model files are downloaded by `scripts/wake_setup.py`, are **non-commercial** (CC BY-NC-SA 4.0) and are not in this repository. Everything else is imported, not copied. The voice code deliberately does **not** import `openjarvis.speech` (its discovery can pick a cloud service, and its Whisper backend writes the audio to a temp file); it uses `faster-whisper` (MIT) directly with Systran's MIT-licensed model conversions, see [docs/VOICE.md](docs/VOICE.md) section 2 and [docs/PROVENANCE.md](docs/PROVENANCE.md). Spoken replies use **Piper** (GPL-3.0-or-later, https://github.com/OHF-Voice/piper1-gpl, with espeak-ng), which is installed separately and run as a separate program, never imported or redistributed, and voices from `rhasspy/piper-voices` whose licences are those of their recordings ([docs/SPEAK.md](docs/SPEAK.md) sections 3 and 4).

The original repository was a directory scaffold and architecture outline. The first offline note store and docs were written with Codex assistance in September 2026. The Phase 1 OpenJarvis integration (packaging, memory layer, chat, tests and docs) was written with Claude Code (Claude Opus 5.5) and the Phase 1b memory-quality work (user-only extraction, duplicate guard, time-bound notes, `remember`, related forget) with Claude Code (Claude Sonnet 5.5), and the Phase 2a permission policy (`steel/security/`, tests, docs/POLICY.md), the notes `source` column and exit-wait cap, the Phase 2b hardening and first memory-search tools (`steel/tools/`, `steel tool`), the Phase 2c push-to-talk voice input (`steel/voice/`, `steel/tools/microphone.py`, `docs/VOICE.md`) and the Phase 3 camera snapshot (`steel/vision/`, `steel/tools/camera.py`, `docs/VISION.md`) with Claude Code (Claude Sonnet 5.5), the Phase 4 slash commands (`steel/slash.py`, `steel/tooldata.py`, `docs/INJECTION.md`) with Claude Code (Claude Opus 5.5), all in October 2026 and reviewed by the repository owner. The Phase 5a spoken replies (`steel/speak/`, `steel/tools/speak.py`, `docs/SPEAK.md`, the setup and check scripts) and the onnxruntime telemetry fix were written with Claude Code (Claude Opus 5.5) in October 2026 and live-checked by the owner. The Phase 5b latency work (`steel/timings.py`, `steel/warm.py`, the early engine start, `docs/LATENCY.md`, `scripts/latency_check.py`, `LICENSE` and `NOTICE`) was written with Claude Code (Claude Opus 5.5) in October 2026 and is **not yet reviewed or measured live by the owner** (the owner has run its `--timings` and `latency_check.py` and reported the numbers in docs/LATENCY.md section 9). The Phase 5b-2 work (`STEEL_OLLAMA_NUM_CTX`, `steel/context.py`, the warm prompt prefix, the long-lived speech engine) and the Phase 5b-3 work (the warm-up report, the shorter base prompt, several kept voices, the engine label) were written with Claude Code (Claude Sonnet 5.5) in October 2026; the owner has run Phase 5b-2 live and reported the numbers in docs/LATENCY.md section 10, and has **not yet reviewed or measured Phase 5b-3**. The Phase 5c Jarvis loop (the design in docs/JARVIS.md, `steel/jarvis/`, the openWakeWord port, `get_wake_tool`, the setup, check and measurement scripts, the per-test time limit and the tests) was written with Claude Code (Claude Opus 5.5) on 2026-10-25 and 2026-10-26 in two sessions; the first ended when an unfinished test looped forever and froze the owner's computer, and the second audited and finished the work. The owner has **not yet run it live**. Do not present this as a finished room-scale AI system or as deployed client work.

No GDPR compliance, security audit or production-readiness is claimed.
