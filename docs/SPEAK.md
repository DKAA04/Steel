# Spoken replies (Phase 5a)

**Status: design, written and committed before any speaking code.** Section 10 will list, per claim, the test that proves it once the code exists; a claim without a named test there is not claimed. Everything here was checked on the owner's machine on 2026-10-25 without playing a sound: no player was started and no playback device was opened by the assistant (section 1 says what that rests on).

Goal: STEEL can read its replies aloud with a local text-to-speech engine, opt-in per session, with the same discipline as the microphone (docs/VOICE.md) and the camera (docs/VISION.md): designed first, behind the permission policy, nothing on disk, nothing over the network at run time.

## 1. What this machine has (evidence)

| Item | Found |
| --- | --- |
| Sound cards (`aplay -l`, which opens only the control device, not a playback device) | card 0 `HD-Audio Generic`: HDMI 0, 1, 2 (devices 3, 7, 8); card 1 `HD-Audio Generic`: `ALC257 Analog` (device 0), the built-in speakers and headphone jack |
| Sound server | PipeWire 1.6.9 with `pipewire-pulse` and `pipewire-alsa` (Debian packages `1.6.9-2~bpo13+1`), `wireplumber`. PipeWire itself holds `/dev/snd/pcmC1D0p` open (`fuser`), which is why that node's timestamps change when any desktop program plays a sound |
| Player programs | `aplay` (alsa-utils 1.2.14), `paplay` and `pacat` (pulseaudio-utils 17.0), `pw-play` / `pw-cat` (PipeWire 1.6.9), `ffplay`, `sox`/`play`, `mpv` |
| Text to speech | none: no `espeak-ng`, `espeak`, `piper`, no Python TTS package in `~/.openjarvis/.venv` (it has `onnxruntime` 1.31.0, `numpy` 2.5.4, `faster-whisper` 1.2.1, `sounddevice` 0.5.6, `soundfile` 0.14.0) |
| System libraries | `libonnxruntime1.21` (Debian) is installed but not used |
| CPU | AMD Ryzen 5 5500U, 12 threads; no GPU is used |
| Network isolation without root | `unshare --net --map-root-user` works for the owner's user: inside it the only interface is a loopback that is down |

**Player choice: `aplay`.** It reads raw 16-bit PCM from standard input (`aplay -q -t raw -f S16_LE -r RATE -c 1 -`), needs no file, is already required for the microphone (`arecord` is the same package), and on this machine its default device is PipeWire (through `pipewire-alsa`), so it plays wherever the desktop's output is set. `pw-play` could read stdin too but is PipeWire-only. No player that needs a file is used.

What "no sound was played" rests on, for the assistant's work: the commands run (listed in the final report), the absence of any player process, and no new opener of `/dev/snd/pcm*p` besides PipeWire. It does not rest on device timestamps, which PipeWire changes by itself.

## 2. Engine: Piper, compared

| | Piper (`piper-tts`) | Kokoro (`kokoro-onnx`) | `espeak-ng` alone |
| --- | --- | --- | --- |
| Version checked | 1.8.0 (PyPI, 2026-09-04) | 0.6.1 (PyPI, 2026-08-19) | not installed |
| Code licence | **GPL-3.0-or-later** (PyPI metadata `License: GPL-3.0-or-later`, file `COPYING`; the project moved from `rhasspy/piper` (MIT) to `OHF-Voice/piper1-gpl`) | MIT; weights Apache-2.0 | GPL-3.0-or-later |
| Phonemes from | **espeak-ng, embedded** (`piper/espeakbridge.so` plus 20 MB `espeak-ng-data` inside the wheel) | `misaki`, which for Spanish and Italian falls back to espeak-ng through `phonemizer` 3.4.0 (GPLv3+) and `espeakng-loader` 0.2.4 (bundles espeak-ng; no licence in its metadata) | itself |
| Download | wheel 34,131,442 bytes (`25b4d3f3…a7d3`) | wheel 26 KB + espeakng-loader 10 MB + model about 300 MB (about 80 MB quantized) | system package (`apt`), not allowed here |
| Installed size | **197 MB** for its own venv (measured: onnxruntime 67 MB, numpy 69 MB, piper 46 MB, pip 12 MB, rest 3 MB) | not measured | n/a |
| RAM while speaking | **190 MiB** (16 kHz voices) to **295 MiB** (22 kHz `medium` voice), peak resident size of the engine process, measured | not measured; an 82M-parameter model, several times Piper's | small |
| Speed on this CPU | **real-time factor 0.06 to 0.07** for synthesis (0.5 s for 7.5 s of speech), but **3.5 s to load a voice**, so about **4 s from start to first sound** when one process is started per reply (measured, section 2.1) | not measured | fast, robotic |
| Spanish and Italian | yes, several voices each | `em_alex`, `em_santa` (no grade), `im_nicola` (grade C); British male `bm_george`/`bm_fable` grade C, `bm_lewis` D+, `bm_daniel` D (its VOICES.md) | yes |

**Recommendation: Piper, run as a separate program.** There is no permissively licensed engine that is as small and does Spanish and Italian without espeak-ng: Kokoro's own code and weights are permissive, but its Spanish and Italian go through espeak-ng and `phonemizer`, both GPL, and it is larger and slower. Since the GPL boundary is needed either way, the smaller, faster and better-documented engine wins. `espeak-ng` alone would need a system package, which this phase may not install, and sounds robotic.

### 2.1 Measurements (synthesis to memory, nothing played)

A scratch script started the engine exactly as STEEL will (section 4), wrote one sentence to its standard input, read the raw audio from its standard output into memory, measured it and overwrote it with zeros. No player was started. Three runs per voice:

| Voice | Sentence | Audio | Wall time per run (incl. start and 3.5 s load) | First audio after | Real-time factor, whole run | Synthesis only, inside the engine |
| --- | --- | --- | --- | --- | --- | --- |
| `en_US-john-medium` | "Good evening. The kettle has boiled, your next meeting is at four, and the forecast says light rain after six." | 7.6-7.9 s | 4.75-4.97 s | 3.96-4.08 s | 0.60-0.63 | 0.46-0.52 s, **RTF 0.06-0.07** |
| `es_ES-carlfm-x_low` | "Buenas tardes. La reunión es a las cuatro y mañana lloverá un poco por la tarde, así que lleva paraguas." | 6.0 s | 4.83-5.06 s | 4.36-4.49 s | 0.80-0.84 | not separated |
| `it_IT-riccardo-x_low` | "Buonasera. La riunione è alle quattro e domani pioverà un po' nel pomeriggio, quindi porta l'ombrello." | 5.4-5.9 s | 4.53-4.68 s | 4.15-4.36 s | 0.78-0.85 | not separated |

Load time: `import onnxruntime` 0.15 s, `import piper` 0.02 s, `PiperVoice.load` 3.5 s (the onnxruntime session; with graph optimisation off it is slower, 4.9 s). One sentence per voice and three runs is a small sample; the machine was under memory pressure (swap 60 % used) at the time.

**Consequence:** every spoken reply waits about 4 s before the first sound. Keeping one engine process alive per session would remove the load time, but Piper's command line gives no end-of-reply marker on its raw output, so that would need a STEEL-written program running inside the GPL engine (a GPL work of its own). Not done in Phase 5a; an open question.

### 2.2 onnxruntime telemetry (found while measuring)

`onnxruntime` 1.31.0 on Linux contains Microsoft "1DS" telemetry: its library holds `https://mobile.events.data.microsoft.com/OneCollector/1.0`, and on first use it writes a device id and an event queue to `~/.cache/Microsoft/DeveloperTools/.onnxruntime/` (`deviceid`, `onnxruntime.db`). The queued events read on this machine carry a device id, the interpreter path (which contains the user name), the OS name, CPU model and connection type. Setting `ORT_DISABLE_TELEMETRY=1` before it starts stops this: a run with a scratch home wrote no telemetry files with it, and wrote both without it. The same library is used by faster-whisper, so voice input was affected too; that fix is already committed and described in docs/VOICE.md section 3. Two of the queued events were caused by the assistant's own probes on 2026-10-25 (10:22 and 10:23 UTC), run before the switch was known; the queue file is left for the owner to delete.

For Piper the engine gets `ORT_DISABLE_TELEMETRY=1` **and** runs without any network (section 4), so even a future library that ignored the switch could not send anything.

## 3. Voices

Voices come from the Hugging Face repository `rhasspy/piper-voices`, pinned to revision **`5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`** (2026-10-20). The repository is labelled MIT, but **a voice's licence is the licence of the recordings it was trained on**, which each voice's `MODEL_CARD` names, and many voices were fine-tuned from the US English "lessac" voice. The lessac data (Blizzard 2013) is licensed "for Research Purposes only", which "excludes … using the Materials for any commercial purpose, including the development … of voice synthesis … products" (its licence page). Whether a voice fine-tuned from a model trained on that data carries the restriction is a legal question this document cannot settle, so the defaults avoid that lineage.

**Defaults** (downloaded by the setup script):

| Voice | Language | Speaker | Quality, rate | `.onnx` size | `.onnx` SHA-256 | Recordings and licence (model card) | Lineage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `en_US-john-medium` | English (US) | male | medium, 22,050 Hz | 63,531,379 | `789c6c875726e627ddee93d51d8727859abe9c091c3d141591f4b83c2072e988` | LibriVox, **public domain** | fine-tuned from `kristin` (LibriVox, public domain, trained from scratch) |
| `es_ES-carlfm-x_low` | Spanish (Spain) | male | x_low, 16,000 Hz | 28,130,791 | `d69677323a907cd4963f42b29c20a98b5d6bfa7f3e64df339915e4650c00d125` | carlfm01/my-speech-datasets, **public domain** | trained from scratch |
| `it_IT-riccardo-x_low` | Italian | male | x_low, 16,000 Hz | 28,130,791 | `1368de15f123275a7ef951c9e5e30be0f58a032daa14a0da44037443c1d1d21b` | M-AILABS speech dataset (LibriVox recordings, which are public domain), **custom M-AILABS licence**: "Copyright (c) 2017-2019 by the original creators @ M-AILABS"; the licence text itself could not be retrieved on 2026-10-25 (its site did not resolve) | trained from scratch |

Each comes with its `.onnx.json` (Piper's configuration) and `MODEL_CARD`, pinned too:

| File | Size | SHA-256 |
| --- | --- | --- |
| `en_US-john-medium.onnx.json` | 4,965 | `af60f177b6b550f3d7a302720c0fb89e7f94a82b5dca464775ef63b1c69ba09a` |
| `en_US-john-medium` `MODEL_CARD` | 498 | `a056da65cd70dcbda96fc3420fe8d3442dbf3c8b75f49cdf800a715b42e299d1` |
| `es_ES-carlfm-x_low.onnx.json` | 4,159 | `d9bdfa9ff01eb2bc9e62e7d2593939d1e4c4d8eb7cf75f972731539d12399966` |
| `es_ES-carlfm-x_low` `MODEL_CARD` | 250 | `a6e62a3d36c37c7702d877f784001208b952e2bf4d17c72a6b5da442fbefb4b3` |
| `it_IT-riccardo-x_low.onnx.json` | 4,161 | `146ab9c634afe524e9fb7530f2510df7a42fb1db56b52658ca1fb3d98001a62a` |
| `it_IT-riccardo-x_low` `MODEL_CARD` | 260 | `7ce3b2fc4f454c84936887971574e436cc2154049b427a530f70bee050b2fdc6` |

**The British idea did not survive the licence check.** The British male voices are `en_GB-alan` (its recordings: "Copyright 2022 Mycroft AI, All Rights Reserved", so rejected), `en_GB-northern_english_male-medium` (CC BY-SA 4.0 recordings, fine-tuned from lessac), and the multi-speaker `vctk` and `semaine` (lessac lineage; `semaine` is also CC BY-NC-SA). `john` is a calm, neutral US male voice with the cleanest licence. The two `x_low` voices are lower quality than `medium` ones; nobody has listened to any of them on this machine yet.

**Alternatives**, pinned in the same manifest and downloaded only on request (`--voice NAME`):

| Voice | Why it might be preferred | Why it is not the default |
| --- | --- | --- |
| `en_GB-northern_english_male-medium` (63,201,294 bytes, `57a219ae…0689`; json `69557ed3…7d47`; card `75661c3a…48bb`) | British male | lessac lineage; recordings CC BY-SA 4.0 (attribution, share-alike) |
| `es_ES-davefx-medium` (63,201,294 bytes, `6658b03b…a917`; json `0e0dda87…4842`; card `420703b5…f95`) | Spanish male, medium quality | lessac lineage (recordings CC0) |
| `it_IT-serena-medium` (63,516,051 bytes, `fe4e26b2…6eb5`; json `14cb4995…7881`; card `1d0dbdc0…bbe7e`) | Italian, medium quality, trained from scratch | female; trained on synthetic speech made with Qwen3-TTS from an unnamed reference voice (dataset CC BY 4.0) |

Full checksums are in `steel/speak/voices.py`, which is the single source of truth.

**Where files go.** Voices: `~/.openjarvis/steel_models/piper-voices/<voice>/` (directory mode 700, files 600), next to the Whisper models, fetched **once**, by the setup script, from the pinned revision, each file checked against its SHA-256 before it is kept. The engine: its own virtual environment at `~/.local/share/steel/piper` (override: `STEEL_PIPER_DIR`, an absolute path), outside `~/.openjarvis` so the OpenJarvis home holds only data. It is installed by the same script from `scripts/piper-requirements.txt`, a lock file with a SHA-256 for every wheel (`pip install --require-hashes --only-binary :all: --no-deps`): `piper-tts` 1.8.0, `onnxruntime` 1.31.0, `numpy` 2.5.4, `protobuf` 7.36.2, `flatbuffers` 25.12.19, `pathvalidate` 3.3.1, `packaging` 26.3. Nothing is fetched at run time.

## 4. The GPL boundary

STEEL's code never imports Piper, onnxruntime-for-Piper or espeak-ng, and nothing of Piper is copied into this repository. STEEL starts Piper as a separate program, from its separate environment:

```
unshare --net --map-root-user -- <engine>/bin/python -I -m piper -m <voice>.onnx --output-raw
```

* **Text goes in on standard input**, one line, then the pipe is closed. It is never on the command line (so `ps` does not show it) and never in the environment.
* **Audio comes out on standard output** as raw 16-bit mono PCM (`--output-raw` writes only to stdout; Piper's file and `ffplay` modes are not used).
* Standard error is discarded, the working directory is `/`, and the environment holds only `LANG`/`LC_ALL` (`C.UTF-8`) and `ORT_DISABLE_TELEMETRY=1`: no proxies, no keys, no `HOME`. `-I` makes Python ignore `PYTHON*` variables, the user's site directory and the current directory.

Two programs that talk only through pipes and a command line, each run as its own process, are what the GPL FAQ calls communicating "at arms length" ("mere aggregation"), not one combined program; STEEL's own code is then not a work based on Piper. STEEL also does not distribute Piper or the voices: the setup script downloads them onto the owner's machine. This is the design's reading of the FSF FAQ, **not legal advice**. STEEL currently has no licence file of its own (an open question, section 9).

**No network at run time, enforced.** `unshare --net` puts the engine in a new network namespace with only a loopback interface that is down, so it cannot reach this machine's network or the internet, whatever its libraries try. If `unshare` is missing or fails, STEEL does **not** start the engine (it says why and stays silent). The 2.1 measurements were taken this way, so the engine needs no network to speak. STEEL's own process opens no socket while speaking; a test blocks `socket.connect` and `getaddrinfo` as for Whisper.

## 5. Language of each reply

The reply's language is guessed with a small local heuristic: count common function words of English, Spanish and Italian (`the`, `and`, `is` …; `el`, `los`, `que`, `está` …; `il`, `gli`, `che`, `è` …), plus marks only Spanish uses (`¿`, `¡`, `ñ`). Spanish or Italian is chosen only with a clear lead; anything else, short or mixed text, or any other language, is spoken as **English**. No language-detection package is added. If the chosen language has no installed voice, the reply is not spoken and STEEL says which voice is missing. The transcript language from Whisper is not used: the reply may be in another language than the question.

## 6. How speaking works

**Opt-in per session:** `steel chat --speak` and `steel chat --voice --speak`. Without `--speak` nothing is ever spoken. With it, after each reply is printed, it is spoken, and STEEL prints once at the start:

> Replies are read aloud through your speakers; anyone nearby can hear them. Enter, a typed line or Ctrl-C stops a reply.

Speaking records nothing, so it needs no consent file, but that line is the reminder.

**Visible while it plays.** The speaker may be opened only by `steel.speak.playback.play`, which first prints the indicator line on the terminal and passes the proof to the player (as the REC and CAMERA OPEN lines do). Without a terminal there is no proof and nothing plays.

```
▶ SPEAKING 00:03  Enter or a typed line stops; Ctrl-C stops
■ finished speaking (4.2 s)        or        ■ stopped speaking (1.3 s)
```

**Stopping:** Enter on its own stops playback immediately; so does a typed line (Enter ends it), and that line is then used as the next message, so typing over STEEL works; Ctrl-C stops it too and the chat goes on. Stopping kills the player and the engine. A reply also stops at a hard limit of 120 s of audio.

**Policy.** A new capability, `audio:play` ("play sound on the speakers"): not a sensor, never covered by a wildcard, and a grant must name the exact tool (a grant with pattern `*` makes the grants file invalid, as for sensors). The speaking function is a tool, `speak`, declaring exactly `audio:play`, built only by `get_speak_tool` and run only through `build_executor` as the agent **`voice`**. The owner writes the grant by hand:

```json
{"agent_id": "voice", "grants": [
  {"capability": "sensor:microphone", "pattern": "microphone_listen"},
  {"capability": "audio:play", "pattern": "speak"}
]}
```

No grant: the first reply is not spoken, STEEL prints that `speak` is not granted to agent `voice` (and how to grant it), the denial is logged, and the session stays silent without asking again. The decision log holds names only, never the text.

**What is spoken, and what never is.** A spoken reply can be overheard, so:

1. Only the assistant's own reply is passed to `speak`. Never a tool-data block, a room note, a transcript or a stored text. The tool also refuses any text that contains the tool-data markers (`[Tool result from`, `[End of tool result]`) or the camera line (`[Camera description`).
2. A reply the injection scanner flags (or that it cannot check) is not spoken.
3. The text is redacted with the same scanners as tool results (secrets and personal data, `steel.tooldata`); a redaction mark is spoken as "redacted". If redaction fails, nothing is spoken.
4. Code blocks are dropped (spoken as "there is code on screen"), URLs and e-mail-like links become "a link on screen", markdown marks (`#`, `*`, `_`, list bullets, table bars, link brackets) are removed, control characters are stripped.
5. At most **600 characters** are spoken (`STEEL_SPEAK_MAX_CHARS`, 50 to 4000; any other value falls back to 600), cut at a sentence or word boundary, followed by "The rest is on screen." (in Spanish and Italian in those languages).

**Nothing on disk.** The text goes from STEEL to the engine through a pipe; the audio goes from the engine through STEEL to `aplay` through pipes, in chunks of 4 KB held in one `bytearray` that is overwritten with zeros after each write and at the end (best effort: the kernel's pipe buffers and the player's own buffers are not in STEEL's reach). No WAV or temporary file is written by STEEL, and neither program is given an output file.

**`steel speak check`** (read only: plays nothing, opens no audio device, starts no program): engine found and its version, whether network isolation is available, the voices installed with size and SHA-256 status, the player found, whether the grant is present (asked without logging), the character limit.

**`steel speak say "TEXT"`** speaks one short sentence (at most 200 characters) through the executor, for testing. Unlike chat, that text is on STEEL's own command line, which is the owner's choice.

**`scripts/speak_check.py`** (the owner's live check, never run by an assistant): with a scratch grant, plays one sentence in each installed language through the real speakers and prints synthesis time and real-time factor.

**`scripts/speak_setup.py`**: installs the engine and downloads the pinned voices (section 3). Run once; running it again checks and skips what is already there.

## 7. No echo handling, and what hands-free would need

The microphone is push-to-talk (docs/VOICE.md): it is open only while the owner holds the turn, and STEEL does not speak during a recording or record while speaking, since both run in the same loop one after the other. So the microphone never hears STEEL's own voice and no echo cancellation is needed. **A future hands-free mode would need acoustic echo cancellation** (or at least muting the microphone while speaking), or STEEL would transcribe and answer itself.

## 8. Limits

* **Anyone nearby hears the reply**, including names, appointments or anything else the reply contains. Redaction only catches patterns; a password in plain words is spoken. The model may repeat in its own words what was in a tool result the owner added; that is spoken, because only the explicit markers are refused.
* The output device is PipeWire's default, which may be HDMI (a TV in another room) rather than the laptop. STEEL does not choose it.
* The language guess is a heuristic and will be wrong on short or mixed replies (then English).
* About 4 s before each reply is heard (section 2.1).
* The `x_low` voices are low quality, and nobody has listened to them yet.
* The indicator and key handling need a terminal; nothing is spoken in a pipe or script.
* The network namespace needs unprivileged user namespaces; where they are disabled, nothing is spoken.
* Not a sandbox: the engine can still read the owner's files (it runs as the owner, mapped to root inside its own namespace only).

## 9. Open questions for the owner

1. Keep `en_US-john-medium` (US, public domain) or take the British `en_GB-northern_english_male-medium` despite its lessac lineage?
2. Spanish and Italian: the clean-licence `x_low` voices, or the better-sounding `es_ES-davefx-medium` / `it_IT-serena-medium` (female)?
3. Should STEEL get a licence file, and which? Section 4 assumes STEEL is not itself GPL.
4. Is a 4 s delay acceptable, or should a long-lived engine helper (a GPL program of its own) be written?
5. Delete the onnxruntime telemetry queue in `~/.cache/Microsoft/DeveloperTools/.onnxruntime/`?

## 10. Evidence

To be filled in with the tests once the code exists.
