# Latency of a chat turn (Phase 5b)

**Status: measurement plan and findings, written and committed before any optimisation (see the git history).** The fixes came after it; section 8 says which were made and which test proves each, and what still needs the owner's run. **Phase 5b-2 (section 9) came after the owner's live measurements: one context window, a warm prompt prefix, and a voice that stays loaded.** What needs the owner's live run is marked **owner's run**. The assistant that wrote this did not run the chat model, play sound or open a microphone or camera; it ran the Piper engine into memory only (section 5).

## 1. The problem

Two delays were noticed in the owner's live use:

* the chat model (`qwen3.5:2b` through Ollama, on the CPU) is slow, and slower still when it is "cold": Ollama unloads a model that has been idle for its keep-alive time (default 5 minutes), and the next message must load it again;
* every spoken reply waits about 4 s before the first sound, because each reply starts a new Piper process that loads its voice first (docs/SPEAK.md section 2).

## 2. `steel chat --timings`

Off by default. After each turn it prints one line, numbers only:

```
timings: model 9.4 s | first token 2.1 s (load 0.0 s, prompt 2.1 s) | generation 7.2 s | 11.3 tokens/s | first audio 0.4 s after the reply | played 4.7 s | engine: warm (0.3 s)
```

| Field | What it is | Where it comes from |
| --- | --- | --- |
| model | the whole model request, as STEEL waits for it | STEEL's clock around `engine.generate` |
| load | Ollama loading the model; 0 when it was already loaded | Ollama's `load_duration` |
| prompt | Ollama reading the prompt (system prompt, remembered facts, history, the message) | `prompt_eval_duration` |
| first token | load + prompt: everything before the first word of the reply | computed; see below |
| generation | Ollama writing the reply | `eval_duration` |
| tokens/s | reply tokens / generation time | `eval_count` / `eval_duration` |
| first audio | from the reply being handed to the speaker to the first audio reaching the player (`--speak` only) | the speak tool's clock |
| played | seconds of audio played (`--speak` only) | the speak tool |
| engine | `warm` if the first audio came within 1 s of the text reaching the Piper engine, `loading` if later; the seconds in brackets. Not shown when the engine was a process of its own (`STEEL_SPEAK_PERSISTENT=0` or the fallback) | the engine run's clock (Phase 5b-2) |

**The chat is not streamed** (section 4), so STEEL never sees the first token arrive. "First token" is Ollama's own load plus prompt time, which is what happens before the first token on the server. The difference between "model" and Ollama's numbers is time spent queueing (behind another request to the same Ollama, such as the fact extraction) and in transport.

The line never contains the message or the reply, and nothing of it is logged or stored (tests in section 8). A number is `n/a` when the engine did not report it.

## 3. Where the time of a turn goes

In order, for one typed message with `--speak`:

1. **Model load**, only when cold. The model file is 2.7 GB (`ollama list`): `qwen3.5:2b` is a **Q8_0** build, 1.9 B parameters plus a 331 M vision projector (`ollama show`). On a machine under memory pressure, loading may read from disk and push other memory to swap.
2. **Prompt.** The system prompt, the remembered facts and notes, up to 20 earlier messages and the new message. This grows during a conversation. On a CPU it costs time for every token not already in Ollama's prompt cache.
3. **Generation**, at the model's tokens per second. Q8 weights are about twice the size of Q4 ones, and on a CPU generation speed is mostly bound by memory bandwidth.
4. **Fact extraction.** After every message, STEEL's memory service sends the message to the *same* model in the background (`[memory] extraction_model`). It is a second model request per turn. It competes for the CPU with the next turn, and with Piper.
5. **Voice load** (Piper), about 4 s, before the first sound (section 5).
6. **Playback**, as long as the reply is.

Other things that can make the model slow or cold: `/look` uses a second model (`qwen2.5vl:3b`, 3.2 GB), which may push the chat model out of memory; OpenJarvis asks Ollama for a 16,384-token context (`JARVIS_NUM_CTX`), and a different context size between requests makes Ollama reload the model.

**State of the machine, read on 2026-10-25 at 14:2x CET** (`free -m`, `systemctl status ollama`; one reading, not a measurement): 14.8 GB RAM, 10.1 GB in use, 4.7 GB available; **swap 6.6 of 9.0 GB in use**. The Ollama service had peaked at 6.4 GB of memory and 995 MB of swap since it started 5 days before. With that much swap in use, a cold load may be much slower than the disk alone would make it.

## 4. Streaming was not used: it would bypass a guardrail

Streaming the reply would show the first words sooner. OpenJarvis offers it (`InferenceEngine.stream`, async, in `engine/_base.py` and `engine/ollama.py`), and STEEL wraps the engine in OpenJarvis' `GuardrailsEngine` (`setup_security`). The owner's config uses `[security] mode = "redact"`, with the secret and PII scanners on input and output.

Read in `security/guardrails.py` at the pinned commit:

* `generate()` scans the input, calls the model, then **redacts the output** before returning it;
* `stream()` scans the input, then **yields every token unredacted**, and scans the whole output only afterwards, **to publish an alert, without changing what was already yielded** ("Post-hoc scan of accumulated output for logging only").

So with streaming, a secret or personal detail the model wrote would reach the screen (and the history kept in RAM, and the speaker) unredacted. The owner's rule for this phase was: if streaming bypasses the guardrails, stop and keep the non-streaming path. **The chat therefore stays non-streaming**, and so does speaking sentence by sentence while the reply is still being generated (2c), which needs streaming. A test proves the chat uses `generate` and never `stream` (section 8).

Options for the owner, none implemented:

* **(a)** STEEL streams, but holds back each sentence until it ends, redacts it with the same OpenJarvis scanners and only then prints it. This adds STEEL's own output redaction beside OpenJarvis'. A pattern that spans a sentence break would be missed, and STEEL must make sure the history keeps the redacted text.
* **(b)** Ask OpenJarvis upstream for redaction in `stream()`. STEEL does not edit OpenJarvis.
* **(c)** Keep non-streaming (current). The first words appear only when the whole reply is done.

Streaming also loses Ollama's `load_duration` and `prompt_eval_duration` at the pinned commit (`stream()` keeps only token counts), and its `stream()` is async, so the REPL would need an event loop.

**Speaking sentence by sentence (2c) without streaming would gain nothing.** Piper already speaks a reply sentence by sentence. Its `synthesize` yields one audio chunk per sentence, and its `--output-raw` mode writes and flushes each chunk as it is made (read in `piper/voice.py` and `piper/__main__.py` of 1.8.0). So the first sentence is already heard while the rest of the reply is still being synthesized. Splitting the reply in STEEL would only add a second place that applies the cleaning and cap rules.

## 5. Piper: what was measured (real engine, into memory, nothing played)

Same command and environment as STEEL (`unshare --net --map-root-user -- …/python -I -m piper -m MODEL --output-raw`, text on stdin), `en_US-john-medium`, one sentence of about 4.6 s of speech, **2 runs each** (a small sample, on a machine with 6.6 GB of swap in use):

| Case | First audio | All audio |
| --- | --- | --- |
| Cold: start the process, write the text at once (what STEEL does now) | 4.24 s, 4.42 s after the start | about 0.1 s after that |
| Voice already loaded: process started 6 s earlier, waiting on stdin | **0.38 s, 0.44 s** after the text was written | 0.47 s, 0.53 s |

An idle engine with its voice loaded held 142 MiB of resident memory.

**Can the stock CLI stay running for a whole session?** Partly. Fed several lines on one open stdin, it keeps the voice loaded and synthesizes each line as it arrives: a second line gave its first audio after 0.14 s. But **in `--output-raw` mode it writes the audio of successive lines back to back, with nothing that marks where one line ends**. Raw PCM has no header, and the CLI prints nothing per line. STEEL could only guess the end of a reply from a pause in the output, which a slow sentence also produces. The other ways to know the end are:

* `--output-dir`, one WAV file per line, which writes audio to disk (refused);
* `piper.http_server`, which needs the network (refused: the engine has no network, and text must arrive on stdin only);
* a small program of STEEL's own that imports Piper and writes a marker after each line, which is code that imports a GPL program (refused by the owner's rule; it would also make that program a work based on Piper, under the GPL).

**Decision in Phase 5b, superseded in Phase 5b-2:** a long-lived engine with a *clear* end signal is not possible, so Phase 5b kept one process per reply (end of file ends the reply) and started it early. **Phase 5b-2 does not need a clear signal.** Synthesis is about 14 times faster than playback, so silence on the engine's output for a short idle gap (1.5 s, longer for a very long sentence) after audio has started means the reply is finished. The stock CLI is used as it is, no STEEL code imports Piper, and nothing is written to disk. The measurements for this are in docs/SPEAK.md section 2.3 and the design in section 9 below.

## 6. Why Ollama may not use the GPU (read-only investigation)

What was read on 2026-10-25, without sudo and without changing anything:

* **Ollama 0.40.3** (`/usr/local/bin/ollama`), run as the system service `ollama.service` by user `ollama`, which is in the groups `video` and `render`. The service sets no `OLLAMA_*` variable: `systemctl show ollama -p Environment` shows only `PATH`, and there is no drop-in file.
* The graphics are the integrated **AMD Radeon (Lucienne/Renoir, Vega)** of the Ryzen 5 5500U (`lspci`: `1002:164c`). `/dev/dri/renderD128` (group `render`) and `/dev/kfd` exist. The firmware gives it **1 GiB of dedicated memory** (`mem_info_vram_total`); it can also map about **7.2 GiB of system RAM** (`mem_info_gtt_total`).
* **Vulkan works** for the desktop user: `vulkaninfo --summary` lists "AMD Radeon Graphics (RADV RENOIR)", Mesa 26.1.6, as an integrated GPU.
* Ollama ships backends for CUDA 12 and 13, **ROCm 7.2** and **Vulkan** in `/usr/local/lib/ollama/`.
* `ollama serve --help` lists **`OLLAMA_IGPU_ENABLE  Enable integrated GPUs`**. That suggests that this Ollama version **leaves integrated GPUs out unless the variable is set**, and it is not set. The binary also contains the name `OLLAMA_VULKAN`, which the help text does not list; what it does in 0.40.3 was not verified.
* **Not verified:** what Ollama actually detected at start-up. Its log is in the system journal, which this user cannot read (`journalctl -u ollama`: "not seeing messages from other users and the system"). No model was loaded during the check (`ollama ps` was empty), so the PROCESSOR column could not be seen. `scripts/latency_check.py` prints it.

What the owner could try. STEEL changes none of this, and each step needs sudo:

1. Read what Ollama found: `sudo journalctl -u ollama -b | grep -iE "inference compute|gpu|vulkan|rocm|igpu|library"`.
2. Enable the integrated GPU: `sudo systemctl edit ollama`, add
   ```
   [Service]
   Environment="OLLAMA_IGPU_ENABLE=1"
   ```
   then `sudo systemctl restart ollama`, run `scripts/latency_check.py` again and compare the PROCESSOR column and tokens/s. If the Vulkan backend is still not chosen, `Environment="OLLAMA_VULKAN=1"` is the variable older versions used; whether 0.40.3 reads it is unknown.
3. **What to expect, honestly:** an integrated GPU shares the same RAM and memory bandwidth as the CPU. Reading the prompt is usually much faster on a GPU. Writing the reply may gain less, because it is limited by memory bandwidth on both. It frees no RAM. On this machine it is unmeasured either way.
4. ROCm does not officially support this chip (gfx90c). The usual workaround (`HSA_OVERRIDE_GFX_VERSION`) is not recommended here.

Other settings in the owner's hands, unmeasured:

* **`OLLAMA_KEEP_ALIVE`** in the same service file (for example `Environment="OLLAMA_KEEP_ALIVE=30m"`) keeps every model loaded longer, machine-wide. STEEL's own `STEEL_OLLAMA_KEEP_ALIVE` (section 8) affects only STEEL's requests.
* A Q4 build of the model would be about half the size of Q8_0, and usually faster on a CPU, at some loss of quality. Whether a Q4 tag of `qwen3.5:2b` exists in the Ollama library was not checked.
* `JARVIS_NUM_CTX` (OpenJarvis' context size, default 16384) is **no longer needed for STEEL** since Phase 5b-2: STEEL's own `STEEL_OLLAMA_NUM_CTX` (default 4096) is set on every request STEEL sends (section 9). Setting `JARVIS_NUM_CTX` changes nothing for STEEL's chat; it still matters for other OpenJarvis programs.
* Freeing RAM: with 6.6 GB of swap in use, anything that keeps the model from being swapped out helps both the cold and the warm times.

## 7. Measurement plan (owner's run)

The order matters: measure, change one thing, measure again.

1. **Baseline, before any setting is changed.** `python scripts/latency_check.py /tmp/steel-latency` (any scratch directory; it stays out of `~/.openjarvis`). It runs `ollama stop`, then sends three fixed prompts through the same path as `steel chat`, with the memory service off, and prints one JSON line per prompt and the memory state before and after. It plays nothing.
2. **The real chat.** `steel chat --speak --timings`: type three or four short messages, read the timing line after each.
3. **One change at a time** (section 6), then repeat 1 and 2.

What to look for:

| Number | Good | What a bad value points to |
| --- | --- | --- |
| `load_s`, cold | a few seconds | well over 10 s: the model is read from a slow disk or out of swap (memory pressure) |
| `load_s`, warm | 0 | not 0: Ollama unloaded or reloaded the model (keep-alive too short, another model such as `/look`'s pushed it out, or a different context size) |
| `prompt_eval_s` | under about 2 s for these short prompts | grows with the conversation; a GPU helps most here |
| `tokens_per_s` | for a 2 B Q8 model on 6 laptop cores, roughly 5 to 15 is plausible (an expectation, not measured here) | low single digits: CPU contention (fact extraction, Piper, other programs) or swapping |
| `processor` | `100% GPU` or a GPU share, if the GPU is enabled | `100% CPU`: no GPU in use (section 6) |
| `model` − (`first token` + `generation`) in `--timings` | under about 0.5 s | larger: the request waited behind another one, usually the fact extraction of the previous message |
| `first audio` in `--timings` | under 1 s once the early start works (section 8) | about 4 s: the voice was loaded after the reply, not before (for example because the reply's language differed from the guess) |

## 8. Fixes made after this document, and their evidence

Made after section 1 to 7 were committed. Measuring the chat model is still the owner's run (section 7), so **none of these fixes has been measured with the real model**. Only the early engine start has been measured, on the real engine into memory (docs/SPEAK.md section 6).

**a) The model is loaded at start (warm-up).** When `steel chat` (text or voice) opens, it sends one request in a background thread, through the same engine and guardrails as the chat, with at most one token to generate. Since Phase 5b-2 that request is **the prompt the first real turn would send, without a message of the owner's** (section 9): the system prompt, the owner's `MEMORY.md` and `USER.md` entries, the trusted remembered facts and the live notes, then the fixed word `ready` as the user turn. (Phase 5b sent `ready` alone with no memory.) It only runs when the engine is OpenJarvis' `OllamaEngine` on this computer (`localhost`, `127.0.0.1` or `::1`, the check vision uses). Any other engine or host gets no warm-up, so no memory and no new request leaves the machine. A failed warm-up is silent. Code: `steel/warm.py`, `ChatSession.warmup_messages`.

**b) Keep-alive (optional).** `STEEL_OLLAMA_KEEP_ALIVE`, a whole number of seconds, minutes or hours from `1s` to `24h` (for example `30m`). Anything else is reported at start, and Ollama's default applies. With it set, every request STEEL's chat engine sends to Ollama (`/api/chat`, `/api/generate`; the chat, the warm-up and the fact extraction, and since Phase 5b-2 the vision call) carries `keep_alive`. Ollama keeps a model loaded for the keep-alive of the latest request, so the model then stays loaded that long after the last message. OpenJarvis' engine does not send this field, so STEEL gives that engine a client of its own: same address and timeout, plus a transport that adds the one field and changes nothing else, and that ignores proxy variables. Only for a local `OllamaEngine`, as in a). Unset (the default), no `keep_alive` is added and Ollama's own setting applies (the same client does set `num_ctx` on every request, section 9). **The machine-wide setting is `OLLAMA_KEEP_ALIVE` in the Ollama service (section 6); STEEL does not change it, the owner sets it.** STEEL's value affects only STEEL's requests. Another program's request to the same model can set a shorter time again.

**c) Not done: streaming (2b) and speaking while the reply is generated (2c).** Section 4: streaming would show unredacted model output, so the chat stays non-streaming. A test pins that the chat calls `generate` and never `stream`, and that the reply is redacted.

**d) The voice is loaded while the model writes.** (Phase 5b; since Phase 5b-2 the engine then stays loaded, section 9.) The chat starts the engine **just before it asks the model**, for the voice of the message's language. The engine loads the voice while the model writes, then gets the reply on stdin. Details, rules and the measurement are in docs/SPEAK.md section 6, "Started early". In short: only with the grant (read-only check); the message chooses the voice and never reaches the engine; a wrong voice or a dead engine falls back to a fresh start; the engine is stopped at every exit, and killed by the kernel if STEEL dies (`setpriv --pdeathsig`). Measured into memory: 0.37 s to first audio when started 5 s ahead, against 4.38 s cold.

**e) `--timings`** (section 2).

### Evidence

Each cell is `file::Class.test`; `tests/test_latency_evidence.py` checks that every cited test exists and that every test in `test_latency.py`, `test_latency_check_script.py` and `test_licence.py` is cited. The speaking tests are listed in docs/SPEAK.md section 10 too. All tests use fake engines, fake clocks, an `httpx.MockTransport` in place of the Ollama server, or a shell script in place of Piper. None reaches Ollama, a model, Piper, a player or the network.

| Claim | Test |
| --- | --- |
| `--timings` is off by default and reaches both chats; after each turn one line with the model time, Ollama's load and prompt time, their sum as the first token, generation time and tokens per second, and with `--speak` the time to the first audio and the seconds played | `test_latency.py::ChatTimingsTest.test_timings_are_off_unless_asked_for`, `test_latency.py::ChatTimingsTest.test_the_command_line_flag_reaches_both_chats`, `test_latency.py::ChatTimingsTest.test_each_turn_prints_one_line_of_numbers_through_the_guardrails`, `test_latency.py::TimingLineTest.test_ollama_durations_give_first_token_and_tokens_per_second`, `test_latency.py::ChatTimingsTest.test_the_spoken_reply_reports_when_its_first_audio_started` |
| Missing or odd numbers from the engine are shown as `n/a`, never guessed | `test_latency.py::TimingLineTest.test_missing_or_odd_engine_numbers_show_as_not_available` |
| The timing line never contains the message or the reply, and is written to no file | `test_latency.py::ChatTimingsTest.test_the_timing_line_never_contains_the_message_or_the_reply`, `test_latency.py::ChatTimingsTest.test_timings_are_never_written_to_any_file` |
| The warm-up sends one fixed word, one token, no user text or memory, to `/api/chat`; the prompt is not held up while it runs; a failed one is silent | `test_latency.py::WarmUpTest.test_the_warm_up_sends_one_fixed_word_and_no_user_text`, `test_latency.py::WarmUpTest.test_the_first_prompt_is_not_held_up_by_the_warm_up`, `test_latency.py::WarmUpTest.test_a_failed_warm_up_is_silent_and_changes_nothing` |
| A remote or non-Ollama engine gets no warm-up and no keep-alive | `test_latency.py::WarmUpTest.test_a_remote_or_non_ollama_engine_gets_no_warm_up_and_no_keep_alive` |
| With `STEEL_OLLAMA_KEEP_ALIVE`, every request (chat and extraction) carries it and is otherwise unchanged; without it nothing is added; only `1s` to `24h` is accepted and a bad value is reported; that client never uses a proxy | `test_latency.py::KeepAliveTest.test_with_the_setting_every_request_carries_the_keep_alive`, `test_latency.py::KeepAliveTest.test_without_the_setting_requests_are_left_alone`, `test_latency.py::KeepAliveTest.test_only_a_duration_from_one_second_to_a_day_is_accepted`, `test_latency.py::KeepAliveTest.test_a_bad_setting_is_reported_at_start_and_the_default_kept`, `test_latency.py::KeepAliveTest.test_the_keep_alive_client_never_uses_a_proxy` |
| The chat never streams, so the guardrails still redact the reply (and the history) | `test_latency.py::NoStreamingTest.test_the_chat_never_streams_so_the_guardrails_redact_the_reply` |
| The engine is started before the model is asked, only with the grant, for the message's language (Phase 5b; its tests were adjusted in Phase 5b-2 because the engine now stays running after the text, docs/SPEAK.md section 6); the message never reaches it; a wrong voice or a dead engine falls back to a fresh one; nothing is left after any exit or if STEEL is killed; a whole spoken turn uses no network and writes no file | `test_speak_cli.py::EarlyStartChatTest.test_the_engine_is_started_before_the_model_is_asked`, `test_speak_cli.py::EarlyStartChatTest.test_without_the_grant_no_engine_is_started_early`, `test_speak_cli.py::EarlyStartChatTest.test_the_voice_started_early_follows_the_language_of_the_message`, `test_speak_cli.py::EarlyStartChatTest.test_the_message_never_reaches_the_engine_only_the_reply_does`, `test_speak_core.py::EarlyStartTest.test_a_waiting_engine_for_another_voice_stays_and_a_second_one_starts`, `test_speak_core.py::EarlyStartTest.test_a_crashed_waiting_engine_is_replaced_by_a_fresh_one`, `test_speak_core.py::EarlyStartTest.test_an_engine_that_dies_as_the_text_arrives_is_replaced_once`, `test_speak_cli.py::EarlyStartChatTest.test_no_engine_is_left_waiting_after_quit_ctrl_d_ctrl_c_or_an_error`, `test_speak_core.py::NoOrphanTest.test_no_engine_process_is_left_after_the_chat_closes_it`, `test_speak_core.py::NoOrphanTest.test_no_engine_process_is_left_if_steel_is_killed`, `test_speak_cli.py::EarlyStartChatTest.test_a_whole_spoken_turn_uses_no_network_and_writes_no_file` |
| `scripts/latency_check.py` reads the PROCESSOR column, prints numbers and the reply length only, uses fixed impersonal prompts, plays nothing, keeps memory off, sets the scratch home before OpenJarvis reads a path, and contacts nothing without a scratch directory (**its live run is the owner's**) | `test_latency_check_script.py::LatencyCheckScriptTest.test_the_processor_column_is_read_for_the_chat_model`, `test_latency_check_script.py::LatencyCheckScriptTest.test_a_result_row_has_numbers_and_the_reply_length_never_its_text`, `test_latency_check_script.py::LatencyCheckScriptTest.test_the_prompts_are_fixed_short_and_impersonal`, `test_latency_check_script.py::LatencyCheckScriptTest.test_it_plays_nothing_keeps_memory_off_and_stays_in_the_scratch_home`, `test_latency_check_script.py::LatencyCheckScriptTest.test_without_a_scratch_directory_it_stops_before_contacting_anything` |
| `LICENSE` is the standard Apache-2.0 text after one copyright line; the package declares it and ships `LICENSE` and `NOTICE`; `NOTICE` names every component and default voice and holds the M-AILABS notice | `test_licence.py::LicenceTest.test_the_licence_is_the_standard_apache_text_after_one_copyright_line`, `test_licence.py::LicenceTest.test_the_package_declares_the_licence_and_ships_both_files`, `test_licence.py::LicenceTest.test_the_notice_names_every_component_and_default_voice` |

**What no test proves**, and what needs the owner's run:

* Any speed-up of the **model**: the warm-up and keep-alive are tested only for what they send. Whether the model is then warm at the first message, how long the warm-up takes, and how much keep-alive helps depend on Ollama and this machine's memory.
* That Ollama 0.40.3 honours `keep_alive` on each request as documented. The behaviour (the latest request's keep-alive wins) is from Ollama's documentation and was not observed here.
* That the early start helps in practice: it does when the model takes longer than the voice load (about 4 s; real engine, one run per case). A short reply from a warm model can arrive before the voice is ready, and then the wait is between 0.4 and 4 s.
* The extra CPU load of loading the voice while the model writes, which may slow the model a little. `--timings` shows both.
* GPU use (section 6).

## 9. Phase 5b-2: one context window, a warm prompt prefix, a voice that stays loaded

**The owner's live numbers that started this phase** (their machine, busy, swap 5.9 to 7.2 of 8.8 GiB used; from `scripts/latency_check.py` and `steel chat --speak --timings`; the assistant ran none of it): cold model start load 13.7 s and prompt 3.8 s; warm first token 0.85 s, generation 0.6 s, 11.7 tokens/s, 100% CPU; the warm-up and keep-alive work (load 0.0 s in the chat). Chat turn 1: first token 5.6 s, all of it prompt processing; turn 2: 0.9 s (prefix cached). First audio after the reply: 0.4 s on turn 1 and **5.6 s on turn 2** (the model was fast, the voice was not loaded). `ollama ps` showed CONTEXT 16384 during the chat (4096 for a plain `ollama run`) and 2.6 GB for the model. Three samples at most; one machine.

### 9.1 One context window (`STEEL_OLLAMA_NUM_CTX`)

* **Where 16384 came from:** `openjarvis/engine/ollama.py`, `_default_num_ctx()`: `$JARVIS_NUM_CTX`, else 16384, put into `options.num_ctx` of every request that does not pass its own `num_ctx`. STEEL's chat, warm-up and fact extraction passed none. Vision passed 4096 (docs/VISION.md), so with the same model every `/look` made Ollama reload it with another context, and every chat turn after it reloaded it again.
* **The fix is inside STEEL:** the client STEEL already gives a local `OllamaEngine` (section 8b) now sets `options.num_ctx` on every `/api/chat` and `/api/generate` request, replacing whatever the request held. OpenJarvis' source is not touched. It is installed for every local Ollama engine, with or without `STEEL_OLLAMA_KEEP_ALIVE`, and ignores proxy variables.
* **The setting:** `STEEL_OLLAMA_NUM_CTX`, a whole number from 2048 to 32768, default **4096**. Anything else is reported on stderr at start and 4096 is used.
* **All four call sites carry the same number:** the chat turn, the warm-up, the fact extractor (all through that client) and the vision call (`vision_config` reads the same setting; the engine `steel look` builds also gets the client, so it carries the same keep-alive too).
* **The prompt is kept inside the window (`steel/context.py`).** Ollama cuts a too-long prompt without saying so. Before each request STEEL estimates the prompt (3 characters per token, plus 4 per message, rounded up on purpose: other languages and code need more tokens than English text) and drops the oldest conversation messages, one at a time (never leaving an assistant reply first), until it fits in `num_ctx − 1024` (1024 tokens stay free for the reply). The system prompt (with memory) and the new message are never dropped. Remembered facts get at most a quarter of the window (a word is at least a token). So at 4096 the prompt may use about 3072 tokens. The history in RAM is not cut, only what is sent. One consequence: once the window is full, every turn drops the oldest message, so Ollama's cached prefix no longer matches and the turn pays the full prompt again; that is the price of a small window.
* Memory: the owner's `ollama ps` showed 2.6 GB for the model at 16384. The KV cache grows with the context, so 4096 should be smaller; how much smaller on this machine is for `scripts/latency_check.py` (it now prints `model_ram`, the SIZE column of `ollama ps`, and `context`).

### 9.2 A warm prompt prefix

The 5.6 s on turn 1 was Ollama reading STEEL's whole system prompt plus the owner's memory. Ollama keeps the processed prefix while the model stays loaded, so the start-up warm-up now sends **the same system message the first turn will send** (built by the same function, `ChatSession.build_messages`) and then the fixed word `ready`, with one token to generate. The first real turn then finds that prefix in the cache and only reads the new message.

* **Memory content leaves STEEL here exactly as in a normal turn**: to the Ollama on this computer, through the same OpenJarvis guardrails (secrets and personal data are redacted in the prompt the same way), never anywhere else. The old statement "the warm-up carries no memory" is false since Phase 5b-2 and has been replaced everywhere (SECURITY.md, docs/INJECTION.md, README.md, CLAUDE.md).
* It is built in the main thread before the background thread starts (the live notes are purged when read). Facts added in another terminal after start simply miss the cache; nothing becomes wrong.
* **Time-bounded:** the warm-up never delays the first prompt (a daemon thread, nothing joins it) and is limited only by the engine's own timeout (1800 s by default), so a stuck Ollama leaves a thread behind and nothing else. A real turn that comes while the warm-up is still running waits for it in Ollama's queue. No test proves a time limit on the warm-up itself.
* The fixed word `ready` stays as the user turn on purpose: some chat templates refuse a conversation with no user message, and a failed warm-up is silent. (Whether this model's does was not checked: `ollama show qwen3.5:2b --template` prints only `{{ .Prompt }}`, because Ollama renders this model's prompt in its own code.) It is the only user turn; there is none of the owner's.
* **What the cache needs:** the prefix must be byte-identical, so the first turn's system message (including the facts) must equal the warm-up's. A test captures both requests and compares everything before the last message.

### 9.3 A voice that stays loaded

The stock Piper CLI keeps the voice loaded between lines (docs/SPEAK.md section 2.3), so the engine now lives for the whole session instead of one reply. Each reply is one line on its stdin; the reply is over when no new audio arrives for the idle gap after audio has started (`STEEL_SPEAK_IDLE_END_SECONDS`, 0.5 to 5, default 1.5; longer for a very long sentence). Interrupts, dead engines, the memory cost (about 142 MiB per kept voice while the chat is open), the fallback to one process per reply, and every rule that did not change (own network namespace, text on stdin only, nothing on disk, stopped at every exit and by the kernel) are in docs/SPEAK.md section 6, "Kept loaded". The tests are listed in docs/SPEAK.md section 10 (`test_speak_persistent.py`).

### 9.4 What good numbers look like after this phase

These are **targets from the measurements above, not results**; nothing in this phase was measured with the real chat model.

| Number | Target | If not, check |
| --- | --- | --- |
| `steel chat --timings`, turn 1, first token | under about 2 s (was 5.6 s) | prompt not 0.x s: the warm-up may not have finished before you typed (wait a few seconds after the banner), or it failed silently (is Ollama on this machine, is the model name right?); `ollama ps` CONTEXT must be 4096 (or your setting), not 16384; a conversation near the window size no longer reuses the cache (section 9.1) |
| turn 2 and later, first token | about 1 s (was 0.9 s) | load not 0.0 s: Ollama reloaded the model: another program changed the context or the model (`scripts/latency_check.py` warns when CONTEXT is not STEEL's) |
| `ollama ps` CONTEXT | the number of `STEEL_OLLAMA_NUM_CTX` (4096) during the chat | 16384: the request did not go through STEEL's client, or another OpenJarvis program loaded the model last |
| `ollama ps` SIZE | less than the 2.6 GB at 16384 | unchanged: the model, not the context, dominates; the saving is then small |
| first audio after the reply, turn 2 and later | under about 1.5 s, `engine: warm` (state, not speed: section 12) | `engine: loading` on turn 2: the engine was replaced (an interrupted reply, a language change, or audio that came after the idle gap) or it never started (`steel speak check`); a language change costs a voice load only the first time per kept voice (Phase 5b-3, section 12); a reply that ends early: raise `STEEL_SPEAK_IDLE_END_SECONDS` |
| first audio, turn 1 | under about 1.5 s if the model took longer than the voice load (about 4 s), else up to the load time | the model answered before the voice was ready; nothing to fix |

### 9.5 Evidence

Each cell is `file::Class.test`. `tests/test_latency_evidence.py` checks that every cited test exists and that every test in `test_context.py`, `test_warmup_report.py` and `test_prompt_budget.py` is cited. All use fake engines, an `httpx.MockTransport` in place of the Ollama server, and a temporary home; none reaches Ollama, a model or the network. The speaking tests are in docs/SPEAK.md section 10.

| Claim | Test |
| --- | --- |
| `STEEL_OLLAMA_NUM_CTX` accepts only a whole number from 2048 to 32768 and falls back to 4096; a bad value is reported on stderr at start and vision uses 4096 too | `test_context.py::NumCtxSettingTest.test_only_a_number_from_2048_to_32768_is_accepted`, `test_context.py::NumCtxSettingTest.test_a_bad_setting_is_reported_at_start_and_4096_kept` |
| The same `num_ctx` reaches the chat turn, the warm-up, the fact extractor and the vision call, whatever OpenJarvis' default or `JARVIS_NUM_CTX` is; the default is 4096; the vision engine also carries the keep-alive | `test_context.py::SameContextEverywhereTest.test_the_same_num_ctx_reaches_all_four_call_sites`, `test_context.py::SameContextEverywhereTest.test_the_default_is_4096_even_when_openjarvis_would_ask_for_16384`, `test_context.py::SameContextEverywhereTest.test_the_vision_engine_also_carries_the_keep_alive` |
| A long fake history is cut from the oldest end until the prompt fits in the window minus the reply reserve (2048, 4096, 8192), keeping the newest messages in order and never starting with an assistant reply; a short one is sent whole; the system prompt and the new message are never dropped; facts get a quarter of the window at most | `test_context.py::FitToContextTest.test_a_long_history_is_cut_from_the_oldest_end_to_fit`, `test_context.py::FitToContextTest.test_a_short_history_is_sent_whole`, `test_context.py::FitToContextTest.test_the_system_prompt_and_new_message_are_never_dropped`, `test_context.py::FitToContextTest.test_the_facts_get_at_most_a_quarter_of_the_window` |
| The warm-up request equals the first turn's request in every message before the last (system prompt, remembered fact, live note), with the same `num_ctx`, one token to generate | `test_context.py::WarmPrefixTest.test_the_warm_up_is_byte_identical_to_the_first_turn_before_the_message` |
| It has exactly one user turn, the fixed word, none of the owner's; the start-up sends it from a background thread; a stuck one is a daemon thread that holds nothing up; a remote engine gets no warm-up and so no memory | `test_context.py::WarmPrefixTest.test_the_warm_up_ends_in_the_fixed_word_and_holds_no_message_of_the_owner`, `test_context.py::WarmPrefixTest.test_the_start_up_sends_that_warm_up_in_a_background_thread`, `test_context.py::WarmPrefixTest.test_a_stuck_warm_up_is_a_daemon_that_never_holds_up_the_chat_or_exit`, `test_context.py::WarmPrefixTest.test_a_remote_engine_gets_no_warm_up_with_memory_in_it` |
| `scripts/latency_check.py` reads the CONTEXT and SIZE columns of `ollama ps` and shows them in each row | `test_latency_check_script.py::LatencyCheckScriptTest.test_the_context_and_the_model_size_are_read_from_ollama_ps` |

**What no test proves**, and needs the owner's run: that turn 1 really gets faster (that depends on Ollama keeping the prefix, which it does by its documentation but was not observed here); that `ollama ps` shows CONTEXT 4096; the RAM saved by the smaller context; that Ollama accepts the warm-up (a fixed user word after the system prompt) and caches it like a real first turn; that the voice stays loaded between replies in a real chat with a real speaker (`steel chat --speak --timings`).

## 10. Phase 5b-3: why turn 1 is still slow, and how to see it

**The owner's live results that started this phase** (`steel chat --speak --timings`, four turns; one run, one machine): turn 1 (English) first token **5.3 s, all prompt processing**, although the start-up warm-up sends the same system prompt and memory (the owner does not know whether they typed before it finished); turns 2 to 4: first token 0.6, 1.5 and 0.9 s. First audio after the reply: 0.9 s (turn 1, engine warm), 1.0 s (turn 2, labelled `engine: loading` though the language did not change and nothing was interrupted), 3.0 s (turn 3, Spanish), 3.6 s (turn 4, English again): each language switch cost a voice load because only one engine was kept.

### 10.1 The warm-up made visible

* With `--timings`, one line says how the warm-up ended, **once, between turns**: `warm-up: done in 4.2 s`, `warm-up: failed (timeout)` or `warm-up: skipped (not-local)`. The codes are `timeout`, `connection`, `http-NNN` (the status number only), `error` and, for skipped, `not-local` (not OpenJarvis' `OllamaEngine`, or not on this computer). Never any text from the server. `failure_code` in `steel/warm.py` is the whole mapping.
* **Where the line appears.** The warm-up's own thread prints nothing, because a line written from a thread lands on whatever the terminal shows, including a half-typed `you>` prompt. The line is printed by the chat loop, from the main thread, just before a message is sent (and after a reply), so it can appear up to one message after the warm-up really ended. Test: `test_warmup_report.py::WarmUpOutcomeTest.test_the_warm_up_thread_prints_nothing_itself`.
* **A message sent while the warm-up runs** prints `waiting for the warm-up to finish` (always, with or without `--timings`) and that turn's timing line gains `queued behind warm-up 2.0 s`: the time from sending until the warm-up ended, not the whole request. Ollama answers one request at a time, so the message really does queue behind it. A turn that was not queued shows no such field. This tells the two cases apart: **queued** (you typed too early; the warm-up and your message ran one after the other) or **not queued** (the warm-up was over, and a slow first token then points to a cache miss, section 10.2).
* **Prompt tokens.** The line gains `prompt tokens 312 evaluated of ~340 (reused n/a)`. "Evaluated" is what Ollama reports as `prompt_eval_count`, the tokens it actually processed, which is fewer when it reused a cached prefix. "Of ~N" is OpenJarvis' figure for the whole prompt: the larger of Ollama's count and OpenJarvis' own estimate (4 characters per token), so approximate. **Ollama's response does not say how many tokens it reused**, so that stays `n/a`; reuse can only be read from the difference, and only roughly. If Ollama reports no count at all, OpenJarvis substitutes its estimate for it, and then "evaluated" is not a measurement. When the engine gives no usable numbers the field reads `prompt tokens n/a`.

### 10.2 Why turn 1 may miss the cache (read-only reasoning; no model was called)

**The two requests as they leave STEEL's transport** (captured with an `httpx.MockTransport` behind the real OpenJarvis `OllamaEngine` and STEEL's request client, `test_context.py::WarmPrefixTest.test_the_two_requests_differ_only_in_the_last_message_and_the_token_limit`):

| Field | Warm-up | First real turn | Same? |
| --- | --- | --- | --- |
| `model` | the chat model | the chat model | yes |
| `messages`, all but the last | system prompt + memory (`MEMORY.md`, `USER.md`, trusted facts, live notes) | the same, built by the same function | yes (byte-identical, same test) |
| `messages`, the last | the user turn `ready` | the owner's message | **no** |
| `stream` | `false` | `false` | yes |
| `think` | `false` (OpenJarvis adds it) | `false` | yes |
| `options.num_ctx` | STEEL's one value (4096) | the same | yes |
| `options.temperature` | 0.7 (OpenJarvis' default) | 0.7 | yes |
| `options.num_predict` | **1** | **1024** | **no** |
| `keep_alive` | the setting, if any | the same | yes |
| `seed`, `format`, `tools` | absent | absent | yes |

**Only two things differ: the last message and `num_predict`.** Neither is avoidable: the warm-up cannot know the owner's message, and a warm-up that generated 1024 tokens would keep the CPU busy for many seconds. No field differs by accident, so nothing was changed in the request. (Whether `num_predict` itself matters to Ollama's cache was not found documented; llama.cpp's prompt cache is keyed by the prompt tokens.)

**What can still stop Ollama reusing the prefix**, from most to least likely, none checkable without the model:

1. **The model's memory cannot be rewound (a hypothesis, not a finding).** `ollama show qwen3.5:2b` reports the architecture `qwen35`. llama.cpp users report that for Qwen3.5 and similar hybrid models (recurrent layers or sliding-window attention next to normal attention) the server "forces full prompt re-processing" when the cached sequence has to be cut back to a shared prefix, because the recurrent state exists only at the end of what was processed and at the few checkpoints that were kept ([llama.cpp issue 24714](https://github.com/ggml-org/llama.cpp/issues/24714), [issue 18497](https://github.com/ggml-org/llama.cpp/issues/18497), [a write-up](https://particula.tech/blog/prompt-reprocessing-swa-hybrid-models-kv-cache)). These are llama.cpp reports; I found nothing Ollama-specific, and Ollama may differ. If it applies, it fits the owner's numbers: the warm-up leaves a cached sequence that ends with `ready` and one generated token; the real turn shares only the system prompt with it and must cut the sequence back there, which a recurrent state cannot do, so turn 1 reads everything. Turns 2 to 4 *extend* the cached sequence (the whole previous conversation is their prefix), which needs no cut, and are fast (0.6 to 1.5 s). **If this is the cause, no change to the warm-up request can help turn 1**; only a shorter prompt (section 11) or a different model can.
2. **Cache slots.** llama.cpp keeps few prompt-cache slots; Ollama's own number depends on its parallelism setting. Any other request to the same model in between can take the slot: the fact extractor (it runs after a message, so not before turn 1), the vision call, `scripts/latency_check.py` run in another terminal, or any other program using the model. Not likely before turn 1 on its own.
3. **The warm-up was not finished.** Now visible: `queued behind warm-up` (section 10.1). Then the message waited, and its first token also includes the unfinished warm-up's work.
4. **The runner was reloaded** between the two (a different context, a model unload and load): visible as `load` above 0 in the timings line.

**How to tell which, with the new line, on turn 1** (type a few seconds after the banner, with `--timings`):

| What the line shows | Reading |
| --- | --- |
| `queued behind warm-up` | cause 3: type later |
| not queued, `load 0.0`, `prompt tokens` about the full size, first token still about 5 s | cause 1 or 2: the cache was not reused |
| not queued, `prompt tokens` small (a few dozen), first token about 1 s | the cache was reused: the warm-up works |

**Remaining uncertainty:** which of causes 1 and 2 applies is not known. A run with `OLLAMA_DEBUG=1` in the Ollama service and `sudo journalctl -u ollama` would say (it needs sudo, so it is the owner's), as would running the same two requests against a non-hybrid model.

### 10.3 Tests

| Claim | Test |
| --- | --- |
| The warm-up line says done and how long; a failure gives only a code (`timeout`, `connection`, `http-500`, `error`), never the server's text; a warm-up that was not started says `skipped (not-local)`; the warm-up's thread prints nothing | `test_warmup_report.py::WarmUpOutcomeTest.test_a_finished_warm_up_says_how_long_it_took`, `test_warmup_report.py::WarmUpOutcomeTest.test_a_failed_warm_up_gives_a_reason_code_and_never_text`, `test_warmup_report.py::WarmUpOutcomeTest.test_a_warm_up_that_was_not_started_says_why`, `test_warmup_report.py::WarmUpOutcomeTest.test_the_warm_up_thread_prints_nothing_itself` |
| With `--timings` the line is printed once, between turns, before the first reply; without it nothing; failed and skipped the same way | `test_warmup_report.py::WarmUpInTheChatTest.test_the_warm_up_line_is_printed_once_between_turns_with_timings`, `test_warmup_report.py::WarmUpInTheChatTest.test_without_timings_nothing_about_the_warm_up_is_printed`, `test_warmup_report.py::WarmUpInTheChatTest.test_a_failed_or_skipped_warm_up_is_reported_the_same_way` |
| A message sent during the warm-up prints the waiting line once and the timing line shows the queued time; the next turn shows none; the queued time is the wait until the warm-up ended, not the whole request | `test_warmup_report.py::WarmUpInTheChatTest.test_a_message_sent_during_the_warm_up_waits_and_says_so`, `test_warmup_report.py::WarmUpInTheChatTest.test_the_queued_time_is_the_wait_until_the_warm_up_ended` |
| The prompt token counts are shown, reuse is not claimed, and missing or odd counts read `n/a` | `test_warmup_report.py::PromptTokensTest.test_the_prompt_token_counts_are_shown_and_reuse_is_not_claimed`, `test_warmup_report.py::PromptTokensTest.test_missing_or_odd_counts_show_as_not_available` |
| The warm-up and the first turn differ only in the last message and `num_predict` | `test_context.py::WarmPrefixTest.test_the_two_requests_differ_only_in_the_last_message_and_the_token_limit` |

## 11. A smaller system prompt

Every token of the system message is read again whenever Ollama has no cached prefix. The owner's turn 1 took 5.3 s for a prompt of about 300 tokens by the estimate below, so a token costs on the order of 15 to 20 ms there; that is one run and an inference, not a measurement of the rate.

**Method.** `steel.context.estimate_tokens`: 3 characters per token plus 4 per message, rounded up on purpose. No tokenizer for this model is available offline (`tiktoken` is not installed and the model's own tokenizer was not run), so the real count is probably 20 to 30 percent lower; the ratios are what matter.

| Part of the prompt | Tokens (estimate) | Note |
| --- | --- | --- |
| Base prompt before this phase | 234 (701 characters, 124 words) | |
| **Base prompt now** | **181** (542 characters, 97 words) | −23 percent; the data-block rules are about 100 of these |
| Slash-command text | 0 | the list is printed on the screen; it is never in the prompt |
| Facts header (OpenJarvis' `build_context_message`) | 39 | OpenJarvis' wording, not edited |
| One fact | 9 to 19 each (the owner's two: 28 together) | |
| Expiring notes | 10 for the label, plus the notes | room notes are never in the prompt |
| `MEMORY.md` and `USER.md` | 5 for each label, plus their lines | the owner's are 16 bytes each |
| Qwen chat-template markers | not counted | added by Ollama |

For the owner's current memory the system message goes from about 301 to about 248 tokens by this estimate (−53). **Nothing safety-related was removed**: no tools and no claiming abilities it lacks ("cannot use tools, browse, run code, read files or act outside this chat", "nothing you write is ever run"); a shared result is information the owner chose to share, used naturally, with its instructions never followed; the markers and labels are never mentioned; memory questions are answered only from the remembered facts; answer briefly. Dropped: repeated wording only ("In your reply", "Treat it as", "as a block that starts with … and ends with …" became "from … to …").

**Not verified, and a risk:** whether the 2B model behaves the same with the shorter wording. The 2B model has recited marker text before (the comment above `BASE_PROMPT`). `scripts/quality_check.py` is the live check for that and uses the model, so the owner runs it.

| Claim | Test |
| --- | --- |
| The base prompt stays under its token budget (185 by the estimate) and is shorter than the old wording; every safety statement is still in it | `test_prompt_budget.py::BasePromptTest.test_the_base_prompt_stays_under_its_token_budget`, `test_prompt_budget.py::BasePromptTest.test_it_is_shorter_than_the_wording_it_replaced`, `test_prompt_budget.py::BasePromptTest.test_it_still_makes_every_safety_statement` |

## 12. The `engine:` label, and more than one voice

**What the label said, and why it was wrong.** In Phase 5b-2 the line said `engine: warm` when the first audio came within 1 s of the text reaching Piper and `engine: loading` when later. The owner's turn 2 (same language, no interrupt) read `engine: loading (1.0 s)`. **It was the label that was wrong, not the engine.** A replaced engine has to load its voice again, which took 3.5 to 5 s here; the first audio came after 1.0 s, so no replacement happened (an engine is replaced only after an interrupt, a death, a silent reply, late audio found in its pipe, or a voice evicted for another). A warm engine can take 1 s to start talking when the first sentence is long, or when the CPU is busy with the model or the fact extraction, which run on the same six cores. I could not reproduce the owner's 1.0 s with the real engine here (an idle machine gave 0.05 to 0.44 s); the explanation (a slow first sentence on a busy CPU) is a reading of the code and the measurements, not an observation of that turn.

**Definition now** (the code decides it when the text reaches the engine, not from the seconds): `warm` = the engine process was already running with its voice loaded: it finished a reply before, or it was started at least 6 s earlier. `loading` = it was started or restarted for this reply, or started less than 6 s before and never used (its voice may still have been loading). The 6 s is the measured voice load (3.5 to 5 s) with a margin, not a guarantee: under memory pressure a load takes longer, and then an engine called `warm` can still be loading. Tests: `test_speak_persistent.py::PersistentEngineTest.test_the_label_is_about_the_engine_state_not_the_speed_of_the_first_audio` (a 1.04 s first audio is `warm`, and the engine is not replaced), `test_an_engine_started_early_is_warm_once_the_voice_had_time_to_load`, `test_after_an_interrupt_the_next_reply_is_loading_then_warm`.

**More than one voice.** `STEEL_SPEAK_KEEP_VOICES` (1 to 3, default 2) keeps the most recently used voices' engines running, and `STEEL_SPEAK_VOICE_IDLE_MINUTES` (5 to 240, default 30) stops one nobody used. Every rule of the single engine applies to each (own network namespace, text on stdin only, `setpriv --pdeathsig`, replaced on death, late audio dropped after an interrupt, no orphan), and an interrupt replaces only the engine that was speaking. Design, memory (about 142 MiB each for the English voice) and tests: docs/SPEAK.md sections 6 and 10. **Expected:** English, Spanish, English: the Spanish reply still pays one voice load (about 3 to 4 s, the first time), the return to English does not (`engine: warm`).
