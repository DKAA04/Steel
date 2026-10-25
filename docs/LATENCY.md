# Latency of a chat turn (Phase 5b)

**Status: measurement plan and findings, written and committed before any optimisation (see the git history).** The fixes come after it; section 8 says which were made and which test proves each. What needs the owner's live run is marked **owner's run**. The assistant that wrote this did not run the chat model, play sound or open a microphone or camera; it ran the Piper engine into memory only (section 5).

## 1. The problem

Two delays were noticed in the owner's live use:

* the chat model (`qwen3.5:2b` through Ollama, on the CPU) is slow, and slower still when it is "cold": Ollama unloads a model that has been idle for its keep-alive time (default 5 minutes), and the next message must load it again;
* every spoken reply waits about 4 s before the first sound, because each reply starts a new Piper process that loads its voice first (docs/SPEAK.md section 2).

## 2. `steel chat --timings`

Off by default. After each turn it prints one line, numbers only:

```
timings: model 9.4 s | first token 2.1 s (load 0.0 s, prompt 2.1 s) | generation 7.2 s | 11.3 tokens/s | first audio 0.4 s after the reply | played 4.7 s
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

**Decision, not asked for in this form:** a long-lived per-session engine is not possible with a clear end signal, so STEEL keeps one process per reply, with stdin closed at the end of the text, which gives a clear end (end of file). It **starts that process early, before the reply exists**, so the voice loads while the model is still writing. This uses the stock CLI exactly as before; section 8 says how it is done and tested.

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
* `JARVIS_NUM_CTX` (OpenJarvis' context size, default 16384) set lower uses less memory. A conversation longer than the context is then cut.
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

*Filled in by the commits that follow this one.*
