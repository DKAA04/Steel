# Voice (Phase 2c): push-to-talk, fully local

**Status of this document: design.** It was written and committed *before* any audio code (see the git history). Every "will" below is a requirement the code is built and tested against. Section 9 is updated when code and tests exist and says, per claim, which test proves it. Until a claim has a named test there, it is not implemented.

Scope: push-to-talk speech input, and optional spoken replies. **No wake word and no always-on listening.** Nothing leaves the computer.

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

`sounddevice` and `soundfile` arrive with the `[speech]` extra but **STEEL does not use them**: capture is `arecord` (section 6.1) and the optional spoken reply is `espeak-ng`. They can be uninstalled; nothing in STEEL imports them.

Models (the one network download, done by hand with `huggingface_hub.snapshot_download`, telemetry off, pinned to a revision; the only files fetched are `config.json`, `model.bin`, `tokenizer.json`, `vocabulary.txt`). They live in `~/.openjarvis/steel_models/` (605 MB):

| Directory | Repository and revision | Size | `model.bin` SHA-256 |
| --- | --- | --- | --- |
| `faster-whisper-small` | `Systran/faster-whisper-small` @ `536b066274…` | 464 MB | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |
| `faster-whisper-base` | `Systran/faster-whisper-base` @ `ebe41f70d5…` | 142 MB | `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9` |

A `tiny` model was fetched for the measurements only and deleted. Not installed: `kokoro`, PyTorch, `piper`, `espeak-ng`.

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
* **Silence costs real time and can invent text.** 5 s of silence or low noise took 0.43 s (`tiny`), 1.0 s (`base`) and 3.0 s (`small`) even with the library's VAD filter on. STEEL runs the bundled Silero VAD itself first (12.8 ms for a sentence, 17 ms for silence or noise, measured) and skips the model when it finds no speech. Neither silence nor noise produced text in these runs; a real room may.
* Documented figure (`faster-whisper` README, shipped in the installed package metadata): `small`, `int8`, beam 5, 13 minutes of audio, 1 min 42 s on an Intel Core i7-12700K with 8 threads. That is long-audio throughput and says little about a 4-second sentence, where the fixed 30-second window dominates; the table above is the number to plan with.

**What the accuracy evidence is, honestly.** Three English sentences from the `flite` synthetic voice (no recording of a person, no microphone): all three models transcribed the short sentence correctly; for "my sister is called Mara", `tiny` produced "Marlenter Burtier's", `base` "Mare" and `small` "Mara"; all three garbled the longest sentence (the `kal` voice is very robotic). That is **one synthetic voice and three sentences**: an anecdote, not a measurement. **No Spanish or Italian speech could be generated here, so accuracy in those languages has not been measured at all.** The expectation that larger Whisper models make fewer mistakes in non-English languages comes from the Whisper paper and model cards, which were not re-checked offline. `scripts/voice_check.py` (step 5) records 5 seconds from the real microphone and can transcribe with both installed models, so the owner can compare them on a real voice before trusting `small`.

**Why `small` over `base`:** a wrong transcript costs more than a few seconds of waiting, because the owner has to notice and discard it, and the multilingual claim is the requirement. **Why not `medium` (1.5 GB) or `large-v3-turbo` (1.6 GB):** not measured; they are slower than `small` by a large factor on this CPU, and the 30-second window already makes `small` take 3–7 s.

**Offline guarantee, measured.** With `socket.connect`, `connect_ex` and `getaddrinfo` made to raise, loading the model from its directory, running the VAD, detecting the language and transcribing all succeeded with **zero network attempts**, and importing `faster_whisper` loaded no `openjarvis`, `openai`, `deepgram` or analytics module. The wrapper loads the model only from a directory that exists, with `local_files_only=True`, so it cannot download.

## 5. Text to speech: the choice

**Nothing local and good is installed, so spoken replies are an optional later step.** `steel chat --voice --speak` will use `espeak-ng` **if it is on the PATH** and otherwise print "no local text-to-speech installed" and carry on silently. `espeak-ng` is not installed here, so that code path can only be tested with a fake runner (stated again in section 9). The reply text is passed on standard input, not on the command line, so it does not appear in `ps`.

Rejected for now: Kokoro (the local OpenJarvis choice) needs PyTorch, several GB, and fetches model files itself; Piper gives better voices than `espeak-ng` but is not installed and needs voice files per language. Both can replace `espeak-ng` later. `openai_tts` and `cartesia` are cloud services and are never used.

Speech output is **not** behind a permission capability: the vocabulary has none for a speaker, and playing a reply reads nothing from the room. It does let anyone nearby hear the reply, which is the owner's choice by passing `--speak`. Whether it needs a capability is an open question (section 8).

## 6. Consent and privacy design

### 6.1 How the microphone is read

* **One mechanism: `arecord` writing raw audio to a pipe.** `arecord -q -D default -f S16_LE -r 16000 -c 1 -t raw -d <max+1>` with no output file. The command line was checked against the ALSA `null` device (no hardware): 2 seconds gave exactly 64,000 bytes on standard output. `-D default` is PipeWire here, so the desktop's own microphone indicator and `wpctl status` also show the stream, which is an independent way for the owner to see it.
* **Why `arecord` and not `sounddevice`:** importing `sounddevice` initialises PortAudio (section 1), a second library would hold the device, and `arecord` is a process the owner can see in `ps` for exactly as long as the microphone is open. `-d` is a second, independent stop: if STEEL died, `arecord` would still end by itself after the maximum length, and it exits at once when its pipe closes.
* **The real source is its own small module** (`steel/voice/mic.py`) behind an `AudioSource` interface. Everything else is tested with a fake source; the real one is tested only with a fake process (section 9), because the tests must never open a real microphone.

### 6.2 Push-to-talk

* The microphone opens **only after an explicit key press in the terminal**, once per recording, never in the background and never at start-up. A terminal reports a key *press* but not a key *release*, so "hold to talk" is not possible; the design is **toggle**: Enter starts, Enter stops. Typed-ahead keys are flushed first, so a stray Enter typed earlier does not start a recording.
* It stops on whichever comes first: Enter, the **maximum length** (30 s by default; `STEEL_VOICE_MAX_SECONDS`, capped at 120, a value that is not a number falls back to 30), the **silence cut-off** (1.5 s of quiet after speech; `STEEL_VOICE_SILENCE_SECONDS`), a give-up if nobody speaks for 5 s, or Ctrl-C.
* The silence test is a fixed energy threshold (RMS 500 of 32,768, as in OpenJarvis' `voice_io`). The owner's microphone gain is low (PipeWire source volume 0.27), so the threshold may need tuning; `scripts/voice_check.py` prints the measured level for that.

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

* Audio exists only as `bytes` in RAM: the pipe from `arecord`, a `bytearray` in the recorder, and a NumPy array inside the transcriber. **No audio file is written** by STEEL or, with an array input, by `faster-whisper`. The recorder overwrites its `bytearray` with zeros and the transcriber zeroes its float array in a `finally`; this is best effort, because Python may keep copies, and swap can write RAM to disk.
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

Order of the checks in `steel voice listen` and `steel chat --voice`: consent file, then (inside the executor) the policy decision, which is logged, then terminal and start key, then the indicator, then the microphone. A denial happens before any sound exists and is logged with the agent, `sensor:microphone`, `deny` and a reason code. A grant with no consent, or consent with no grant, opens nothing.

### 6.7 The transcript

* It is shown to the owner **before** it is used: `You said: "…"` and then Enter to send or Esc to discard.
* Confirmed text goes through the **same path as typed text** (`ChatSession.ask`): the OpenJarvis guardrails, then the user-only fact extraction, then the same memory. Discarded text is dropped: it is not sent to the model, not published to the event bus, and never reaches the fact extractor.
* The transcript is never stored by the voice code itself.

### 6.8 Other people

**Anyone who speaks near the microphone while it is open is recorded** (in RAM, and transcribed). The microphone does not know whose voice it hears, and STEEL does no speaker identification. What limits this is the push-to-talk design: the microphone is open only for short, deliberate, visible recordings, never in the background. It is not a privacy guarantee for third parties; the owner must not record people who do not know. Whatever a bystander said can end up in the transcript, and, if the owner confirms it, in the model prompt and in the fact store.

## 7. Wake word: NOT IMPLEMENTED

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
* **Terminal limits.** There is no key-release event; the Esc key is told apart from arrow keys by a 50 ms wait, which can mis-read a very slow key sequence.
* **Spoken replies:** `espeak-ng` is not installed, so that path has only been tested with a fake. Nothing says whether a speaker needs a capability.
* **The transcript of a bystander** can be confirmed by mistake; nothing detects "this is not my voice".
* **The model directory is trusted.** A replaced `model.bin` would run as the owner. The SHA-256 values in section 3 are recorded so they can be compared by hand; STEEL does not check them.
* Open questions for the owner: whether speaking needs a capability (a `sensor:speaker` or `audio:play` name), whether `steel wipe` should also offer to delete the consent file, whether the allowed-language default should stay `en,es,it`, and whether to keep both models.

## 9. Evidence

*(Filled in after the implementation, with a named test per claim. Anything not listed here is not claimed.)*
