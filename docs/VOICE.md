# Voice (Phase 2c): push-to-talk, fully local

**Status: implemented (Phase 2c), except where marked.** Spoken replies moved to their own design in Phase 5a: docs/SPEAK.md. This document was written and committed *before* any audio code (see the git history), and the code was then built against it. Section 9 says, per claim, which test proves it, and what no test can prove. A claim without a named test there is not claimed. **Not implemented here: the wake word, which exists only as the separate `steel jarvis` loop (section 7, docs/JARVIS.md); and everything that needs the real microphone, which the test suite never opens.** Speech output is Phase 5a (docs/SPEAK.md); the earlier `espeak-ng` call is gone.

Scope: push-to-talk speech input, and optional spoken replies. **No wake word and no always-on listening in this design**; the one exception the owner allowed, the foreground `steel jarvis` loop, is designed and documented separately in docs/JARVIS.md (section 7 below). Nothing leaves the computer.

## 1. What this machine has (evidence)

Gathered read-only; nothing was recorded. Commands were run on the owner's machine on 2026-10-24.

| Question | Evidence |
| --- | --- |
| Capture hardware | `arecord -l`: one capture device, `card 1: Generic_1 [HD-Audio Generic], device 0: ALC257 Analog`. `/proc/asound/pcm` lists capture only on `01-00`. |
| Playback hardware | `aplay -l`: three HDMI outputs on card 0 and `ALC257 Analog` on card 1. |
| Sound server | PipeWire 1.6.9 (`pipewire`, `pipewire-pulse`, `wireplumber` running). `wpctl status`: default source `Ryzen HD Audio Controller Analog Stereo`. `arecord -L`: the ALSA `default` device is "currently PipeWire". |
| Tools present | `arecord`, `aplay`, `pactl`, `wpctl`, `ffmpeg`, `sox`, `libportaudio.so.2`. |
| Tools absent | `espeak-ng`, `espeak`, `piper`, `festival`, `flite`, `spd-say` (so no local text-to-speech is installed). |
| Is the microphone open now? | `/proc/asound/card1/pcm0c/sub0/status` reads `closed`; `fuser /dev/snd/*` shows only `wireplumber` (control devices) and `pipewire` (sequencer). |
| CPU and memory | AMD Ryzen 5 5500U, 6 cores / 12 threads, AVX2 and FMA. 14 GiB RAM, about 4.4 GiB free during the measurements (a browser and an editor were open). |
| User groups | `audio` is among them. |

**One disclosure.** While checking whether the Python packages worked I ran `import sounddevice` once. That import calls `Pa_Initialize()` (visible in `sounddevice.py`: `_initialize()` runs at import), and PortAudio can briefly open and close device handles to list them. No stream was started and no audio was read; the capture status read `closed` immediately afterwards. This is also the reason STEEL never imports `sounddevice` (section 3).

## 2. What OpenJarvis offers (all of `openjarvis/speech/` was read)

Pinned commit `108d257`. Twelve files, 1,217 lines.

| Backend | Registry key | Where it runs | Needs | Network? |
| --- | --- | --- | --- | --- |
| Faster-Whisper (speech to text) | `faster-whisper` | this computer (CTranslate2) | `faster-whisper`, a model | Downloads the model from Hugging Face **if the model is not already a local directory** (`WhisperModel(name)`). Runtime transcription: none (measured, section 4). |
| OpenAI Whisper API (speech to text) | `openai` | **cloud** | `OPENAI_API_KEY` | **Yes**: `client.audio.transcriptions.create` uploads the audio. |
| Deepgram (speech to text) | `deepgram` | **cloud** | `DEEPGRAM_API_KEY`, `deepgram-sdk` | **Yes**: `transcribe_file` uploads the audio. |
| Kokoro (text to speech) | `kokoro` | this computer | `kokoro` and PyTorch (not installed) | Not read here: the `kokoro` package is not installed, so how it obtains its model files was not checked. |
| OpenAI TTS | `openai_tts` | **cloud** | `OPENAI_API_KEY` | **Yes**: `httpx.post("https://api.openai.com/v1/audio/speech")` sends the text. |
| Cartesia | `cartesia` | **cloud** | `CARTESIA_API_KEY` | **Yes**: `httpx.post("https://api.cartesia.ai/tts/bytes")` sends the text. |
| `voice_io.py` | n/a | this computer | `sounddevice` (+ `soundfile`, `numpy` for playback) | No. `record_until_silence` records with a fixed RMS threshold and returns WAV bytes. |

Findings that decide the design:

1. **The discovery functions can pick a cloud backend.** `get_speech_backend` defaults to `speech.backend = "auto"` and tries `faster-whisper`, then `openai`, then `deepgram`. If the local one is not healthy and `OPENAI_API_KEY` or `DEEPGRAM_API_KEY` is in the environment, the audio goes to the cloud without any prompt. `get_tts_backend` is the same for speech output (`kokoro`, `openai_tts`, `cartesia`). **STEEL must not call either function.**
2. **Importing anything from `openjarvis.speech` loads all of it.** `speech/__init__.py` imports the cloud backends too (and with them the `openai` client library). They make no call at import time, but a process that never needed a cloud backend should not contain one.
3. **The Faster-Whisper backend writes the audio to a file.** `transcribe()` saves it with `tempfile.NamedTemporaryFile(delete=False)` in the default temp directory and removes it afterwards. The requirement here is RAM only.
4. **The backend takes a model *name*.** `WhisperModel("small")` downloads the model when it is missing, so a first run would silently use the network.

**Decision: STEEL does not import `openjarvis.speech`.** The "import rather than copy" preference is kept for everything it fits (policy executor, tool base classes, registry types, config, the event bus). Here the stock backend cannot be used as it is, and the reason is not analytics, telemetry or the Rust extension (none of them is involved) but points 1 to 4 above. `faster-whisper` itself accepts a NumPy array (so no file: `transcribe.py` skips `decode_audio` for an ndarray) and a model *directory* (so no download: `os.path.isdir` is checked before `download_model`), which makes a ~70-line module of STEEL's own against `faster_whisper`'s public API simpler and safer than wrapping the OpenJarvis class. No OpenJarvis code is copied, so no provenance row is needed (a note in `docs/PROVENANCE.md` says so). A test fails if STEEL's voice code imports `openjarvis.speech`, calls `get_speech_backend`/`get_tts_backend`, or mentions a cloud backend's module.

## 3. What was installed

Command, as requested: `uv pip install --python ~/.openjarvis/.venv/bin/python -e "$HOME/.openjarvis/src[speech]"`. The `uv sync` suggestion from `jarvis doctor` was not used. The `openjarvis` editable install was re-linked (same version string `1.0.5.dev119+g108d257`; `git -C ~/.openjarvis/src status` is clean and `HEAD` is unchanged).

Twelve packages were added to `~/.openjarvis/.venv` (before: 509,668 KB; after: 861,320 KB, so **+343 MiB**):

| Package | Version | On disk |
| --- | --- | --- |
| `ctranslate2` | 4.8.3 | 62 MB + 75 MB of bundled libraries |
| `onnxruntime` | 1.31.0 | 63 MB |
| `av` (PyAV) | 19.0.2 | 26 MB + 101 MB of bundled libraries |
| `tokenizers` | 0.23.3 | 12 MB |
| `soundfile` | 0.14.0 | 3.6 MB (with libsndfile) |
| `faster-whisper` | 1.2.1 | 1.4 MB |
| `protobuf`, `flatbuffers`, `cffi`, `pycparser`, `sounddevice` | 7.36.2, 25.12.19, 2.1.1, 3.12, 0.5.6 | under 2 MB together |

`sounddevice` and `soundfile` arrive with the `[speech]` extra but **STEEL does not use them**: capture is `arecord` (section 6.1) and spoken replies use Piper as a separate program (docs/SPEAK.md). They can be uninstalled; nothing in STEEL imports them.

Models (the one network download, done by hand with `huggingface_hub.snapshot_download`, telemetry off, pinned to a revision; the only files fetched are `config.json`, `model.bin`, `tokenizer.json`, `vocabulary.txt`). They live in `~/.openjarvis/steel_models/` (605 MiB in all; sizes in this document are MiB unless it says MB):

| Directory | Repository and revision | Size | `model.bin` SHA-256 |
| --- | --- | --- | --- |
| `faster-whisper-small` | `Systran/faster-whisper-small` @ `536b066274…` (MIT) | 464 MiB | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |
| `faster-whisper-base` | `Systran/faster-whisper-base` @ `ebe41f70d5…` (MIT) | 142 MiB | `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9` |

A `tiny` model was fetched for the measurements only and deleted. Not installed: `kokoro`, PyTorch, `piper`, `espeak-ng`.

**onnxruntime telemetry (found in Phase 5a, fixed then).** `onnxruntime` 1.31.0, which faster-whisper uses for its voice-activity detection (`vad_filter=True` and the silence check), contains Microsoft "1DS" telemetry on Linux: its library holds the collector address `https://mobile.events.data.microsoft.com/OneCollector/1.0` and it keeps a device id and an event queue in `~/.cache/Microsoft/DeveloperTools/.onnxruntime/` (`deviceid`, `onnxruntime.db`). On the owner's machine that `deviceid` was created on 2026-10-24 at 21:31 CEST, which fits the owner's live voice check, so **from Phase 2c until this fix, `steel voice listen` and `steel chat --voice` very likely sent telemetry** (device id, interpreter path, OS, CPU model, connection type; no audio or text was seen in the queued events). Whether events were uploaded cannot be proven from here; the queue held none from that day when it was read on 2026-10-25, which suggests they were. During Phase 5a the assistant's own probes queued two more events (10:22 and 10:23 UTC) before the switch was known. **Fix:** STEEL now sets `ORT_DISABLE_TELEMETRY=1` at the start of every `steel` command and again right before faster-whisper loads (`steel/offline.py`); with it, a test run with a scratch home wrote no telemetry files, without it the same run wrote `deviceid` and `onnxruntime.db`. The existing files are the owner's to delete (`rm -r ~/.cache/Microsoft/DeveloperTools/.onnxruntime`); STEEL does not touch them. The earlier statement "nothing is sent over the network" in this document was about STEEL's own code and the speech model; it did not hold for this library until the fix.

## 4. Speech to text: the choice

**Chosen: `faster-whisper`, model `small` (multilingual, not `small.en`), `int8` on the CPU, run through STEEL's own thin wrapper.** `base` stays installed as the fast fallback (`STEEL_VOICE_MODEL=base`).

Measured on this machine (AMD Ryzen 5 5500U, `int8`, one utterance each, median of 3 runs, model already in the disk cache). The machine was **busy**: load average 5 to 11, partly my own 12-thread benchmark and a browser tab at about 70% of a core, so these are pessimistic.

| Model | Load | 3.8 s sentence, language fixed | 3.8 s sentence, language auto-detected | 9.6 s sentence, auto | Peak memory |
| --- | --- | --- | --- | --- | --- |
| `tiny` | 0.4 s | n/a | 1.05 s | 1.84 s | n/a |
| `base` | 0.6 s | 1.06–1.21 s | 1.81–1.91 s | 2.44 s | 318 MB |
| `small` | 1.5 s | 3.29–3.58 s | 6.06–6.71 s | 7.09 s | 606 MB |

* Thread count (4, 6, 12) and beam size (1, 5) changed the time by less than 15% either way, so STEEL uses `beam_size=1` and the library default thread count.
* **Language detection roughly doubles the time** (one extra pass of the 30-second window). So the wrapper accepts a *list of allowed languages* and detects among those only (default `en,es,it`, the owner's languages); a single code (`STEEL_VOICE_LANGUAGE=es`) skips detection; `auto` detects among all.
* **Vocabulary hint (since Phase 4).** In the owner's live check, "STEEL" was heard as "Steele". Whisper's `initial_prompt` is now set to a short hint: `STEEL` by default, or the value of `STEEL_VOICE_HINT` (1 to 200 printable characters; anything else falls back to `STEEL`; if the value lacks the word `STEEL` it is put in front, as `STEEL, <value>`). The owner's name goes in that setting, not in the code. The hint is local (it goes only to the local model) and is shown by `steel voice check`. It helps spelling, not accuracy in general, and **its effect was not measured**: a hint can also make Whisper repeat it on near-silence, which the VAD gate below mostly prevents.
* **Silence costs real time and can invent text.** 5 s of silence or low noise took 0.43 s (`tiny`), 1.0 s (`base`) and 3.0 s (`small`) even with the library's VAD filter on. STEEL runs the bundled Silero VAD itself first (12.8 ms for a sentence, 17 ms for silence or noise, measured) and skips the model when it finds no speech. Neither silence nor noise produced text in these runs; a real room may.
* Documented figure (`faster-whisper` README, shipped in the installed package metadata): `small`, `int8`, beam 5, 13 minutes of audio, 1 min 42 s on an Intel Core i7-12700K with 8 threads. That is long-audio throughput and says little about a 4-second sentence, where the fixed 30-second window dominates; the table above is the number to plan with.

**What the accuracy evidence is, honestly.** Three English sentences from the `flite` synthetic voice (no recording of a person, no microphone): all three models transcribed the short sentence correctly; for "my sister is called Mara", `tiny` produced "Marlenter Burtier's", `base` "Mare" and `small` "Mara"; all three garbled the longest sentence (the `kal` voice is very robotic). That is **one synthetic voice and three sentences**: an anecdote, not a measurement. **No Spanish or Italian speech could be generated here, so accuracy in those languages has not been measured at all.** The expectation that larger Whisper models make fewer mistakes in non-English languages comes from the Whisper paper and model cards, which were not re-checked offline. `scripts/voice_check.py` (step 5) records 5 seconds from the real microphone and can transcribe with both installed models, so the owner can compare them on a real voice before trusting `small`.

**Why `small` over `base`:** a wrong transcript costs more than a few seconds of waiting, because the owner has to notice and discard it, and the multilingual claim is the requirement. **Why not `medium` (1.5 GB) or `large-v3-turbo` (1.6 GB):** not measured; they are slower than `small` by a large factor on this CPU, and the 30-second window already makes `small` take 3–7 s.

**Offline guarantee, measured.** With `socket.connect`, `connect_ex` and `getaddrinfo` made to raise, loading the model from its directory, running the VAD, detecting the language and transcribing all succeeded with **zero network attempts**, and importing `faster_whisper` loaded no `openjarvis`, `openai`, `deepgram` or analytics module. The wrapper loads the model only from a directory that exists, with `local_files_only=True`, so it cannot download.

## 5. Text to speech: the choice

**Superseded in Phase 5a by docs/SPEAK.md.** Phase 2c had only an optional `espeak-ng` call (not installed, fake-tested) and no capability for the speaker. Phase 5a removed that call and added Piper, run as a separate program with no network, behind its own capability `audio:play` (tool `speak`, agent `voice`). `openai_tts`, `cartesia` and OpenJarvis' Kokoro backend are still never used.

## 6. Consent and privacy design

### 6.1 How the microphone is read

* **One mechanism: `arecord` writing raw audio to a pipe.** `arecord -q -D default -f S16_LE -r 16000 -c 1 -t raw -d <max+1>` with no output file. The command line was checked against the ALSA `null` device (no hardware): 2 seconds gave exactly 64,000 bytes on standard output. `-D default` is PipeWire here, so the desktop's own microphone indicator and `wpctl status` also show the stream, which is an independent way for the owner to see it.
* **Why `arecord` and not `sounddevice`:** importing `sounddevice` initialises PortAudio (section 1), a second library would hold the device, and `arecord` is a process the owner can see in `ps` for exactly as long as the microphone is open. `-d` is a second, independent stop: if STEEL died, `arecord` would still end by itself after the maximum length, and it exits at once when its pipe closes.
* **The real source is its own small module** (`steel/voice/mic.py`) behind an `AudioSource` interface. Everything else is tested with a fake source; the real one is tested only with a fake process (section 9), because the tests must never open a real microphone.

### 6.2 Push-to-talk

* The microphone opens **only after an explicit key press in the terminal**, once per recording, never in the background and never at start-up. A terminal reports a key *press* but not a key *release*, so "hold to talk" is not possible; the design is **toggle**: Enter starts, Enter stops. Typed-ahead keys are flushed first, so a stray Enter typed earlier does not start a recording.
* It stops on whichever comes first: Enter, the **maximum length** (30 s by default; `STEEL_VOICE_MAX_SECONDS`, capped at 120, a value that is not a number falls back to 30), the **silence cut-off** (1.5 s of quiet after speech; `STEEL_VOICE_SILENCE_SECONDS`), a give-up if nobody speaks for 5 s, or Ctrl-C.
* The silence test is an energy threshold: 16-bit RMS 500 of 32,768 (the value OpenJarvis' `voice_io` uses), changeable with `STEEL_VOICE_SILENCE_RMS` (0 to 10,000). The owner's microphone gain is low (PipeWire source volume 0.27), so it may need tuning; `scripts/voice_check.py` prints the loudest 100 ms level it heard for that. Whether 500 suits this microphone has **not been measured**.

### 6.3 The visible indicator

* While the microphone is open, stderr shows a line that starts with `● REC` and the elapsed time, redrawn about twice a second, e.g. `● REC 00:07  Enter = stop, 30 s max`. When the microphone is closed the line is replaced by `■ stopped after 7.2 s, microphone closed`.
* **It is impossible to open the microphone without printing it, by construction:** `Indicator.show()` returns a proof object only after the line was written and flushed to a terminal; `AudioSource.open()` requires that proof, and the recorder is the only caller. If stderr is not a terminal, or the write fails, there is no proof and the microphone is not opened. This stops accidental misuse, not a program that forges the proof: STEEL is not a sandbox (section 8).
* The tool also has to refuse before the indicator if there is no consent, no grant or no terminal: the microphone is the last thing touched.

### 6.4 Consent

* **First use asks in plain words** (`steel voice consent`) and records consent **as a timestamp only** in `~/.openjarvis/steel_voice_consent.json` (mode 600). The owner can delete that file (or run `steel voice revoke`) at any time. **No file means no microphone**, and this is checked in the tool itself, not only in the commands.
* The consent text (the code uses exactly this wording, and a test compares them):

> **STEEL voice input: please read**
>
> STEEL can turn your speech into text, using the microphone of this computer.
>
> - The microphone opens only after you press a key, and only for one recording at a time. It closes when you press the key again, when the maximum length is reached (30 seconds), or after a pause in your speech.
> - While it is open, STEEL prints a "● REC" line with the seconds elapsed. If that line cannot be printed, the microphone is not opened.
> - The sound is kept in memory only. It is turned into text on this computer and then thrown away. No audio file is written, and nothing is sent over the network.
> - You see the text first. It is used (sent to the model, and searched for facts about you) only when you confirm it. If you discard it, it is thrown away.
> - The microphone hears everyone who is speaking near it, not only you. Do not use it when other people might be heard without knowing.
> - This permission is stored as a date and time in a file in your OpenJarvis folder. You can withdraw it at any time with: steel voice revoke
> - You must also grant the microphone tool by hand in steel_policy.json. This consent does not do that.
>
> Type yes to agree.

* Consent is **not** a policy grant, and the grant is **not** consent: both are needed (section 6.6).

### 6.5 Audio handling

* Audio exists only as `bytes` in RAM: the pipe from `arecord`, a `bytearray` in the recorder, and a NumPy array inside the transcriber. **No audio file is written** by STEEL or, with an array input, by `faster-whisper`. The tool overwrites the recording's `bytearray` with zeros in a `finally` (the recorder does the same itself if reading fails) and the transcriber zeroes its float array in a `finally`; this is best effort, because Python may keep copies, and swap can write RAM to disk.
* **No temporary file exists in this design.** If a future backend needs one, it must go in a private directory (mode 700) with a mode-600 file, deleted in a `finally`, and this document must say so first. The `faster-whisper` path used here needs none.
* The tool returns **the transcript text only**. The policy log never holds text (names, reasons and times only), and the tool is run with no event bus, so arguments and results are not published anywhere.

### 6.6 Permission policy

The tool is `microphone_listen`, declares exactly one capability, `sensor:microphone`, and runs only through `build_executor`. The policy already refuses a wildcard for sensors and a sensor grant with pattern `*`, so the owner must name the tool. Example (written **by hand** as `~/.openjarvis/steel_policy.json`, mode 600; STEEL never writes this file):

```json
{
  "agents": [
    {
      "agent_id": "voice",
      "grants": [
        {"capability": "sensor:microphone", "pattern": "microphone_listen"}
      ]
    }
  ]
}
```

Order of the checks in `steel voice listen` and `steel chat --voice`: the command checks the consent file; the executor makes the policy decision, which is logged; the tool checks the consent file again; then the terminal and the start key; then the REC line; then the microphone. A denial happens before any sound exists and is logged with the agent, `sensor:microphone`, `deny` and a reason code. A grant with no consent, or consent with no grant, opens nothing.

### 6.7 The transcript

* It is shown to the owner **before** it is used: `You said: "…"` and then Enter to send or Esc to discard.
* Confirmed text goes through the **same path as typed text** (`ChatSession.ask`): the OpenJarvis guardrails, then the user-only fact extraction, then the same memory. Discarded text is dropped: it is not sent to the model, not published to the event bus, and never reaches the fact extractor.
* The transcript is never stored by the voice code itself.

### 6.8 Other people

**Anyone who speaks near the microphone while it is open is recorded** (in RAM, and transcribed). The microphone does not know whose voice it hears, and STEEL does no speaker identification. What limits this is the push-to-talk design: the microphone is open only for short, deliberate, visible recordings, never in the background. It is not a privacy guarantee for third parties; the owner must not record people who do not know. Whatever a bystander said can end up in the transcript, and, if the owner confirms it, in the model prompt and in the fact store.

## 7. Wake word: only the foreground `steel jarvis` loop (Phase 5c, docs/JARVIS.md)

**Update 2026-10-25.** The owner lifted the rule "no wake word and no always-on listening" **only for the design in [docs/JARVIS.md](JARVIS.md)** (the foreground `steel jarvis` loop), and only if that design meets every requirement below; where the two differ, the stricter wins. It stays forbidden everywhere else: no background service or daemon, no start at login, no listening without the loop running in a foreground terminal, no cloud speech service. **Status (2026-10-26): implemented and tested with fakes, not yet run live by the owner.** docs/JARVIS.md section 3 answers each requirement below, one subsection each, and section 16 cites the test for each: a separate always-on consent (3a), the red window outside the terminal plus the terminal line, both required before the microphone opens (3b), `steel jarvis stop`, the guard's watchdog and `setpriv --pdeathsig KILL` (3c), nothing kept before the trigger (3d), the review of third parties (3e) and the capability `sensor:microphone_always_on` (3f). The push-to-talk design in the rest of this document is unchanged. The text below is the original section, unchanged.


A wake word ("hey STEEL") would mean **a microphone that is always open**, listening for a trigger. That is outside this phase and outside this design. If it is ever built it needs its own design and review first. The risks it brings:

* **Always-on microphone.** Sound is processed continuously, all day, with no recording marker the owner chose to start. Even if nothing is kept, the microphone is open when the owner is not thinking about it.
* **False triggers.** A word spoken on television or by a visitor can open a recording nobody asked for.
* **Third parties.** People in the room are listened to by default, not by their own or the owner's moment-to-moment choice. In a shared home or a client's office that is a much bigger problem than a push-to-talk key.
* **Hard to see.** A permanent REC line is not workable; any indicator would have to be a persistent, separate one.

What it would need before it is built: a separate written consent that says "always on" in plain words, a persistent visible state outside the terminal, a physical or one-command way to turn it off that works while STEEL is wedged, a rule about what is kept (nothing before the trigger), a second review of third parties, and its own capability name in the vocabulary rather than reusing `sensor:microphone`.

## 8. Limits and open questions

* **Not a sandbox.** The indicator proof, the consent check and the grant all live in the STEEL process; code running in that process can ignore them. The tool is trusted code.
* **The real microphone path is untested by the test suite** (a rule of the project): `ArecordSource` is tested with a fake process, and `scripts/voice_check.py` is the live check, run by the owner.
* **Accuracy in Spanish and Italian is unmeasured**, English accuracy rests on three synthetic sentences, and the speed figures were taken on a busy machine.
* **Terminal limits.** There is no key-release event; the Esc key is told apart from arrow keys by a 50 ms wait, which can mis-read a very slow key sequence. Key handling was tested on a pseudo-terminal, not on the owner's terminal emulator.
* **Ctrl-C and a wedged device.** `cancel()` closes the source, which ends `arecord` and unblocks a stalled read (tested with a fake source that stalls). Whether `arecord` really exits promptly when its device wedges on this machine is untested. If STEEL is killed with SIGKILL, `arecord` ends when its pipe closes or after `-d`, whichever is first.
* **Slow when memory is tight.** With the swap busy, loading the speech model or the chat model can take far longer than the figures above. `steel doctor` warns (without failing) when more than half of the swap is in use or less than 2 GiB is available (`steel/meminfo.py`, tested with fake `/proc/meminfo` text in `test_meminfo.py`).
* **Typed slash commands work in `steel chat --voice` too (Phase 4); a transcript is never a command.** A line typed at the `you>` prompt that starts with `/` runs as in `steel chat` (docs/INJECTION.md); a transcript that reads like one ("/search tea") is sent to the model as text, after the owner confirmed it, and runs nothing (`test_voice_cli.py::ChatVoiceTest.test_a_typed_slash_command_runs_but_a_transcript_that_reads_like_one_does_not`). There is no `/listen`: an empty line is the push-to-talk key. Since Phase 5a a typed bare `exit` or `quit` leaves, like `/quit`; a transcript that says "quit" is sent as text (`test_voice_cli.py::ChatVoiceTest.test_a_typed_exit_leaves_but_a_transcript_saying_quit_is_sent`).
* **Transcripts reach the terminal.** `steel voice listen` and `steel chat --voice` print the transcript, so it ends up in the terminal's scrollback.
* **Another program can record at the same time.** PipeWire allows several clients; the REC line shows only STEEL's own recording.
* **The executor stops waiting** after the start-key wait, the maximum length and 90 s for the model; the recording itself ends far earlier, so the microphone is closed before that can happen.
* **Spoken replies:** see docs/SPEAK.md (Phase 5a). They are never played while recording: the chat loop speaks a reply only after the recording has ended. Since Phase 5b, with `--speak`, the language of the confirmed transcript chooses which voice the engine loads early, after the microphone has closed and before the model is asked (docs/SPEAK.md section 6). The transcript itself never reaches the engine, and a discarded transcript starts nothing.
* **The transcript of a bystander** can be confirmed by mistake; nothing detects "this is not my voice".
* **The model directory is trusted.** A replaced `model.bin` would run as the owner. The SHA-256 values in section 3 are recorded so they can be compared by hand; STEEL does not check them.
* Open questions for the owner: whether `steel wipe` should also offer to delete the consent file, whether the allowed-language default should stay `en,es,it`, and whether to keep both models.

## 9. Evidence

Test files: `tests/test_voice_core.py` (C), `test_voice_stt.py` (S), `test_voice_tool.py` (T), `test_voice_cli.py` (L), `test_voice_check_script.py` (X); helpers in `voice_helpers.py`. Everything runs with fake audio, a fake model, fake keys (or a pseudo-terminal) and a temporary home: no microphone, speaker, speech model, network or real `~/.openjarvis` is used.

| Claim | Test |
| --- | --- |
| No consent file means no microphone (also if the file is malformed, a symlink or writable by others) | T `PermissionTest.test_a_grant_without_consent_still_opens_nothing`; C `ConsentTest.test_a_malformed_file_is_not_consent`, `test_a_symlink_is_not_consent`, `test_a_file_others_can_write_is_not_consent`; L `ListenCommandTest.test_no_consent_means_no_tool_is_even_built`, `ChatVoiceCommandTest.test_voice_without_consent_stops_before_loading_a_model` |
| Consent is a timestamp only, mode 600, deletable; revoking makes the microphone refuse again | C `ConsentTest.test_recording_consent_writes_only_a_timestamp_with_mode_600`, `test_an_existing_wide_open_file_becomes_private_again`, `test_revoking_deletes_the_file`; T `PermissionTest.test_revoking_consent_makes_the_microphone_refuse_again`; L `ConsentCommandTest.test_revoke_deletes_the_file_and_the_microphone_refuses_again` |
| Consent needs a typed `yes` at a terminal (not a pipe or script); consent creates no grant | L `ConsentCommandTest.test_typing_yes_records_consent_with_mode_600`, `test_anything_but_yes_records_nothing`, `test_consent_cannot_be_given_from_a_pipe_or_script`, `test_consent_does_not_create_a_grant` |
| The consent text in the code is the one in section 6.4 | C `ConsentTest.test_the_consent_text_is_the_one_in_the_design_document`, `test_the_consent_text_says_the_things_the_owner_must_know` |
| No grant: denied, the denial is logged, no sound is captured | T `PermissionTest.test_no_grant_means_denied_logged_and_no_sound_captured`; L `ListenCommandTest.test_no_grant_is_denied_and_the_denial_is_logged`, `test_a_malformed_grants_file_denies_and_logs` |
| A sensor grant must name this exact tool; wildcard sensor grants make the file invalid; another tool's name or another agent does not allow it | T `PermissionTest.test_a_wildcard_sensor_grant_makes_the_whole_file_invalid`, `test_a_grant_for_some_other_tool_name_does_not_allow_it`, `test_another_agent_is_not_allowed`; Phase 2a `tests/test_policy.py` `WildcardAndSensorTest` |
| The tool declares exactly one capability (`sensor:microphone`), takes no arguments, runs only through `build_executor` and is registered nowhere | T `PermissionTest.test_the_tool_declares_exactly_one_capability_and_no_arguments`, `test_unexpected_arguments_are_refused`, `test_the_tool_is_not_registered_with_openjarvis`, `ToolHygieneTest.test_the_tool_source_never_builds_an_executor`; `tests/test_executor.py` `NoOtherWayToRunAToolTest` and the test that only `executor.py` constructs a `ToolExecutor` |
| The REC line is on screen before the microphone opens; without a terminal, or if printing fails, it never opens; the source refuses to open without the proof, which cannot be made by hand | C `RecorderTest.test_the_rec_line_is_printed_before_the_source_is_opened`, `test_without_a_terminal_the_source_is_never_opened`, `test_a_failing_print_means_the_source_is_never_opened`, `IndicatorTest.test_no_terminal_means_no_proof`, `test_a_failing_write_means_no_proof`, `test_the_proof_cannot_be_made_by_hand`; T `RecordingFlowTest.test_the_rec_line_is_on_screen_before_the_microphone_opens`, `test_without_a_terminal_the_microphone_is_never_opened`, `ArecordSourceTest.test_opening_without_the_rec_proof_starts_nothing` |
| The vocabulary hint (`STEEL` by default, `STEEL_VOICE_HINT` for names, validated, always with `STEEL`) is passed to Whisper as its initial prompt and shown by `steel voice check` | C `ConfigTest.test_the_vocabulary_hint_defaults_to_steel_and_comes_from_a_setting`, `test_a_bad_vocabulary_hint_falls_back_to_steel`; S `LocalWhisperTest.test_the_vocabulary_hint_reaches_the_model_as_its_initial_prompt`, `test_without_a_hint_no_initial_prompt_is_passed`, `test_the_microphone_tool_gives_the_configured_hint_to_whisper`; L `VoiceCheckTest.test_check_shows_the_vocabulary_hint` |
| The microphone opens only after a start key, once per press; typing never opens it; keys typed earlier do not count | T `RecordingFlowTest.test_without_the_start_key_the_microphone_is_never_opened`; L `ChatVoiceTest.test_typing_never_opens_the_microphone`, `test_the_microphone_opens_once_per_key_press`; C `TerminalTest.test_keys_typed_earlier_do_not_start_a_recording`, `test_enter_starts_and_the_prompt_is_shown`, `test_no_key_means_no_start`, `test_without_a_terminal_there_is_no_start` |
| Recording stops at the maximum length (configurable, capped at 120 s, bad values fall back to 30 s) and when audio arrives too slowly | C `RecorderTest.test_recording_stops_at_the_maximum_length`, `test_slow_audio_is_cut_by_the_wall_clock`, `ConfigTest.test_the_maximum_length_is_configurable_but_capped`, `test_a_bad_number_falls_back_to_the_default`; T `RecordingFlowTest.test_recording_stops_at_the_configured_maximum_length` |
| The silence cut-off works (and does not cut mid-speech); giving up when nobody speaks; the stop key; cancelling | C `RecorderTest.test_the_silence_cut_off_ends_the_recording_after_speech`, `test_silence_does_not_cut_while_the_owner_is_still_talking`, `test_nobody_speaking_gives_up_after_the_startup_wait`, `test_the_stop_key_ends_the_recording`, `test_cancelling_ends_the_recording_and_closes_the_source`; T `RecordingFlowTest.test_the_silence_cut_off_ends_it_early`, `test_the_stop_key_ends_it` |
| onnxruntime telemetry is switched off before faster-whisper or its voice detection loads, and at the start of every `steel` command (even if the environment said otherwise) | S `OnnxTelemetryOffTest.test_telemetry_is_off_before_faster_whisper_or_its_vad_loads`, `test_every_steel_command_turns_it_off_at_start` |
| Audio is not written to disk: no new file in the home, no temporary file requested, no audio bytes in any file | T `AudioNotOnDiskTest.test_a_whole_capture_writes_no_audio_and_no_temporary_file`, `test_no_audio_bytes_appear_in_any_file_of_the_home`; S `NoDiskNoNetworkTest.test_transcribing_creates_no_file_and_opens_no_connection`, `SourceHygieneTest.test_nothing_writes_a_file_or_makes_a_temporary_one`. **There are no temporary files in this design, so there are no tests of temporary-file mode or deletion.** |
| The audio buffers are zeroed after transcription, when it fails, and when reading fails (best effort) | T `RecordingFlowTest.test_the_audio_buffer_is_wiped_after_transcription`, `test_the_audio_buffer_is_wiped_when_transcription_fails`; C `RecorderTest.test_audio_already_recorded_is_wiped_when_reading_fails_midway`; S `LocalWhisperTest.test_the_audio_array_is_zeroed_afterwards`, `test_the_audio_array_is_zeroed_even_if_the_model_fails` |
| The tool returns the transcript text, never audio; error messages never contain transcript text | T `RecordingFlowTest.test_the_result_is_the_transcript_and_never_audio`, `test_the_audio_buffer_is_wiped_when_transcription_fails` (checks the message) |
| The decision log never contains transcript text and holds only the five name fields | T `AuditLogTest.test_the_decision_log_never_holds_transcript_text`, `test_a_denial_is_logged_with_names_only` |
| Speech to text is local: model loaded from a directory with `local_files_only`; never downloads; no network; no cloud code, `openjarvis.speech`, `sounddevice` or `soundfile` is imported or called | S `RealLoaderIsOfflineTest.test_the_model_is_loaded_from_a_directory_with_local_files_only`, `LocalWhisperTest.test_a_missing_model_directory_is_reported_not_downloaded`, `NoDiskNoNetworkTest`, `SourceHygieneTest.test_nothing_imports_openjarvis_speech_or_a_cloud_backend`, `test_importing_the_voice_package_loads_no_audio_or_speech_library`; L `VoiceCheckTest.test_a_cloud_key_in_the_environment_is_noted_but_never_printed` |
| Silence skips the model (VAD gate); language is forced, restricted or detected as configured | S `LocalWhisperTest.test_no_speech_means_the_model_is_never_asked`, `test_a_single_language_is_forced_and_not_detected`, `test_several_languages_pick_the_likeliest_of_those`, `test_when_none_of_the_allowed_languages_is_ranked_the_first_is_used`, `test_no_language_setting_lets_the_model_detect_among_all` |
| `arecord` is run as a list (no shell), with no output file and a hard `-d` limit; closing terminates it; a device name cannot be an option | T `ArecordSourceTest.test_the_command_is_a_list_with_no_file_and_a_hard_time_limit`, `test_closing_terminates_the_process_and_is_idempotent`, `test_a_process_that_will_not_stop_is_killed`, `test_a_device_that_looks_like_an_option_is_refused`, `test_it_cannot_be_opened_twice`; C `ConfigTest.test_model_and_device_cannot_carry_paths_or_options` |
| The transcript is shown before it is used; Enter sends it through `ChatSession.ask` exactly like typed text and through the normal fact extraction | L `ChatVoiceTest.test_the_transcript_is_shown_before_it_is_used_and_enter_sends_it`, `test_a_confirmed_transcript_is_passed_to_ask_exactly_like_typed_text`, `test_a_confirmed_transcript_goes_through_the_normal_fact_extraction` |
| A discarded transcript is not sent, not published, not remembered and never reaches the fact extractor; nothing heard sends nothing | L `ChatVoiceTest.test_discarding_sends_nothing_and_remembers_nothing`, `test_a_discarded_transcript_never_reaches_the_fact_extractor`, `test_the_transcript_is_not_published_to_the_event_bus_when_discarded`, `test_nothing_heard_sends_nothing` |
| `steel voice check` opens no microphone, starts no process, writes nothing, changes no permission, and reports devices, backend, model, consent and grant | L `VoiceCheckTest.test_check_opens_no_microphone_and_starts_no_process`, `test_check_with_the_fake_source_never_opens_it`, `test_check_writes_nothing_not_even_a_decision_log`, `test_check_changes_no_permissions_and_creates_nothing`, `test_everything_in_place_is_ready`, `test_missing_consent_is_reported`, `test_a_missing_grant_is_reported_with_the_example_to_write_by_hand`, `test_an_invalid_grants_file_is_reported_as_everything_denied`, `test_a_missing_model_and_a_missing_capture_program_are_reported`; `CaptureDeviceListTest` |
| Ctrl-C stops a recording and closes the microphone, even if the device stopped delivering audio | L `ListenCommandTest.test_ctrl_c_during_a_recording_stops_and_closes_the_microphone`; T `RecordingFlowTest.test_cancelling_from_another_thread_stops_and_closes_the_microphone`, `test_cancel_ends_a_recording_whose_device_stopped_delivering_audio`, `test_a_cancel_from_an_earlier_call_does_not_cancel_the_next_one` |
| Two recordings cannot run at once | T `RecordingFlowTest.test_two_recordings_cannot_run_at_once` |
| `chat --voice`: leaving still waits at most 30 s for fact extraction; a denied microphone does not stop the chat | L `ChatVoiceTest.test_leaving_still_waits_for_fact_extraction_at_most_30_seconds`, `test_a_denied_microphone_is_reported_and_the_chat_goes_on`; `tests/test_close_wait.py` |
| In voice chat a reply is spoken only with `--speak`, only the reply, and only after the microphone has closed (the rest of speaking: docs/SPEAK.md section 10) | L `ChatVoiceTest.test_replies_are_spoken_only_with_speak_after_the_recording` |
| `scripts/voice_check.py` uses a scratch directory, writes consent and the grant only there, never into the real home, and deletes it, also on failure and Ctrl-C (**run with fakes; the live run is the owner's**) | X `ScriptTest.test_it_records_transcribes_prints_times_and_deletes_the_scratch`, `test_it_never_writes_a_consent_or_grants_file_into_the_real_home`, `test_the_scratch_holds_only_consent_grants_and_the_decision_log`, `test_a_failed_run_still_deletes_the_scratch`, `test_ctrl_c_still_deletes_the_scratch` |

**Mutation checks (a small, hand-made sample).** 69 single-line changes to the voice code were made by hand, one at a time (REC line skipped, proof not checked, limits removed, close not called, consent checks removed, wipe removed, VAD removed, flags like `shell=True`, discarding that still sends, and so on), and the suite was run after each. 64 were caught at once; five survived (audio not wiped when reading fails, consent file not forced to mode 600, a stale cancel flag, a message that could leak an exception's text, `voice check` calling `harden()`); each now has its own test and is caught. A first run was invalid and was thrown away: a background process inherits SIGINT as ignored, which made the Ctrl-C test fail for every mutation; the test now installs the handler it needs. Four more mutations covered the cancel/close code added afterwards; one was only caught after another test was added. Mutation checks do not prove the absence of other bugs.

**What no test proves.**

* Anything with the **real microphone**: that `arecord -D default` records from PipeWire here, that `-d` and closing the pipe end it, that the level threshold suits the microphone, that the transcript is right. `scripts/voice_check.py` exists for that and has not been run. (The `arecord` command line itself was exercised once against the ALSA `null` device, which uses no hardware: 2 s gave 64,000 bytes.)
* **Accuracy and speed of the real model** on the owner's voice and languages (section 4).
* The terminal behaviour in the owner's actual terminal emulator.
* That nothing else in the process could open the microphone: the proof object and the consent check stop mistakes, not code that ignores them.
