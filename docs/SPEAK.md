# Spoken replies (Phase 5a, faster start in Phase 5b)

**Status: implemented (Phase 5a), except where marked. Phase 5b (docs/LATENCY.md) starts the engine before the reply exists (section 6, "Started early").** This document was written and committed *before* any speaking code (see the git history), and the code was then built against it; section 10 lists, per claim, the test that proves it, and what no test can prove. A claim without a named test there is not claimed. **The owner reported on 2026-10-25 that spoken replies work on their machine (their live check, `scripts/speak_check.py`, and live chats); the assistant has never played a sound.** Everything here was checked on the owner's machine on 2026-10-25 without playing a sound: no player was started and no playback device was opened by the assistant (section 1 says what that rests on).

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
| `it_IT-riccardo-x_low` | Italian | male | x_low, 16,000 Hz | 28,130,791 | `1368de15f123275a7ef951c9e5e30be0f58a032daa14a0da44037443c1d1d21b` | M-AILABS speech dataset (LibriVox recordings, which are public domain), **custom M-AILABS licence** (BSD-style: use, including commercial use, is allowed; redistributions of the data must keep the copyright notice, conditions and disclaimer). The dataset's own page did not resolve; the full text, taken on 2026-10-25 from the dataset author's repository `github.com/i-celeste-aurora/m-ailabs-dataset`, is quoted word for word in [NOTICE](../NOTICE) | trained from scratch |

Each comes with its `.onnx.json` (Piper's configuration) and `MODEL_CARD`, pinned too:

| File | Size | SHA-256 |
| --- | --- | --- |
| `en_US-john-medium.onnx.json` | 4,965 | `af60f177b6b550f3d7a302720c0fb89e7f94a82b5dca464775ef63b1c69ba09a` |
| `en_US-john-medium` `MODEL_CARD` | 498 | `a056da65cd70dcbda96fc3420fe8d3442dbf3c8b75f49cdf800a715b42e299d1` |
| `es_ES-carlfm-x_low.onnx.json` | 4,159 | `d9bdfa9ff01eb2bc9e62e7d2593939d1e4c4d8eb7cf75f972731539d12399966` |
| `es_ES-carlfm-x_low` `MODEL_CARD` | 250 | `a6e62a3d36c37c7702d877f784001208b952e2bf4d17c72a6b5da442fbefb4b3` |
| `it_IT-riccardo-x_low.onnx.json` | 4,161 | `146ab9c634afe524e9fb7530f2510df7a42fb1db56b52658ca1fb3d98001a62a` |
| `it_IT-riccardo-x_low` `MODEL_CARD` | 260 | `7ce3b2fc4f454c84936887971574e436cc2154049b427a530f70bee050b2fdc6` |

**The British idea did not survive the licence check.** The British male voices are `en_GB-alan` (its recordings: "Copyright 2022 Mycroft AI, All Rights Reserved", so rejected), `en_GB-northern_english_male-medium` (CC BY-SA 4.0 recordings, fine-tuned from lessac), and the multi-speaker `vctk` and `semaine` (lessac lineage; `semaine` is also CC BY-NC-SA). `john` is a calm, neutral US male voice with the cleanest licence. The two `x_low` voices are lower quality than `medium` ones; the owner has since heard them in their live check (2026-10-25); the assistant has not.

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

Two programs that talk only through pipes and a command line, each run as its own process, are what the GPL FAQ calls communicating "at arms length" ("mere aggregation"), not one combined program; STEEL's own code is then not a work based on Piper. STEEL also does not distribute Piper or the voices: the setup script downloads them onto the owner's machine. This is the design's reading of the FSF FAQ, **not legal advice**. Since Phase 5b STEEL's own code is Apache-2.0 (`LICENSE`, `NOTICE`), which is consistent with this boundary: STEEL is not a work based on Piper, so it need not be GPL.

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

The code is `steel/speak/` (`text.py`, `language.py`, `config.py`, `voices.py`, `synth.py` for the engine process, `player.py`, `indicator.py`, `keys.py`, `playback.py`, `commands.py`) and `steel/tools/speak.py`. If both a default and an alternative voice for one language are installed, the alternative (fetched on purpose) is used. Keys typed before a reply starts are thrown away, so an earlier Enter cannot stop it.

No grant: the first reply is not spoken, STEEL prints that `speak` is not granted to agent `voice` (and how to grant it), the denial is logged, and the session stays silent without asking again. The decision log holds names only, never the text.

**What is spoken, and what never is.** A spoken reply can be overheard, so:

1. Only the assistant's own reply is passed to `speak`. Never a tool-data block, a room note, a transcript or a stored text. The tool also refuses any text that contains the tool-data markers (`[Tool result from`, `[End of tool result]`) or the camera line (`[Camera description`).
2. A reply the injection scanner flags (or that it cannot check) is not spoken.
3. The text is redacted with the same scanners as tool results (secrets and personal data, `steel.tooldata`); a redaction mark is spoken as "redacted". If redaction fails, nothing is spoken.
4. Code blocks are dropped (spoken as "there is code on screen"), URLs and e-mail-like links become "a link on screen", markdown marks (`#`, `*`, `_`, list bullets, table bars, link brackets) are removed, control characters are stripped.
5. At most **600 characters** are spoken (`STEEL_SPEAK_MAX_CHARS`, 50 to 4000; any other value falls back to 600), cut at a sentence or word boundary, followed by "The rest is on screen." (in Spanish and Italian in those languages).

**Nothing on disk.** The text goes from STEEL to the engine through a pipe; the audio goes from the engine through STEEL to `aplay` through pipes, in chunks of 4 KB through one reused `bytearray` that is overwritten with zeros at the end, on every path (best effort: the kernel's pipe buffers and the player's own buffers are not in STEEL's reach). No WAV or temporary file is written by STEEL, and neither program is given an output file.

**Started early (Phase 5b).** Loading a voice takes about 4 s (section 2.1), so the chat starts the engine **before it asks the model**, for the voice of the language of the owner's message (typed, or the confirmed transcript in voice chat, after the microphone has closed). The engine loads the voice and waits on its standard input. When the reply is ready and cleaned (rules below), its text is written to that stdin, which is then closed, and the engine speaks it and exits. One process still speaks one reply: the stock CLI marks no end between lines in raw mode, so end of file is the only reliable end of a reply (docs/LATENCY.md section 5). Rules:

* The early start happens only if the `audio:play` grant for agent `voice` is there. That is checked read-only, without a log entry; speaking itself still goes through `build_executor`, which checks and logs as before. After a denial in the session, nothing is started any more.
* The message only chooses the voice. It is never written to the engine; only the reply's cleaned text is, as before.
* If the reply's language needs another voice, or the waiting engine has died, it is stopped and a fresh engine is started (the old 4 s path). A fresh engine that dies at once is an error, not a loop.
* The chat stops a waiting engine when it ends, on every path (`/quit`, Ctrl-D, Ctrl-C, an error), and at interpreter exit. The engine is started through `setpriv --pdeathsig KILL` (util-linux), so **the kernel kills it if STEEL itself dies**, even on SIGKILL. Its stdin then also reaches end of file, which makes Piper exit; without `setpriv` that is the only fallback.
* A waiting engine holds its voice in memory: about 142 MiB resident for `en_US-john-medium`, measured once. It has no network (same `unshare --net`), no text and no audio device.

Measured on the real engine, into memory, nothing played, one sentence, one run each: first audio **4.38 s** with no early start, **0.37 s** when the engine was started 5 s before the text, **3.35 s** when only 1 s before (the voice was still loading), and **4.34 s** when the guessed language was wrong. Whether 5 s of model time is usual for a reply on this machine is the owner's measurement (docs/LATENCY.md section 7).

**`steel speak check`** (read only: plays nothing, opens no audio device, starts no program): engine found and its version, whether network isolation is available, the voices installed with size and SHA-256 status, the player found, whether the grant is present (asked without logging), the character limit. **At run time only the sizes of the voice files are checked** (hashing 120 MB before every reply would add a noticeable delay); `check` verifies the SHA-256 values, and the setup script keeps a file only if it matches. If a Spanish or Italian voice is missing, replies in that language are not spoken; without the English voice, `check` says not ready.

**`steel speak say "TEXT"`** speaks one short sentence (at most 200 characters) through the executor, for testing. Unlike chat, that text is on STEEL's own command line, which is the owner's choice.

**`scripts/speak_check.py`** (the owner's live check, never run by an assistant): with a scratch grant, plays one sentence in each installed language through the real speakers and prints synthesis time and real-time factor.

**`scripts/speak_setup.py`**: installs the engine and downloads the pinned voices (section 3). Run once; running it again checks and skips what is already there.

## 7. No echo handling, and what hands-free would need

The microphone is push-to-talk (docs/VOICE.md): it is open only while the owner holds the turn, and STEEL does not speak during a recording or record while speaking, since both run in the same loop one after the other. So the microphone never hears STEEL's own voice and no echo cancellation is needed. **A future hands-free mode would need acoustic echo cancellation** (or at least muting the microphone while speaking), or STEEL would transcribe and answer itself.

## 8. Limits

* **Anyone nearby hears the reply**, including names, appointments or anything else the reply contains. Redaction only catches patterns; a password in plain words is spoken. The model may repeat in its own words what was in a tool result the owner added; that is spoken, because only the explicit markers are refused.
* The output device is PipeWire's default, which may be HDMI (a TV in another room) rather than the laptop. STEEL does not choose it.
* The language guess is a heuristic and will be wrong on short or mixed replies (then English).
* About 4 s before each reply is heard (section 2.1) when the engine could not be started early: the model answered in less time than the voice takes to load, the reply's language differed from the message's, or the grant was missing at the start. Otherwise well under a second (section 6, "Started early").
* With `--speak`, an idle engine holding its voice (142 MiB resident for `en_US-john-medium`, measured once) stays in memory between the message and the reply.
* The `x_low` voices are low quality.
* The indicator and key handling need a terminal; nothing is spoken in a pipe or script.
* The network namespace needs unprivileged user namespaces; where they are disabled, nothing is spoken.
* Not a sandbox: the engine can still read the owner's files (it runs as the owner, mapped to root inside its own namespace only).

## 9. Open questions for the owner

1. Keep `en_US-john-medium` (US, public domain) or take the British `en_GB-northern_english_male-medium` despite its lessac lineage?
2. Spanish and Italian: the clean-licence `x_low` voices, or the better-sounding `es_ES-davefx-medium` / `it_IT-serena-medium` (female)?
3. ~~Should STEEL get a licence file?~~ Answered in Phase 5b: Apache-2.0 (`LICENSE`, `NOTICE`).
4. ~~Is a 4 s delay acceptable?~~ Phase 5b starts the engine early instead (section 6); a long-lived helper is not needed for that. It would still be needed to speak while the reply is being generated, which is blocked anyway (docs/LATENCY.md section 4).
5. ~~Delete the onnxruntime telemetry queue?~~ The folder `~/.cache/Microsoft/DeveloperTools/.onnxruntime/` was no longer there on the afternoon of 2026-10-25 (not deleted by the assistant), and none of the Phase 5b runs of the engine recreated it.

## 10. Evidence

**Set up on the owner's machine (2026-10-25), by the assistant, network used for this step only.** The engine venv was first built by hand from the same lock file to take the measurements in section 2.1 (`python3 -m venv`, then `pip install --require-hashes --only-binary :all: --no-deps -r scripts/piper-requirements.txt`; 197 MB, piper-tts 1.8.0, onnxruntime 1.31.0, numpy 2.5.4, protobuf 7.36.2, flatbuffers 25.12.19, pathvalidate 3.3.1, packaging 26.3). `python scripts/speak_setup.py --voices-only` then fetched the three default voices into `~/.openjarvis/steel_models/piper-voices/` (9 files, 119,807,254 bytes, 114 MiB; every SHA-256 equal to section 3), and `speak_setup.py --dry-run` reported the engine as already installed. `steel speak check` on the real home then reported the engine, `unshare`, `aplay`, all three voices "SHA-256 OK", and "grant: NOT granted" (the owner writes it), and wrote nothing. A scratch script ran STEEL's own `PiperEngine` on one reply per language, read the audio into memory and discarded it, with no player started: 5.3 s (en), 3.4 s (es) and 4.3 s (it) of audio in 4.3 to 4.4 s each; while it ran, the engine's `/proc/<pid>/cmdline` held only the model path and its environment only `LANG`, `LC_ALL` and `ORT_DISABLE_TELEMETRY=1`, and the onnxruntime telemetry queue was not touched.

Test files: `tests/test_speak_text.py`, `test_speak_core.py`, `test_speak_tool.py`, `test_speak_cli.py`, `test_speak_scripts.py`, `test_speak_policy.py`; helpers in `speak_helpers.py`. Everything runs with a fake engine process, a fake player, fake keys (or a pseudo-terminal) and a temporary home: no engine or player program is started, nothing is played, no network is used, and neither the real `~/.openjarvis` nor `~/.local/share/steel` is read. Each cell is `file::Class.test`; `test_speak_evidence.py` checks that every cited test exists and every test in these files is cited.

| Claim | Test |
| --- | --- |
| `audio:play` is in the vocabulary, is not a sensor, needs the exact tool name; `audio:*`, pattern `*` and bare `*` are refused and make the grants file invalid; no other grant covers it; a deny wildcard does | `test_speak_policy.py::AudioPlayPolicyTest.test_audio_play_is_in_the_vocabulary_and_is_not_a_sensor`, `test_speak_policy.py::AudioPlayPolicyTest.test_a_grant_must_name_the_exact_tool`, `test_speak_policy.py::AudioPlayPolicyTest.test_wildcard_audio_grants_are_refused`, `test_speak_policy.py::AudioPlayPolicyTest.test_a_wildcard_audio_grant_makes_the_whole_file_invalid`, `test_speak_policy.py::AudioPlayPolicyTest.test_no_other_grant_covers_audio_play`, `test_speak_policy.py::AudioPlayPolicyTest.test_a_deny_wildcard_covers_audio_play`, `test_speak_tool.py::PermissionTest.test_a_wildcard_audio_grant_makes_the_file_invalid_and_all_is_denied` |
| The tool `speak` declares exactly `audio:play`, takes only `text`, runs as agent `voice` through `build_executor`, is registered nowhere and builds no executor itself | `test_speak_tool.py::PermissionTest.test_the_tool_declares_exactly_audio_play_and_takes_only_text`, `test_speak_tool.py::PermissionTest.test_with_the_grant_the_reply_is_spoken_as_agent_voice`, `test_speak_tool.py::PermissionTest.test_the_tool_is_not_registered_and_never_builds_an_executor` |
| No grant: silent, the denial is logged; another agent's or another tool's grant does not count; in chat the notice is printed once, logged once, then the session is silent | `test_speak_tool.py::PermissionTest.test_no_grant_means_silent_and_the_denial_is_logged`, `test_speak_tool.py::PermissionTest.test_a_grant_for_another_agent_or_tool_does_not_allow_it`, `test_speak_cli.py::ChatSpeakTest.test_no_grant_says_so_once_logs_once_and_stays_silent` |
| `--speak` is off by default (`steel chat`, `steel chat --voice`); with it each reply is spoken after it is printed, with the reminder line; in voice chat only after the microphone has closed | `test_speak_cli.py::ChatSpeakTest.test_speaking_is_off_unless_speak_is_passed`, `test_speak_cli.py::ChatSpeakTest.test_with_the_grant_each_reply_is_spoken_after_it_is_printed`, `test_voice_cli.py::ChatVoiceTest.test_replies_are_spoken_only_with_speak_after_the_recording` |
| Tool-data blocks, room notes, camera text and flagged text are never spoken (also when the model repeats a tool result); a scanner that fails means silence | `test_speak_text.py::PrepareTest.test_tool_data_room_notes_and_camera_text_are_never_spoken`, `test_speak_text.py::PrepareTest.test_flagged_text_is_never_spoken`, `test_speak_tool.py::WhatIsSpokenTest.test_tool_data_room_notes_and_flagged_text_never_reach_the_engine`, `test_speak_cli.py::ChatSpeakTest.test_a_reply_that_repeats_tool_data_is_not_spoken` |
| Secrets and personal data are redacted and said as a word; if redaction fails nothing is spoken | `test_speak_text.py::PrepareTest.test_secrets_and_personal_data_are_redacted_before_speaking`, `test_speak_text.py::PrepareTest.test_if_redaction_fails_nothing_is_spoken` |
| Markdown, links, URLs, code blocks and control characters are removed before the engine sees the text; nothing left means nothing spoken | `test_speak_text.py::StripTest.test_markdown_marks_are_removed`, `test_speak_text.py::StripTest.test_links_and_urls_become_a_short_phrase`, `test_speak_text.py::StripTest.test_code_blocks_are_dropped_and_mentioned_once`, `test_speak_text.py::StripTest.test_control_characters_are_stripped_and_lines_joined`, `test_speak_text.py::PrepareTest.test_a_reply_with_nothing_left_to_say_is_not_spoken`, `test_speak_tool.py::WhatIsSpokenTest.test_markdown_and_urls_are_stripped_before_the_engine_sees_them` |
| At most 600 characters (`STEEL_SPEAK_MAX_CHARS`, 50 to 4000, else 600), cut at a sentence or word, then "the rest is on screen" in the reply's language | `test_speak_text.py::ConfigTest.test_the_spoken_length_defaults_to_600_and_is_checked`, `test_speak_text.py::PrepareTest.test_long_text_is_cut_and_says_the_rest_is_on_screen`, `test_speak_text.py::PrepareTest.test_a_cut_without_a_sentence_end_falls_on_a_word`, `test_speak_tool.py::WhatIsSpokenTest.test_a_long_reply_is_cut_and_the_rest_is_on_screen` |
| Language: English, Spanish and Italian are recognised; short, mixed or other languages are English; the language picks the voice; a missing voice means silence and a message | `test_speak_text.py::LanguageTest.test_english_spanish_and_italian_are_recognised`, `test_speak_text.py::LanguageTest.test_unsure_short_mixed_or_other_text_falls_back_to_english`, `test_speak_text.py::PrepareTest.test_a_plain_reply_is_spoken_in_its_language`, `test_speak_tool.py::WhatIsSpokenTest.test_the_language_picks_the_voice`, `test_speak_tool.py::WhatIsSpokenTest.test_a_missing_voice_is_reported_and_nothing_starts` |
| The engine is started as `unshare --net --map-root-user -- <venv>/bin/python -I -m piper -m MODEL --output-raw`; the text goes only to its standard input (never the command line, so not in `ps`, and never the environment); its environment is `LANG`, `LC_ALL`, `ORT_DISABLE_TELEMETRY=1` only; one line | `test_speak_core.py::EngineTest.test_the_engine_runs_without_network_and_gets_the_text_only_on_stdin`, `test_speak_core.py::EngineTest.test_the_engine_environment_has_no_home_proxy_or_key_and_no_telemetry`, `test_speak_core.py::EngineTest.test_several_lines_become_one_line_on_stdin` |
| Without `unshare`, or without the installed engine, nothing is started; the engine's place and version are found without running it | `test_speak_core.py::EngineTest.test_without_unshare_the_engine_is_never_started`, `test_speak_core.py::EngineTest.test_without_the_engine_installed_nothing_is_started`, `test_speak_core.py::EngineTest.test_the_engine_location_and_version_are_found_without_running_it`, `test_speak_tool.py::WhatIsSpokenTest.test_an_unavailable_engine_or_terminal_means_silence` |
| `aplay` reads raw audio from standard input and is given no file; it never opens without the SPEAKING proof, which cannot be made by hand; an unexpected rate or a missing `aplay` starts nothing | `test_speak_core.py::SinkTest.test_aplay_reads_raw_audio_from_stdin_and_is_given_no_file`, `test_speak_core.py::SinkTest.test_the_speaker_never_opens_without_the_speaking_proof`, `test_speak_core.py::SinkTest.test_an_unexpected_rate_or_a_missing_player_starts_nothing` |
| The SPEAKING line is on screen before the speaker opens and says how to stop; without a terminal or if printing fails nothing plays and the engine is stopped | `test_speak_core.py::PlaybackTest.test_the_speaking_line_is_on_screen_before_the_speaker_opens`, `test_speak_core.py::IndicatorTest.test_no_terminal_or_a_failing_write_means_no_proof`, `test_speak_core.py::IndicatorTest.test_the_line_says_how_to_stop`, `test_speak_core.py::PlaybackTest.test_without_a_terminal_nothing_plays_and_the_engine_is_stopped` |
| All audio reaches the player, which then finishes; the buffer is zeroed afterwards; an engine without sound and a player that will not open are reported, and the engine is stopped | `test_speak_core.py::PlaybackTest.test_all_audio_reaches_the_player_and_then_it_finishes`, `test_speak_core.py::PlaybackTest.test_the_audio_buffer_is_zeroed_after_use`, `test_speak_core.py::PlaybackTest.test_an_engine_that_produces_nothing_is_reported`, `test_speak_core.py::PlaybackTest.test_if_the_player_cannot_open_the_engine_is_stopped` |
| Enter or a typed line stops the player and the engine at once (also while the player drains); keys typed earlier do not count; a typed line is kept, once, and becomes the next message (`exit` then leaves) | `test_speak_core.py::PlaybackTest.test_enter_stops_the_player_and_the_engine_at_once`, `test_speak_core.py::PlaybackTest.test_a_key_while_the_player_drains_also_stops_it`, `test_speak_core.py::TypedLineStopTest.test_a_bare_enter_stops_and_leaves_no_line`, `test_speak_core.py::TypedLineStopTest.test_a_typed_line_stops_and_is_kept_once_for_the_chat`, `test_speak_core.py::TypedLineStopTest.test_keys_typed_before_the_reply_do_not_stop_it`, `test_speak_tool.py::StopTest.test_enter_stops_the_player_and_the_engine`, `test_speak_cli.py::ChatSpeakTest.test_a_line_typed_to_stop_a_reply_is_the_next_message`, `test_speak_cli.py::ChatSpeakTest.test_a_typed_exit_while_speaking_leaves` |
| Ctrl-C stops the reply (player and engine) and the chat goes on; the 120 s limit stops a long reply | `test_speak_core.py::PlaybackTest.test_ctrl_c_cancels_and_stops_both`, `test_speak_tool.py::StopTest.test_ctrl_c_cancels_the_reply_and_stops_both`, `test_speak_cli.py::ChatSpeakTest.test_ctrl_c_while_speaking_stops_the_reply_and_the_chat_goes_on`, `test_speak_core.py::PlaybackTest.test_the_audio_limit_stops_a_long_reply` |
| No file is written anywhere during a spoken reply (no temporary file, nothing new in the home, the text in no file); STEEL's process opens no connection and resolves no name while speaking | `test_speak_tool.py::NoDiskNoNetworkTest.test_a_spoken_reply_writes_no_file_anywhere`, `test_speak_tool.py::NoDiskNoNetworkTest.test_no_network_is_used_while_speaking` |
| The result and the decision log never contain the text | `test_speak_tool.py::WhatIsSpokenTest.test_the_result_and_the_log_never_contain_the_text` |
| STEEL's speaking code imports no Piper, onnxruntime, espeak, cloud or network library and writes no file | `test_speak_tool.py::SourceHygieneTest.test_nothing_imports_piper_onnxruntime_or_a_network_library`, `test_speak_tool.py::SourceHygieneTest.test_nothing_writes_a_file_or_makes_a_temporary_one` |
| Voices are pinned to one revision with size and SHA-256 (the defaults' values are the ones in section 3); a voice counts only with every file at its pinned size and not a symlink; a voice fetched on purpose is preferred | `test_speak_core.py::VoicesTest.test_every_voice_is_pinned_to_one_revision_with_size_and_sha256`, `test_speak_core.py::VoicesTest.test_the_default_checksums_are_the_ones_in_the_design_document`, `test_speak_core.py::VoicesTest.test_a_voice_counts_only_with_every_file_at_its_pinned_size`, `test_speak_core.py::VoicesTest.test_a_symlinked_voice_file_does_not_count`, `test_speak_core.py::VoicesTest.test_the_voice_for_a_language_prefers_one_fetched_on_purpose` |
| `steel speak check` is read only (starts nothing, logs nothing, creates nothing), lists what is missing with the example grant, says ready only with engine, `unshare`, `aplay`, verified voices (English required) and the grant | `test_speak_cli.py::SpeakCheckTest.test_check_is_read_only_starts_nothing_and_logs_nothing`, `test_speak_cli.py::SpeakCheckTest.test_check_lists_what_is_missing`, `test_speak_cli.py::SpeakCheckTest.test_with_everything_in_place_it_is_ready`, `test_speak_cli.py::SpeakCheckTest.test_without_an_english_voice_it_is_not_ready` |
| `steel speak say` speaks one sentence (at most 200 characters) through the policy, and is denied and logged without the grant | `test_speak_cli.py::SpeakSayTest.test_say_speaks_one_sentence_through_the_policy`, `test_speak_cli.py::SpeakSayTest.test_say_without_a_grant_is_denied_and_logged`, `test_speak_cli.py::SpeakSayTest.test_say_takes_one_short_sentence_only`, `test_speak_cli.py::SpeakSayTest.test_the_command_line_needs_text_for_say_and_none_for_check` |
| Setup keeps a voice file only if size and SHA-256 match (mode 600, directories 700, also on a re-run), does not fetch correct files again, fetches nothing on a dry run, only from the pinned https repository; the engine is installed only with hashes, no dependencies resolved, telemetry off; an installed engine is left alone | `test_speak_scripts.py::SetupVoicesTest.test_matching_files_are_kept_private`, `test_speak_scripts.py::SetupVoicesTest.test_a_file_that_does_not_match_its_pin_is_not_kept`, `test_speak_scripts.py::SetupVoicesTest.test_files_already_there_and_correct_are_not_fetched_again`, `test_speak_scripts.py::SetupVoicesTest.test_a_dry_run_fetches_and_writes_nothing`, `test_speak_scripts.py::SetupVoicesTest.test_every_url_is_https_at_the_pinned_revision_and_others_are_refused`, `test_speak_scripts.py::SetupVoicesTest.test_the_defaults_and_named_extra_voices_are_chosen`, `test_speak_scripts.py::SetupEngineTest.test_the_lock_file_pins_every_package_by_hash`, `test_speak_scripts.py::SetupEngineTest.test_the_engine_is_installed_with_hashes_only_and_no_telemetry`, `test_speak_scripts.py::SetupEngineTest.test_an_installed_engine_is_left_alone_and_a_dry_run_runs_nothing`, `test_speak_scripts.py::SpeakCheckScriptTest.test_the_url_base_is_the_pinned_repository` |
| `scripts/speak_check.py` measures into memory, then speaks each language through the executor with a scratch grant, leaves the real home untouched and deletes the scratch (**run with fakes; the live run is the owner's**) | `test_speak_scripts.py::SpeakCheckScriptTest.test_it_measures_then_speaks_each_language_with_a_scratch_grant` |
| Started early (Phase 5b): a prepared engine waits with no text and gets the text on stdin later, which is then closed; preparing twice starts one engine; a waiting engine for another voice, or one that died (before the reply, or before a second prepare), is stopped and a fresh one used; one that dies as the text arrives is replaced once, then it is an error; `close` stops it; without `unshare` or the engine nothing is prepared | `test_speak_core.py::EarlyStartTest.test_a_prepared_engine_waits_with_no_text_then_gets_it_on_stdin`, `test_speak_core.py::EarlyStartTest.test_preparing_twice_for_the_same_voice_starts_one_engine`, `test_speak_core.py::EarlyStartTest.test_a_waiting_engine_for_another_voice_is_stopped_for_a_fresh_one`, `test_speak_core.py::EarlyStartTest.test_a_crashed_waiting_engine_is_replaced_by_a_fresh_one`, `test_speak_core.py::EarlyStartTest.test_an_engine_that_dies_as_the_text_arrives_is_replaced_once`, `test_speak_core.py::EarlyStartTest.test_close_stops_the_waiting_engine_and_the_next_reply_starts_fresh`, `test_speak_core.py::EarlyStartTest.test_without_unshare_or_the_engine_nothing_is_prepared` |
| The engine is started through `setpriv --pdeathsig KILL`; no engine process is left after the chat closes it, or after STEEL is killed with SIGKILL (real processes, checked in `/proc`; a shell script stands in for `unshare` and Piper, so neither runs) | `test_speak_core.py::EarlyStartTest.test_the_kernel_stops_the_engine_if_steel_dies`, `test_speak_core.py::NoOrphanTest.test_no_engine_process_is_left_after_the_chat_closes_it`, `test_speak_core.py::NoOrphanTest.test_no_engine_process_is_left_if_steel_is_killed` |
| In chat the engine is started before the model is asked, for the voice of the message's language, only with the grant; the message never reaches the engine; no engine is left waiting after `/quit`, Ctrl-D, Ctrl-C or an error; a whole spoken turn (start early, model, speak) uses no network and writes no file but the decision log; in voice chat only after the microphone has closed | `test_speak_cli.py::EarlyStartChatTest.test_the_engine_is_started_before_the_model_is_asked`, `test_speak_cli.py::EarlyStartChatTest.test_the_voice_started_early_follows_the_language_of_the_message`, `test_speak_cli.py::EarlyStartChatTest.test_without_the_grant_no_engine_is_started_early`, `test_speak_cli.py::EarlyStartChatTest.test_the_message_never_reaches_the_engine_only_the_reply_does`, `test_speak_cli.py::EarlyStartChatTest.test_no_engine_is_left_waiting_after_quit_ctrl_d_ctrl_c_or_an_error`, `test_speak_cli.py::EarlyStartChatTest.test_a_whole_spoken_turn_uses_no_network_and_writes_no_file`, `test_voice_cli.py::ChatVoiceTest.test_replies_are_spoken_only_with_speak_after_the_recording` |
| Tests never look at the real engine directory or home | `test_speak_cli.py::LiveStateTest.test_the_engine_dir_and_home_are_the_temporary_ones` |

**Mutation checks (hand-made, a small sample, not exhaustive).** The speaking code was written before most of its tests in Phase 5a (the policy change, the text rules and the language pick had tests first), so 35 one-line changes were made by hand, one at a time, and the speaking, voice and chat tests run after each: the engine without `--net`, the text on the command line, the engine inheriting the environment, starting without `unshare`, the player without the proof check, opening before showing the line, ignoring keys, Ctrl-C or the limit, not stopping the engine or the player, no buffer wipe, no marker check, a failing scanner treated as clean, no redaction, no cap, URLs or code kept, a looser language rule, the wrong capability, the text in the result, no key reset, `audio:play` not exact-name, the chat notice repeated, Ctrl-C escaping, the typed line dropped, no flush of earlier keys, voice symlinks followed, the default preferred, setup keeping a bad file, world-readable directories, no `--require-hashes`, telemetry on, `--speak` ignored, no length check for `say`. **34 were caught.** One (removing the wipe after each chunk) survived because the final wipe made it redundant; that line was removed and removing the final wipe is caught.

**What no test proves.**

* Anything with the **real speakers**: that `aplay` plays through PipeWire here, that it sounds right, that stopping is immediate to the ear, that the volume is sensible. `scripts/speak_check.py` exists for that; the owner reported on 2026-10-25 that it works, which is their observation, not a test.
* That the voices are understandable, or that the `x_low` voices are good enough.
* That the engine really cannot reach the network is a property of `unshare --net`, shown once by hand (only a loopback that is down inside it) and used for every measurement; tests only check that the engine is started that way.
* The terminal behaviour in the owner's terminal emulator while the SPEAKING line is redrawn and the owner types.
* That nothing else in STEEL's process could open the speaker: the proof object stops mistakes, not code that ignores it.
* That the kernel kills a waiting **Piper** engine when STEEL dies: the tests use a stand-in, and `setpriv` with `unshare --map-root-user` was checked by hand once with `sleep` in place of the engine (it was killed; without `setpriv` it was not). The death signal follows the thread that started the engine; the chat starts it from its main thread, and the tool runs on OpenJarvis' worker threads, which live until the process ends.

**Phase 5b mutation checks (hand-made, small).** 21 one-line changes to the Phase 5b code (`steel/warm.py`, the early start in `synth.py`, the chat wiring, `--timings`). Two changed nothing (one edited a comment, one an attribute the wrapper does not have) and do not count. Of the 19 that did, 18 were caught at once; one (a waiting engine for the same voice that had died was reused by `prepare`) exposed a gap, now covered by `EarlyStartTest.test_a_crashed_waiting_engine_is_replaced_by_a_fresh_one`.
