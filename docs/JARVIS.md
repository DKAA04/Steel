# The Jarvis loop: a hands-free, local wake word (Phase 5c design)

**Status: DESIGN ONLY. Nothing in this document is implemented yet.** This file was written and committed **before** any wake-word code, before any wake-word model was downloaded, and before any "never" rule in CLAUDE.md, SECURITY.md, ARCHITECTURE.md or docs/VOICE.md was changed (see the git history). Section 16 will list, per claim, the test that proves it, once the code exists; until then nothing here is claimed as working.

**The owner's decision (2026-10-25), quoted from the task:** "I, the owner, lift the rule 'never add a wake word or any always-on listening' ONLY for the design written in docs/JARVIS.md, and ONLY if that design satisfies every requirement listed in docs/VOICE.md section 7 [...] where it and this prompt differ, the stricter one wins. The rule stays in force for everything else: no cloud speech service, no background service or daemon, no start at login, no listening when the loop is not running in a foreground terminal. If any section-7 requirement cannot be met, STOP and tell me; never weaken it."

docs/VOICE.md section 7 lists six requirements. Section 3 below takes them one by one. **All six can be met with what is on this machine without installing anything**, with the limits stated in each subsection; if the implementation finds one cannot be met after all, the loop is not built.

## 1. Wake-word engines

Requirements: runs fully locally and offline; permissive **code** licence; no account, access key or network licence check; nothing downloaded at run time; no telemetry (onnxruntime's must be off before it loads, as STEEL already does, docs/VOICE.md section 3).

| | openWakeWord | microWakeWord | Porcupine (Picovoice) | Vosk keyword grammar | Mycroft Precise |
| --- | --- | --- | --- | --- | --- |
| Code licence | Apache-2.0 ([README](https://raw.githubusercontent.com/dscripka/openWakeWord/main/README.md)) | Apache-2.0 | Apache-2.0 SDK | Apache-2.0 | Apache-2.0 |
| Pretrained model licence | **CC BY-NC-SA 4.0** ("due to the inclusion of datasets with unknown or restrictive licensing as part of the training data", README). Non-commercial. | Apache-2.0: the [esphome/micro-wake-word-models](https://github.com/esphome/micro-wake-word-models) README says its Apache-2.0 terms cover the whole repository "including the .tflite model weights" | proprietary; needs an **AccessKey** from a Picovoice account | Apache-2.0 | Apache-2.0 |
| "hey jarvis" pretrained | yes, `hey_jarvis_v0.1` | yes, `models/v2/hey_jarvis.tflite` (52,272 bytes) | yes | any phrase, but see the next row | no |
| How it listens | three small ONNX models: a mel spectrogram, a shared speech embedding (re-implemented from Google's Apache-2.0 TFHub module, README), and a 1.27 MB "hey jarvis" classifier | one TFLite micro model | closed library | **full speech recognition** of everything said, then a word match | TensorFlow |
| Runtime it needs | onnxruntime (already in the OpenJarvis environment: 1.31.0, used by faster-whisper) or tflite-runtime | a TFLite runtime (`tflite-runtime`, `ai-edge-litert` or `pymicro-wakeword`); **none is installed here** | its own library | Vosk + a 40 MB model | TensorFlow |
| Network at run time | none, unless `openwakeword.utils.download_models()` is called | none | **licence check needs the network** | none | none |
| Accuracy claims | the project's target: "<5% false-reject rates and <0.5/hour false-accept rates with appropriate threshold tuning" (README). A competitor's benchmark ([LiveKit](https://livekit.com/blog/livekit-wakeword), not independent) reports 8.5 false positives per hour at its chosen threshold, not broken out per model | none found | vendor benchmark only | n/a | archived project |
| CPU cost (claims) | "a single core of a Raspberry Pi 3 can run 15-20 openWakeWord models simultaneously in real-time" (README) | designed for ESP32 microcontrollers | vendor claim under 4 % of a Pi 3 core | a full recogniser running continuously | |
| Verdict | **chosen** (section 1.1) | best licence; not usable without installing a runtime | **excluded**: account and access key | **excluded**: it transcribes everything said before the trigger, which section 3d forbids | **excluded**: archived, TensorFlow |

The openWakeWord numbers above are the project's own claims and one competitor's; none was measured here. **CPU per second of audio, RAM and the false-trigger rate on this machine will be measured after the model files are downloaded (Step 2) and written into section 1.2**; the owner's `scripts/jarvis_check.py` counts false triggers in the room.

### 1.1 Recommendation: openWakeWord's "hey jarvis", run by STEEL's own small port on onnxruntime

* **No new Python package.** STEEL does not install `openwakeword` (PyPI 0.6.0, 2024-02-11; it would pull in `requests`, `scipy`, `scikit-learn`, `tqdm` and on Linux `tflite-runtime`, and contains a downloader). This phase may download only the model files. Instead STEEL ports the streaming feature code of openWakeWord's `AudioFeatures` class (`openwakeword/utils.py`, Apache-2.0, Copyright 2022 David Scripka): about 100 lines of numpy around three `onnxruntime.InferenceSession` objects. The port keeps the exact numbers (16 kHz 16-bit mono, 1280-sample = 80 ms frames, the mel transform `x/10 + 2`, 76-frame windows every 8 frames, 16 embeddings per prediction, the first 5 predictions forced to 0) and gets its own header and a row in docs/PROVENANCE.md and NOTICE. It changes only the buffer sizes (smaller: section 2) and never downloads.
* **Model files**, from the openWakeWord GitHub release **v0.5.1** (published 2023-09-02; GitHub gives no checksum for these assets, so `scripts/wake_setup.py` records the SHA-256 at the first download and pins it in the code, and every later run and every start of the loop compares against it):

  | File | Size (bytes) | Licence |
  | --- | --- | --- |
  | `melspectrogram.onnx` | 1,087,958 | part of the openWakeWord release; the README gives no separate licence (treated as the release's CC BY-NC-SA 4.0 to be safe) |
  | `embedding_model.onnx` | 1,326,578 | as above (re-implementation of Google's Apache-2.0 speech embedding) |
  | `hey_jarvis_v0.1.onnx` | 1,271,370 | CC BY-NC-SA 4.0 |

* **onnxruntime telemetry:** the wake engine imports onnxruntime only inside its constructor, after `steel.offline.telemetry_off()`; a test proves the switch is set before the import. (On 2026-10-25 the assistant imported onnxruntime once *without* the switch while checking which packages exist; that recreated `~/.cache/Microsoft/DeveloperTools/.onnxruntime/`. `steel doctor` now warns about that folder and prints the command to delete it.)
* **What the licence means for STEEL.** STEEL's code, including the port, stays Apache-2.0. The model files are downloaded by the owner's setup script onto the owner's machine and are not in this repository. **Using them is fine for personal, non-commercial use.** Any commercial use of STEEL with these model files (selling it, using it in paid client work) is not allowed by CC BY-NC-SA 4.0; that would need another model: microWakeWord's Apache-2.0 "hey jarvis" (which needs a TFLite runtime, a later decision), or a model trained on data whose licence allows it.
* **A custom "hey steel" model** would take openWakeWord's training notebook ("<1 hour" in Colab, README), thousands of synthetic clips from a text-to-speech engine, negative audio, and ideally a GPU; the resulting model's licence follows the training data. Not done (section 13).

### 1.2 Measured on this machine

To be filled in after the download (Step 2): CPU time per second of audio for the three models, resident memory, start-up time, and the test that the engine opens no network connection.

## 2. Reading the microphone continuously, with the least risk

* **One `arecord` process** for listening (`arecord -q -D default -f S16_LE -r 16000 -c 1 -t raw -d SECONDS`, no file: raw audio on a pipe), started through `setpriv --pdeathsig KILL` so the kernel kills it if STEEL dies, and with `-d` set to the rest of the session limit plus one second, so it also ends by itself.
* It is read in **80 ms frames (1280 samples, 2,560 bytes)**. Each frame goes **only** to the wake-word engine. Nothing else reads it: no transcription, no file, no log, no network, no level meter.
* **Ring buffers, all in RAM, all fixed-size** (smaller than upstream openWakeWord, which keeps 10 s of raw audio):
  * raw audio: the last **1,760 samples (110 ms, 3,520 bytes)**, which is what the mel model needs for one frame (the new 1,280 samples plus 480 of context);
  * mel spectrogram: the last **84 frames (about 0.84 s)**, which is the 76-frame window plus one step;
  * speech embeddings: the last **16 (about 1.3 s)**, exactly what the classifier reads.
  None of these is audio that could be played back except the 110 ms raw ring; the other two are derived features. A test asserts the sizes never exceed these numbers.
* **Nothing before the trigger is kept, transcribed or stored.** When the wake word is detected, the listening `arecord` is **closed** and every buffer is overwritten with zeros (best effort: Python and the kernel may keep copies STEEL cannot reach). The request is then recorded by a **separate** recording (section 6), so **the pre-roll never reaches speech recognition**: the recogniser only ever sees audio recorded after the wake word.
* The same zeroing happens on mute, on quit, on every limit and on any error.

## 3. docs/VOICE.md section 7, requirement by requirement

### 3a. A separate written consent that says "always on"

* File `steel_wake_consent.json` in the OpenJarvis home, written **only** by `steel jarvis consent` after the owner types `yes`, with the same checks as the voice and camera consents (`steel/consent.py`: a regular file, the owner's, mode 600, one timestamp). `steel jarvis revoke` deletes it.
* The text the owner reads (quoted word for word in section 3a.1; a test compares them) says in plain words: **the microphone stays open (ALWAYS ON) while `steel jarvis` runs**; everything said near the computer is analysed on this computer for the wake word; only what follows the wake word is transcribed; guests, family and phone calls nearby may be heard; false triggers will happen; nothing is recorded or stored before the wake word; how to stop it (`q`, `m`, `steel jarvis stop`, Ctrl-C).
* **The push-to-talk consent does not count.** No wake consent file means the listening microphone never opens. The post-trigger recording also still needs the voice consent, because it reuses `microphone_listen` (section 6).
* Tests (planned): no wake consent → no microphone, even with the voice consent; consent written only after a typed yes; the text says "ALWAYS ON".

### 3a.1 The consent text, word for word

`steel jarvis consent` prints this and records consent only after a typed `yes` (`steel/jarvis/consent.py`; a test compares the two):

```text
STEEL Jarvis loop: ALWAYS-ON LISTENING. Please read.

While "steel jarvis" runs, the microphone of this computer stays open: it is ALWAYS ON until you quit, mute it or a limit ends the session.

- Everything said near this computer is analysed on this computer, 80 milliseconds at a time, by a small model that listens for "hey jarvis". Nothing is recorded, transcribed, stored or sent anywhere before the wake word.
- After the wake word, the next few seconds are recorded and turned into text on this computer, whoever is speaking. You see the text and can cancel it (Esc) before it is sent to the model.
- Guests, family, children, people on a phone call and voices from a television or radio may be heard. They have not agreed to this. Tell them, and do not run it where that is not appropriate.
- False triggers will happen: a word that sounds like "hey jarvis" can start a recording nobody asked for.
- A red window outside the terminal and a line in the terminal show when the microphone is open. If they cannot be shown, the microphone is not opened.
- To stop it: press q, press m to mute (the microphone closes), press Ctrl-C, or run "steel jarvis stop" in another terminal. The session ends by itself after 60 minutes, after 20 minutes without a wake word, or after 30 wake words in an hour.
- It runs only in a terminal you started. There is no background service and nothing starts at login.
- This permission is stored as a date and time in a file in your OpenJarvis folder. Withdraw it at any time with: steel jarvis revoke
- Push-to-talk consent (steel voice consent) does not count for this, and this does not replace it: the request after the wake word is recorded by the push-to-talk tool, which needs its own consent. You must also grant the tools by hand in steel_policy.json for the agent "jarvis".

Type yes to agree.
```

### 3b. A persistent visible state outside the terminal

**What this machine has** (read 2026-10-25, nothing installed): KDE Plasma 6.3.6 on Wayland (`XDG_CURRENT_DESKTOP=KDE`, XWayland on `:0`); `notify-send`, `kdialog`, `xmessage`; Python's `tkinter` (Tk 8.6) in the OpenJarvis environment; PipeWire with the ALSA plug-in (`99-pipewire-default.conf`), so `arecord` records through PipeWire; Plasma's volume applet (`org.kde.plasma.volume`). No `zenity` or `yad`.

**Choice: a small always-on-top window drawn by a separate helper process, the "guard"** (`python -I -m steel.jarvis.guard`, Tk, no new package). A borderless (`overrideredirect`), topmost red badge in the top-right corner of the screen: "● STEEL MICROPHONE ON: listening for 'hey jarvis'" (and "RECORDING" while the request is recorded). Checked once by hand on this machine with no microphone: the window maps, is viewable and topmost (`333x33+1567+20` on a 1920x1080 screen). It does not depend on the terminal: it stays when the terminal is hidden or minimised, and a borderless window has no minimise button.

* **It is shown before the microphone opens.** The loop asks the guard to show it and waits for the guard's answer `SHOWN`, which the guard sends only after Tk reports the window mapped. No answer within 5 s, or a guard that exits, means the microphone is not opened.
* **It disappears when the microphone closes, and the microphone cannot run without it.** The guard does not trust the loop's word: every 100 ms it looks in `/proc` for `arecord` processes whose parent is the loop. While one exists, the window stays; when none exists and the loop said `HIDE`, the window goes. If one exists while the window is not shown, the guard kills it.
* Why not the others: a `notify-send` notification can be dismissed and then shows nothing while the microphone is open; `kdialog` blocks and is not a status. **Plasma itself also shows a microphone icon in the system tray while a program records through PipeWire**; that is an independent second sign, but it was not checked here (it needs the microphone open), so STEEL does not rely on it.
* **Limits:** a full-screen program (a video, a game) may cover the window; on a second screen it is on the primary screen only; it is software in the owner's session, not a hardware light.
* **In the terminal too**, one status line: `● LISTENING for 'hey jarvis'`, `◆ HEARD WAKE WORD`, `● RECORDING` (the existing REC line), `… THINKING`, `▶ SPEAKING`, `■ MUTED (microphone closed)`. If that line cannot be written to a terminal, the microphone is not opened (as for the REC line, docs/VOICE.md 6.3): the listening source demands a proof object that only the terminal status and the guard's `SHOWN` together can make.
* Tests (planned): no `SHOWN` → no microphone; the terminal line is written before the microphone opens; the guard kills an `arecord` child that runs while the window is hidden; the guard hides the window when no `arecord` child is left.

### 3c. An off switch that works while STEEL is stuck

Four independent ways, none of which needs the loop's cooperation:

1. **`steel jarvis stop`**, a separate command run from any other terminal: reads the pidfile `steel_jarvis.pid` (mode 600: the loop's and the guard's process ids plus each process's start time from `/proc/PID/stat`, so a reused process id is never killed), then sends **SIGKILL** to every `arecord` whose parent is the loop, to the loop and to the guard, and removes the pidfile.
2. **The kernel:** every `arecord` STEEL starts in the loop is started through `setpriv --pdeathsig KILL` (as Piper is), so it dies when the thread that started it dies, even on SIGKILL. Both microphones (listening and the post-trigger recording) are started that way in the loop.
3. **The guard's watchdog:** while a listening microphone is open, the loop sends a heartbeat to the guard at least once a second from the frame loop itself (not from a separate thread, which would keep beating while the frame loop hangs). No heartbeat for **10 s** with an `arecord` child alive: the guard kills the `arecord` children and then the loop. When the guard's input ends (the loop died), it kills any `arecord` it has seen and exits.
4. **By hand:** `steel jarvis check` prints the fallback in plain words: `pkill -KILL -f 'steel jarvis'` and, if a microphone is still on, `pkill -KILL -x arecord` (which ends every `arecord` of this user, not only STEEL's).

Also: `q` quits, `m` mutes (section 4), Ctrl-C ends the loop.

Tests (planned): `steel jarvis stop` kills a deliberately hung fake loop (it ignores SIGTERM) and its stand-in `arecord` child; a reused process id with another start time is not killed; the watchdog kills the microphone when heartbeats stop (fake clock) and a real stand-in when they stop for real; the guard kills what it has seen when its input ends; no `arecord` survives a SIGKILL of the loop (real stand-in processes).

### 3d. Nothing heard before the trigger is kept

Section 2: frames go only to the wake engine's fixed-size ring buffers; never written, never transcribed, never logged; zeroed when listening ends; the pre-roll is never passed to speech recognition, because the request is a separate recording. The decision log holds tool names only.

Tests (planned): the transcriber receives only the post-trigger recording's audio (different bytes from the listening frames); the ring buffers never exceed their sizes; nothing is written to the temporary home or the temporary directory during a whole simulated session; the buffers are zeroed after listening; the audit log holds no transcript text.

### 3e. Review of the risk to other people in the room

**Who is affected.** Anyone near the computer while the loop runs: family, children, guests, people on a phone call or video call on speaker, voices from a television, radio or video. None of them chose this; most will not know.

**What happens to their voices.** Everything they say is analysed on this computer, 80 ms at a time, by the wake-word model, and immediately thrown away (at most 110 ms of raw audio and about 1.3 s of features exist at any moment). Nothing is recorded, transcribed or stored **unless the wake word is detected**. After a detection (real or false), the next few seconds of speech **are** recorded and transcribed, whoever speaks: that is the moment other people's words can reach the screen and, after the countdown, the model.

**False triggers.** The model was trained on synthetic speech; its project targets under 0.5 false triggers per hour, and a competitor measured about 8.5 per hour for openWakeWord in its own test. "Jarvis" in a film, a similar word, or a voice on the television can trigger it. Every trigger opens a short recording of whoever is speaking.

**What limits it** (each enforced by code and tested):
* **Foreground only.** Only while `steel jarvis` runs in a terminal the owner started. No service, daemon, autostart or background mode exists (section 13).
* **Visible**: the window outside the terminal and the terminal line (3b).
* **Time limits**: the session ends after 60 minutes (at most 240), and after 20 minutes without a wake word (section 4).
* **Rate limit**: more than 30 wake triggers in an hour ends the session, with a message: frequent triggers mean a noisy room or a television, and listening should stop.
* **Nothing kept before the trigger** (3d); the post-trigger recording is in RAM only and wiped after transcription.
* **The owner sees every transcript first**, with a countdown and Esc to cancel (section 6). With `STEEL_JARVIS_CONFIRM_SECONDS=0` that check is gone; the owner chooses that.
* **No tools by voice** (section 8): a guest cannot make STEEL look, search notes or act.
* **Not remembered by default** (section 7): what a guest says is not turned into "facts about the owner".
* **Mute** (`m`) closes the microphone completely.

**What remains unprotected, plainly.** People nearby are heard by the wake-word model without their knowledge or consent. After any trigger, a guest's words are transcribed and, unless the owner cancels within the countdown, sent to the local model and kept in the conversation in RAM. The answer is spoken aloud. STEEL cannot tell whose voice it is (no speaker recognition, section 13). The window cannot be seen by someone who does not look at the screen, and means nothing to someone who does not know what it is. **The owner is responsible for telling the people in the room, and for not running the loop when that is not appropriate** (a shared office, a client's premises, children who cannot consent). The consent text says so.

### 3f. Its own capability name

docs/VOICE.md section 7 asks for its own name but does not give one, so it is **`sensor:microphone_always_on`** ("listen to the microphone continuously for a wake word"). In docs/POLICY.md and `steel/security/policy.py` it is a sensor like the others: never covered by `*` or any wildcard, a grant must name one exact tool, and a grant with pattern `*` makes the grants file invalid.

* The always-on tool is **`wake_listen`**, declaring exactly `sensor:microphone_always_on`, built only by `get_wake_tool`, run only through `build_executor` as the agent **`jarvis`**. It returns a short status ("woke", "muted", "quit", a limit), the score as a number, and never audio or text.
* The post-trigger recording reuses **`microphone_listen`** (`sensor:microphone`), and speaking reuses **`speak`** (`audio:play`), each with its own exact grant **under the agent `jarvis`**. The owner writes all three by hand:

```json
{"agent_id": "jarvis", "grants": [
  {"capability": "sensor:microphone_always_on", "pattern": "wake_listen"},
  {"capability": "sensor:microphone", "pattern": "microphone_listen"},
  {"capability": "audio:play", "pattern": "speak"}
]}
```

A grant for agent `voice` (the push-to-talk chat) does not count for `jarvis`. Without the `speak` grant the loop prints answers and stays silent.

Tests (planned): the capability is in the vocabulary as a sensor; `*` and wildcard grants for it are refused and make the file invalid; `sensor:*` does not cover it; `wake_listen` declares exactly it; without the grant the tool is denied and the denial is logged, and the microphone never opens; a `voice` grant does not count.

## 4. Limits and behaviour

* **Foreground only**: `steel jarvis` runs in the terminal the owner started; without a terminal (stdin or stderr not a TTY) it refuses to start.
* **Session limit**: `STEEL_JARVIS_MAX_MINUTES`, default **60**, at most **240** (a whole number from 1 to 240; anything else is reported and 60 is used). When it is reached the microphone closes and the loop ends with "Session limit reached; start steel jarvis again to keep listening."
* **Idle limit**: `STEEL_JARVIS_IDLE_MINUTES`, default **20** (1 to 240), without a wake word: the loop ends.
* **Trigger rate limit**: `STEEL_JARVIS_MAX_WAKES_PER_HOUR`, default **30** (1 to 120): more wake triggers than that in the last 60 minutes ends the loop with a message.
* **Detection threshold**: `STEEL_JARVIS_THRESHOLD`, default **0.5** (openWakeWord's usual value), from 0.3 to 0.95; higher means fewer false triggers and more missed ones. *Not asked for; added so the owner can tune false triggers without a code change.*
* **Keys while listening** (read without Enter): **`m`** mutes: the listening `arecord` process ends and the window goes; `m` again unmutes, which shows the indicators again first and then opens a new `arecord`. **`q`** quits. Ctrl-C ends the loop. While muted, the session limit still counts; the idle limit does not.
* **One request at a time**: while recording, thinking and speaking, the listening microphone is closed (section 5).

## 5. Echo

**The listening microphone is closed while STEEL records the request, thinks and speaks.** It is reopened only after the reply's player (`aplay`) **has exited**, plus **0.5 s**. The signal is the player process's own exit (its `wait()` returned), recorded as a time by STEEL's player wrapper, not a computation from bytes and sample rate. So STEEL cannot wake itself, and its own voice is never transcribed.

* **Interrupted reply** (Enter, a typed line, Ctrl-C while speaking): the player is killed and waited for; its exit time is the signal.
* **Dead engine** (Piper died mid-reply): the reply ends (docs/SPEAK.md 6); the player is told the audio is over and plays what it holds, then exits; its exit time is the signal.
* **No player at all** (nothing to say, no `speak` grant, `--no-speak`, the engine failed before any sound): the 0.5 s count starts when the speak step returned.
* **A player whose exit cannot be confirmed** (killed but `wait()` timed out): the loop does not reopen the microphone and ends with a message, rather than listen while something may still be playing.

**Barge-in (interrupting STEEL by voice) is NOT implemented**: it would need the microphone open while STEEL speaks, which means hearing STEEL itself and needs acoustic echo cancellation (docs/SPEAK.md section 7). Enter, a typed line and Ctrl-C still stop a reply.

## 6. What the loop does with a request

1. **Wake word detected**: the listening microphone closes, the buffers are zeroed, the terminal shows `◆ HEARD WAKE WORD`.
2. **Recording**: `microphone_listen` (agent `jarvis`) records the request with its REC line, the guard window says RECORDING, silence or Enter ends it, at most `STEEL_VOICE_MAX_SECONDS` (default 30). The wake word itself is the start key (the tool's own start-key wait is satisfied by the trigger). It needs the voice consent too.
3. **Transcription** on this computer with Whisper **base** by default (`STEEL_JARVIS_STT_MODEL`: `base` or `small`, anything else is reported and `base` is used), for speed (docs/VOICE.md section 4: base 1.8 to 1.9 s for a sentence on this machine, small slower).
4. **The transcript is shown with a countdown**: `STEEL_JARVIS_CONFIRM_SECONDS`, default **3** (0 to 10; 0 sends at once). Esc cancels; Enter sends at once. An empty transcript or one with no words (silence, noise) is cancelled. Nothing cancelled is sent or kept.
5. **The model**: the transcript goes to the **same `ChatSession` class as typed lines**: the same system prompt, the same guardrailed non-streaming `generate`, the same history trimming, `STEEL_OLLAMA_KEEP_ALIVE`, `STEEL_OLLAMA_NUM_CTX` (4096), and the start-up warm-up.
6. **The answer** is printed and spoken through the persistent Piper engines (`STEEL_SPEAK_KEEP_VOICES`, docs/SPEAK.md 6), as agent `jarvis`, with the SPEAKING line; `--no-speak` prints only. Speaking is on only if the `speak` grant for `jarvis` exists.
7. **Echo pause** (section 5), then listening again.

## 7. Memory and other people's voices

* **Wake transcripts are NOT sent to the fact extractor by default.** The speaker cannot be verified, and a guest could say "remember that the owner...". Only with `steel jarvis --remember-voice` are they treated like typed lines (the extractor then reads the transcript, never the reply).
* The loop never saves room notes, camera descriptions or tool data, and never writes a note.

## 8. Tools by voice: never

**Rule: nothing spoken is ever run.** A transcript is plain text for the model. A spoken "/look", "slash search", "run", "open the camera" is sent to the model as words (after the countdown), and nothing parses it: slash commands and the camera stay typed-only, in `steel chat`. The loop has no slash-command handler, and the model still cannot call a tool (docs/INJECTION.md). Tests (planned): a transcript "/look" reaches the model as text and no camera or slash code runs; the loop module has no reference to the slash handler.

## 9. Prompt injection by voice

A voice from a television, a phone on speaker or another person is **untrusted text** like any other: it reaches the model only after the countdown, as a user message, never as an instruction to STEEL itself, and the model cannot run anything. It can still mislead the model's answer, and the answer is spoken aloud. docs/INJECTION.md gets a voice-channel section.

## 10. Memory budget

`steel jarvis check` and the start of the loop read `/proc/meminfo` with `steel/meminfo.py`. If less than **2 GiB** is available, or more than **70 %** of the swap is in use, they print a plain warning, and the loop asks for a typed `yes` before it continues (`check` only warns). The 70 % is the owner's figure for this gate; `steel doctor` keeps its own 50 % warning. Tests with fake meminfo.

## 11. Speech recognition

`STEEL_JARVIS_STT_MODEL`, `base` (default) or `small`, validated; the model files are the ones `steel voice` already uses (`steel_models/faster-whisper-*`).

## 12. Latency budget

From earlier measurements on this machine (docs/VOICE.md, docs/LATENCY.md, docs/SPEAK.md): detection itself is a fraction of a frame (to be measured, section 1.2); the request recording ends about `STEEL_VOICE_SILENCE_SECONDS` after the speaker stops; Whisper base about **1 to 2 s**; the model's first token **1 to 2 s** after the first turn (5 s or more on the first turn, which reads the whole system prompt again, docs/LATENCY.md 10.2); generation as long as the answer; first audio **0.5 to 1.6 s** after the reply when the voice is warm. **The first turn after a cold start is slower** (model load and the full prompt), and so is the first reply in a new language (voice load, 3 to 4 s). The countdown (3 s by default) adds its own time on purpose.

With `--timings` the loop prints the existing timing line after each turn, plus one figure: **wake to first audio**, from the detection to the first audio reaching the player.

## 13. Not implemented, and why

* **Speaker recognition** (only the owner's voice counts): it would mean storing a voiceprint, a biometric record of the owner, and still would not keep guests from being heard. Not built.
* **Barge-in**: section 5.
* **A background service or daemon, start at login, listening without a foreground terminal**: forbidden by the owner's decision, which this design keeps.
* **Custom wake-word training ("hey steel")**: section 1.1; needs training data, compute and a licence review.
* **Remote or mobile triggering** (a phone, the network, a web page): STEEL has no network server and this phase adds none.
* **Tools by voice**: section 8, never.
* **Cloud speech services**: never.

## 14. Processes and how they talk

```
terminal: steel jarvis (the loop, foreground)
 ├─ guard: python -I -m steel.jarvis.guard   (Tk window + watchdog; reads lines on stdin)
 ├─ setpriv --pdeathsig KILL -- arecord ...   (listening; only between SHOWN and HIDE)
 ├─ setpriv --pdeathsig KILL -- arecord ...   (the request; only during RECORDING)
 ├─ setpriv --pdeathsig KILL -- unshare --net ... piper   (speaking, docs/SPEAK.md)
 └─ aplay                                      (speaking)
```

Loop to guard, one line each: `SHOW <state>` (answer `SHOWN` once the window is mapped, or `FAILED`), `STATE <state>`, `HIDE`, `BEAT`. The guard never sees audio or text of the owner's, only the state words. Its watchdog and its `/proc` check act on process ids only.

## 15. Licensing summary

| Component | Licence | In this repository? |
| --- | --- | --- |
| STEEL, including the ported openWakeWord feature code | Apache-2.0 (the port keeps David Scripka's copyright line; NOTICE and docs/PROVENANCE.md say so) | yes |
| openWakeWord model files (`melspectrogram.onnx`, `embedding_model.onnx`, `hey_jarvis_v0.1.onnx`) | CC BY-NC-SA 4.0 (non-commercial) | no: downloaded by `scripts/wake_setup.py` |
| onnxruntime | MIT | no: already installed |
| Tk / tkinter | Tcl/Tk licence (BSD-style), part of Python | no |

## 16. Evidence

To be written with the code: one row per claim, each citing the test that proves it, checked by an evidence test like docs/SPEAK.md section 10.
