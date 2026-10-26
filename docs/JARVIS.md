# The Jarvis loop: a hands-free, local wake word (Phase 5c)

**Status: IMPLEMENTED and tested with fakes (2026-10-26); NOT yet run live by the owner.** `steel jarvis` exists (`steel/jarvis/`, `steel/tools/__init__.py:get_wake_tool`), and section 16 lists, per claim, the test that proves it. Every test uses fakes or stand-in processes: no test opens a microphone, a speaker, a window or a model. **What only the owner's live run can show** (`scripts/jarvis_check.py`): that the real model hears a real "hey jarvis", how often it triggers falsely in a real room, that the red window appears on the real desktop and that the real microphone closes when it should. The design (sections 1 to 15) was written and committed **before** any wake-word code, before any wake-word model was downloaded, and before any "never" rule in CLAUDE.md, SECURITY.md, ARCHITECTURE.md or docs/VOICE.md was changed (commits `b602a74`, then `a23c0f2`, then the code in `73b0f88`).

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
* **Model files**, from the openWakeWord GitHub release **v0.5.1** (published 2023-09-02; the three assets were uploaded again on 2023-10-09). Their sizes and SHA-256 are pinned in `steel/jarvis/models.py`; `scripts/wake_setup.py` keeps a download only if it matches, and `steel jarvis check`, the start of the loop and `wake_listen` hash all three again (3.7 MB) every time:

  | File | Size (bytes) | Licence |
  | --- | --- | --- |
  | `melspectrogram.onnx` | 1,087,958 | part of the openWakeWord release; the README gives no separate licence (treated as the release's CC BY-NC-SA 4.0 to be safe) |
  | `embedding_model.onnx` | 1,326,578 | as above (re-implementation of Google's Apache-2.0 speech embedding) |
  | `hey_jarvis_v0.1.onnx` | 1,271,370 | CC BY-NC-SA 4.0 |

* **What the pins prove, and what they do not.** GitHub publishes no checksum for these three files: its release metadata (read 2026-10-26) has no `digest` for them, because GitHub records digests only for assets uploaded since 2025, and the author's Hugging Face repositories (`davidscripka/openwakeword`, the `davidscripka/openWakeWord` Space) do not contain them. So the SHA-256 values were recorded at the first download on 2026-10-25 (**trust on first use**), after the sizes matched GitHub's listing byte for byte. They prove that the files STEEL loads are exactly the ones downloaded then, over HTTPS, from the GitHub release, and that nothing has changed them since. They do **not** prove that those files are the ones the author first published, or that the files are harmless; nobody independent has vouched for these bytes.
* **onnxruntime telemetry:** the wake engine imports onnxruntime only inside its constructor, after `steel.offline.telemetry_off()`; so do `scripts/jarvis_check.py` and `scripts/wake_bench.py`. `tests/test_telemetry_guard.py` checks every loader in STEEL and its scripts, structurally and by running it against a fake onnxruntime. (On 2026-10-25 the assistant imported onnxruntime once *without* the switch while checking which packages exist; that recreated `~/.cache/Microsoft/DeveloperTools/.onnxruntime/`. `steel doctor` warns about that folder. The owner deleted it; the measurement in section 1.2 ran with the switch and the folder was still absent afterwards.)
* **What the licence means for STEEL.** STEEL's code, including the port, stays Apache-2.0. The model files are downloaded by the owner's setup script onto the owner's machine and are not in this repository. **Using them is fine for personal, non-commercial use.** Any commercial use of STEEL with these model files (selling it, using it in paid client work) is not allowed by CC BY-NC-SA 4.0; that would need another model: microWakeWord's Apache-2.0 "hey jarvis" (which needs a TFLite runtime, a later decision), or a model trained on data whose licence allows it.
* **A custom "hey steel" model** would take openWakeWord's training notebook ("<1 hour" in Colab, README), thousands of synthetic clips from a text-to-speech engine, negative audio, and ideally a GPU; the resulting model's licence follows the training data. Not done (section 13).

### 1.2 Measured on this machine

Measured on 2026-10-26 by the assistant with `scripts/wake_bench.py`, run by hand (not by pytest), with **synthetic audio only** (no microphone): 60 s each of silence, white noise, a 100 Hz to 4 kHz tone sweep and speech-like bursts of harmonics. Machine: AMD Ryzen 5 5500U (6 cores, 12 threads), 14.5 GiB RAM; onnxruntime 1.31.0, numpy 2.5.4; one thread per ONNX session. It ran three times (the first two runs exposed mistakes in the script's own network checks, which were fixed); the figures below are from the last run and the three agreed within 3 ms.

| Measure | Result |
| --- | --- |
| Import of onnxruntime | 0.14 to 0.19 s |
| Engine start-up (three sessions and the 4 s noise warm-up) | 0.17 s |
| CPU time per second of audio | 36.0 to 36.6 ms (about 3.6 % of one core), the same for all four kinds of audio; wall time equal |
| Resident memory | 12 MiB before onnxruntime, 99 MiB after loading the engine, 126 MiB after the run (peak 153 MiB) |
| Highest score (threshold 0.5) | silence 0.0000, tone sweep 0.0003, speech-like 0.0001, **white noise 0.3153** |
| Network | run inside `unshare --user --map-root-user --net` (a namespace with only loopback); a Python audit hook saw no socket, connect or name lookup; no socket was opened during the run |
| Telemetry queue | absent before, absent after |

What this does **not** show: anything about a real voice, a real room or a real false-trigger rate. Four minutes of synthetic sound is a tiny sample; the white-noise score of 0.32 is a warning that `STEEL_JARVIS_THRESHOLD` near its lowest allowed value (0.3) could trigger on steady noise. **The false-trigger rate is the owner's to measure** with `scripts/jarvis_check.py` (it asks after every trigger whether it was real, and reports false triggers per hour). The audit hook sees only Python code; native code inside onnxruntime is stopped by the namespace, not observed.

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
* Tested (section 16): no wake consent → no microphone, even with the voice consent; consent written only after a typed yes; the text says "ALWAYS ON".

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
* Tested (section 16): no `SHOWN` → no microphone; the terminal line is written before the microphone opens; the guard kills an `arecord` child that runs while the window is hidden; the guard hides the window when no `arecord` child is left.

### 3c. An off switch that works while STEEL is stuck

Four independent ways, none of which needs the loop's cooperation:

1. **`steel jarvis stop`**, a separate command run from any other terminal: reads the pidfile `steel_jarvis.pid` (mode 600: the loop's and the guard's process ids plus each process's start time from `/proc/PID/stat`, so a reused process id is never killed), then sends **SIGKILL** to every `arecord` whose parent is the loop, to the loop and to the guard, and removes the pidfile.
2. **The kernel:** every `arecord` STEEL starts in the loop is started through `setpriv --pdeathsig KILL` (as Piper is), so it dies when the thread that started it dies, even on SIGKILL. Both microphones (listening and the post-trigger recording) are started that way in the loop.
3. **The guard's watchdog:** while a listening microphone is open, the loop sends a heartbeat to the guard at least once a second from the frame loop itself (not from a separate thread, which would keep beating while the frame loop hangs). No heartbeat for **10 s** with an `arecord` child alive: the guard kills the `arecord` children and then the loop. When the guard's input ends (the loop died), it kills any `arecord` it has seen and exits.
4. **By hand:** `steel jarvis check` prints the fallback in plain words: `pkill -KILL -f 'steel jarvis'` and, if a microphone is still on, `pkill -KILL -x arecord` (which ends every `arecord` of this user, not only STEEL's).

Also: `q` quits, `m` mutes (section 4), Ctrl-C ends the loop.

Tested (section 16): `steel jarvis stop` kills a deliberately hung fake loop (it ignores SIGTERM) and its stand-in `arecord` child; a reused process id with another start time is not killed; the watchdog kills the microphone when heartbeats stop (fake clock) and a real stand-in when they stop for real; the guard kills what it has seen when its input ends; no `arecord` survives a SIGKILL of the loop (real stand-in processes).

### 3d. Nothing heard before the trigger is kept

Section 2: frames go only to the wake engine's fixed-size ring buffers; never written, never transcribed, never logged; zeroed when listening ends; the pre-roll is never passed to speech recognition, because the request is a separate recording. The decision log (`steel_policy_audit.jsonl`) holds names only: agent, capability, decision and reason; never a transcript, audio or a score.

Tested (section 16): the transcriber receives only the post-trigger recording's audio (different bytes from the listening frames); the ring buffers never exceed their sizes; nothing is written to the temporary home or the temporary directory during a whole simulated session; the buffers are zeroed after listening; the audit log holds no transcript text.

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

Tested (section 16): the capability is in the vocabulary as a sensor; `*` and wildcard grants for it are refused and make the file invalid; `sensor:*` does not cover it; `wake_listen` declares exactly it; without the grant the tool is denied and the denial is logged, and the microphone never opens; a `voice` grant does not count.

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

**Rule: nothing spoken is ever run.** A transcript is plain text for the model. A spoken "/look", "slash search", "run", "open the camera" is sent to the model as words (after the countdown), and nothing parses it: slash commands and the camera stay typed-only, in `steel chat`. The loop has no slash-command handler, and the model still cannot call a tool (docs/INJECTION.md). Tested (section 16): a transcript "/look" reaches the model as text and no camera or slash code runs; the loop module has no reference to the slash handler.

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

Test files: `tests/test_jarvis_core.py`, `test_jarvis_loop.py`, `test_jarvis_check_script.py`, `test_telemetry_guard.py` and `test_test_timeout.py`; helpers in `jarvis_helpers.py`. Everything runs with fakes (microphone frames, the wake-word engine, Whisper, the guard window, keys, Piper, the player, the model and the clock) and a temporary home. Real processes appear only as stand-ins: a copy of `sleep` named `arecord`, a hung fake loop, and the real guard started with no display. No microphone, speaker, window, model or network is used, and the real `~/.openjarvis` is not read. Every fake that a loop polls fails after a fixed number of calls, and every test phase has a time limit (`tests/conftest.py`): on 2026-10-25 an unfinished countdown test looped forever with a fake clock that never moved, grew about 60 MiB a second and froze the owner's computer. Each cell is `file::Class.test`; `test_jarvis_evidence.py` checks that every cited test exists and every test in these files is cited.

| Claim | Test |
| --- | --- |
| **3a.** Always-on consent is its own file, written only after a typed `yes`, with the voice and camera checks (regular file, the owner's, not writable by others, not a symlink); the push-to-talk consent does not count; the text says ALWAYS ON and is the one quoted in 3a.1 word for word; without it the loop, the tool and the live check open nothing | `test_jarvis_core.py::ConsentTest.test_its_own_file_and_the_push_to_talk_consent_does_not_count`, `test_jarvis_core.py::ConsentTest.test_a_consent_file_others_can_write_or_a_symlink_does_not_count`, `test_jarvis_core.py::ConsentTest.test_the_consent_text_says_always_on_and_names_the_risks`, `test_jarvis_core.py::ConsentTest.test_the_text_in_the_design_document_is_the_same_word_for_word`, `test_jarvis_loop.py::CommandsTest.test_consent_is_recorded_only_after_a_typed_yes`, `test_jarvis_loop.py::StartTest.test_no_always_on_consent_means_the_microphone_never_opens`, `test_jarvis_loop.py::StartTest.test_the_tool_itself_refuses_without_its_consent`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_no_always_on_consent_means_nothing_is_opened` |
| **3b.** The terminal line and the guard's window (answer `SHOWN`) both come before the microphone; the listening source refuses to open without the proof only they make; no terminal or no window means no microphone; the window goes only when no microphone is left, after each listening | `test_jarvis_core.py::IndicatorsTest.test_both_indicators_come_before_the_proof`, `test_jarvis_core.py::IndicatorsTest.test_no_terminal_or_no_window_means_no_proof`, `test_jarvis_core.py::SourceTest.test_without_the_proof_or_setpriv_the_microphone_never_opens`, `test_jarvis_loop.py::IndicatorOrderTest.test_both_indicators_are_up_before_the_microphone_opens`, `test_jarvis_loop.py::IndicatorOrderTest.test_no_window_means_no_microphone`, `test_jarvis_loop.py::IndicatorOrderTest.test_the_window_is_hidden_after_each_listening_and_the_pidfile_removed`, `test_jarvis_core.py::GuardLogicTest.test_shown_is_answered_only_when_the_window_is_up`, `test_jarvis_core.py::GuardLogicTest.test_the_window_goes_only_when_no_microphone_is_left` |
| **3b.** The guard does not trust the loop: a microphone running without the window is killed (also by the real guard process, started with no display so no window can open, which answers `FAILED` and exits when its input ends) | `test_jarvis_core.py::GuardLogicTest.test_a_microphone_running_without_the_window_is_killed`, `test_jarvis_core.py::GuardProcessTest.test_the_real_guard_without_a_display_refuses_and_kills_the_microphone`, `test_jarvis_core.py::GuardProcessTest.test_mic_children_finds_the_real_stand_in_by_name_and_parent` |
| **3c.** Off switches that need nothing from the loop: `steel jarvis stop` kills a hung loop that ignores SIGTERM and its stand-in `arecord`; no `arecord` survives a SIGKILL of the loop (`setpriv --pdeathsig KILL`); a reused process id is never killed; the pidfile is private | `test_jarvis_core.py::StopCommandTest.test_stop_kills_a_hung_loop_and_its_microphone`, `test_jarvis_core.py::StopCommandTest.test_no_microphone_survives_a_sigkill_of_the_loop`, `test_jarvis_core.py::StopCommandTest.test_a_pidfile_naming_a_reused_process_id_kills_nothing`, `test_jarvis_core.py::GuardProcessTest.test_a_reused_process_id_is_never_killed`, `test_jarvis_core.py::StopCommandTest.test_the_pidfile_is_private`, `test_jarvis_core.py::SourceTest.test_the_listening_microphone_dies_with_steel_and_ends_by_itself`, `test_jarvis_core.py::SourceTest.test_the_request_microphone_also_dies_with_steel` |
| **3c.** The watchdog: no heartbeat for its limit while listening kills the microphone and then the loop (fake clock, and a real stand-in process); it is not armed while the request is recorded, which ends by itself; when the loop goes, every microphone it was seen with is killed | `test_jarvis_core.py::GuardLogicTest.test_the_watchdog_kills_the_microphone_then_the_loop_when_beats_stop`, `test_jarvis_core.py::GuardProcessTest.test_the_watchdog_kills_a_real_microphone_when_beats_stop`, `test_jarvis_core.py::GuardLogicTest.test_no_watchdog_while_recording_the_request_which_ends_by_itself`, `test_jarvis_core.py::GuardLogicTest.test_when_the_loop_goes_every_microphone_seen_is_killed` |
| **3c / 4.** `q`, `m` and Ctrl-C close the microphone (Ctrl-C reaches the main thread while the tool listens in the executor's thread); `m` again shows both indicators before a new microphone | `test_jarvis_loop.py::KeysAndLimitsTest.test_q_quits`, `test_jarvis_loop.py::KeysAndLimitsTest.test_m_mutes_closing_the_microphone_and_m_again_reopens_it`, `test_jarvis_loop.py::FailureTest.test_ctrl_c_while_listening_closes_the_microphone` |
| **3d.** Nothing heard before the trigger is kept: the engine's buffers never exceed 110 ms of raw audio, 76 mel frames and 16 embeddings, are zeroed after every listening and at the end; the transcriber receives only the request recorded after the wake word; no file in the home or the temporary directory and no network during a whole session; the audit log holds no transcript | `test_jarvis_core.py::WakeEngineTest.test_buffers_never_grow_beyond_their_documented_sizes`, `test_jarvis_core.py::WakeEngineTest.test_wipe_zeroes_every_buffer_and_reset_starts_afresh`, `test_jarvis_loop.py::TurnTest.test_nothing_before_the_wake_word_reaches_speech_recognition`, `test_jarvis_loop.py::WholeSessionTest.test_no_network_and_no_file_during_a_whole_session`, `test_jarvis_loop.py::TurnTest.test_the_audit_log_holds_no_transcript_text`, `test_jarvis_core.py::SourceHygieneTest.test_no_jarvis_module_writes_audio_or_a_file_except_the_pidfile` |
| **3f.** `sensor:microphone_always_on` is its own sensor: exact tool name only, never a wildcard (`*` or `sensor:*` make the grants file invalid); only the exact grant for agent `jarvis` allows it, a `sensor:microphone` grant or another agent's does not; without it the tool is denied, the denial logged, and nothing opens | `test_jarvis_core.py::CapabilityTest.test_the_always_on_microphone_is_its_own_sensor`, `test_jarvis_core.py::CapabilityTest.test_a_wildcard_or_star_grant_for_it_is_refused`, `test_jarvis_core.py::CapabilityTest.test_a_star_grant_for_it_makes_the_whole_grants_file_invalid`, `test_jarvis_core.py::CapabilityTest.test_only_the_exact_grant_for_agent_jarvis_allows_it`, `test_jarvis_loop.py::StartTest.test_without_the_grant_the_tool_is_denied_logged_and_never_opens`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_no_grant_for_wake_listen_means_nothing_is_opened` |
| **3f.** `get_wake_tool` is the only place that builds `wake_listen`; building it opens nothing and loads no model; the tool declares exactly the one capability | `test_jarvis_core.py::WakeToolBuilderTest.test_nothing_else_in_steel_or_scripts_constructs_wake_listen`, `test_jarvis_core.py::WakeToolBuilderTest.test_building_it_opens_nothing_and_declares_only_its_sensor`, `test_jarvis_core.py::WakeToolBuilderTest.test_building_it_loads_no_model_or_audio_library` |
| **4.** Foreground only (also checked while listening); one loop at a time; the memory gate asks for a typed `yes`; session, idle and wake-rate limits end the loop and close the microphone; the settings are validated and a bad value is reported and replaced by the default | `test_jarvis_loop.py::StartTest.test_only_in_the_foreground_of_a_terminal`, `test_jarvis_loop.py::StartTest.test_going_to_the_background_while_listening_ends_the_loop`, `test_jarvis_loop.py::StartTest.test_a_second_loop_is_refused_while_one_runs`, `test_jarvis_loop.py::StartTest.test_the_memory_gate_needs_a_typed_yes`, `test_jarvis_loop.py::KeysAndLimitsTest.test_the_session_limit_ends_the_loop`, `test_jarvis_loop.py::KeysAndLimitsTest.test_the_idle_limit_ends_the_loop`, `test_jarvis_loop.py::KeysAndLimitsTest.test_too_many_wake_words_in_an_hour_end_the_loop`, `test_jarvis_core.py::LimitsTest.test_session_idle_and_wake_rate_limits`, `test_jarvis_core.py::LimitsTest.test_wakes_older_than_an_hour_no_longer_count`, `test_jarvis_core.py::ConfigTest.test_defaults`, `test_jarvis_core.py::ConfigTest.test_every_setting_is_validated_and_a_bad_value_reported` |
| **5.** Echo: listening resumes only after the player's own exit plus 0.5 s (also after an interrupted reply, a dead engine, or no player); a player whose exit cannot be confirmed keeps the microphone closed and ends the loop | `test_jarvis_core.py::EchoTest.test_listening_waits_for_the_players_exit_plus_half_a_second`, `test_jarvis_core.py::EchoTest.test_an_interrupted_reply_counts_from_the_killed_players_exit`, `test_jarvis_core.py::EchoTest.test_a_dead_engine_still_waits_for_the_player_to_drain_and_exit`, `test_jarvis_core.py::EchoTest.test_with_no_player_the_pause_counts_from_the_end_of_speaking`, `test_jarvis_core.py::EchoTest.test_a_player_that_will_not_exit_keeps_the_microphone_closed`, `test_jarvis_core.py::EchoTest.test_the_tracked_sink_passes_everything_to_the_real_player`, `test_jarvis_loop.py::TurnTest.test_listening_resumes_only_after_the_player_exited_plus_half_a_second`, `test_jarvis_loop.py::TurnTest.test_a_player_whose_exit_cannot_be_confirmed_ends_the_loop` |
| **6.** A request: wake, record (agent `jarvis`, `microphone_listen`, which needs the voice consent and its grant), show with a countdown (Esc cancels, Enter sends, an empty transcript is cancelled), answer through the same chat session, speak as agent `jarvis` only with its `speak` grant, then listen again; `--timings` adds wake to first audio | `test_jarvis_loop.py::TurnTest.test_wake_record_confirm_answer_speak_then_listen_again`, `test_jarvis_loop.py::TurnTest.test_esc_during_the_countdown_cancels_and_nothing_is_sent`, `test_jarvis_loop.py::TurnTest.test_the_countdown_sends_after_its_seconds_or_on_enter`, `test_jarvis_loop.py::TurnTest.test_an_empty_transcript_is_cancelled`, `test_jarvis_loop.py::TurnTest.test_a_mic_denial_for_the_request_ends_the_loop`, `test_jarvis_loop.py::CommandsTest.test_no_speak_grant_means_answers_are_only_printed`, `test_jarvis_loop.py::TurnTest.test_timings_add_the_wake_to_first_audio_figure` |
| **7.** Wake transcripts never reach the fact extractor unless `--remember-voice`; with it only the words (never the reply) do | `test_jarvis_loop.py::TurnTest.test_wake_transcripts_never_reach_the_extractor_by_default`, `test_jarvis_loop.py::TurnTest.test_with_remember_voice_they_do` |
| **8.** Nothing spoken is run: a spoken "/look" reaches the model as the words of the user turn and no slash or camera code runs; the loop's modules do not reference the slash handler, the camera, the memory tools or tool data | `test_jarvis_loop.py::TurnTest.test_a_spoken_slash_command_is_plain_text_never_run`, `test_jarvis_core.py::SourceHygieneTest.test_the_loop_never_reaches_slash_commands_or_the_camera` |
| **1.1.** The engine keeps upstream's numbers (16 embeddings for the classifier, the first 5 scores 0, whole 80 ms frames only); the model files are pinned with size and SHA-256 from one release and count only as regular files with both; no Jarvis module can download, open a connection or use a forbidden audio library | `test_jarvis_core.py::WakeEngineTest.test_the_classifier_sees_16_embeddings_and_the_first_5_scores_are_zero`, `test_jarvis_core.py::WakeEngineTest.test_only_whole_80_ms_frames_are_accepted`, `test_jarvis_core.py::ModelsTest.test_three_files_pinned_with_size_and_sha256_from_one_release`, `test_jarvis_core.py::ModelsTest.test_a_file_counts_only_with_its_size_and_hash_and_not_as_a_link`, `test_jarvis_core.py::SourceHygieneTest.test_no_jarvis_module_can_download_or_open_a_connection`, `test_jarvis_core.py::WakeEngineTest.test_importing_the_jarvis_package_loads_no_audio_or_model_library` |
| **1.1.** onnxruntime telemetry is off before onnxruntime (or faster_whisper) can load, in every loader in STEEL and its scripts (structural scan and a fake library that records the switch), and the `steel` command sets it first | `test_jarvis_core.py::WakeEngineTest.test_onnxruntime_is_imported_only_after_telemetry_is_off`, `test_telemetry_guard.py::StructuralTest.test_no_module_level_import_and_no_openwakeword_package`, `test_telemetry_guard.py::StructuralTest.test_every_loader_turns_telemetry_off_on_an_earlier_line`, `test_telemetry_guard.py::FunctionalTest.test_each_loader_imports_its_library_only_with_the_switch_on`, `test_telemetry_guard.py::FunctionalTest.test_the_steel_command_turns_it_off_before_anything_else`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_engine_is_loaded_only_after_telemetry_is_off`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_it_turns_telemetry_off_and_uses_no_forbidden_audio_library` |
| Failures end the loop with the microphone closed: an engine crash, a microphone that stops, a read error | `test_jarvis_loop.py::FailureTest.test_an_engine_crash_ends_the_loop_cleanly`, `test_jarvis_loop.py::FailureTest.test_a_microphone_that_stops_ends_the_loop_cleanly`, `test_jarvis_loop.py::FailureTest.test_a_read_error_ends_the_loop_cleanly` |
| `steel jarvis check` is read only (starts nothing, writes nothing) and lists what is missing with the example grant; the command line passes its flags to the loop | `test_jarvis_loop.py::CommandsTest.test_check_is_read_only_and_opens_nothing`, `test_jarvis_loop.py::CommandsTest.test_check_shows_what_is_missing`, `test_jarvis_loop.py::CommandsTest.test_the_command_line_reaches_the_loop_with_its_flags` |
| `scripts/jarvis_check.py` (the owner's live check) refuses before opening anything without consent, grant or the foreground; takes 1 to 240 minutes; counts confirmed wakes and false triggers, engine CPU and memory; closes the microphone every time and leaves no file but names-only audit lines; its private pidfile (mode 600) exists while it runs and is removed after a normal end, an error at start-up or while listening, and Ctrl-C (fakes only) | `test_jarvis_check_script.py::JarvisCheckScriptTest.test_not_in_the_foreground_means_nothing_is_opened`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_period_must_be_from_1_to_240_minutes`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_measured_engine_counts_frames_and_cpu`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_a_whole_fake_run_counts_a_false_trigger_and_cleans_up`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_pidfile_is_private_while_it_runs_and_removed_at_the_end`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_pidfile_is_removed_when_starting_up_fails`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_pidfile_is_removed_when_an_error_happens_while_listening`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_the_pidfile_is_removed_after_ctrl_c_and_the_report_is_printed`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_a_tool_error_ends_the_check_cleanly` |
| A runaway test fails instead of hanging: every test phase has a time limit (`tests/conftest.py`, `STEEL_TEST_TIMEOUT`); a spinning loop, a blocking sleep and `except Exception` cannot escape it; nesting and the setting are handled; a real pytest run stops a slow test and goes on | `test_test_timeout.py::DeadlineTest.test_a_spinning_loop_is_stopped`, `test_test_timeout.py::DeadlineTest.test_a_blocking_sleep_is_stopped`, `test_test_timeout.py::DeadlineTest.test_except_exception_cannot_swallow_it`, `test_test_timeout.py::DeadlineTest.test_a_fast_block_is_not_disturbed_and_the_handler_is_put_back`, `test_test_timeout.py::DeadlineTest.test_the_outer_limit_is_put_back_after_an_inner_one`, `test_test_timeout.py::ParseTimeoutTest.test_default_and_valid_values`, `test_test_timeout.py::ParseTimeoutTest.test_unusable_values_are_refused`, `test_test_timeout.py::WholeRunTest.test_a_deliberately_slow_test_fails_with_a_clear_message`, `test_test_timeout.py::WholeRunTest.test_an_invalid_setting_stops_the_run_before_any_test` |
| **3b.** The loop watches the guard: before every read of the microphone it checks that the guard process is alive, and a heartbeat that raises, does not arrive, or an error while asking counts as dead; the microphone is closed at once with no further read, the tool returns `no_indicator` unsuccessfully with a clear message, and the loop prints it, closes the guard and the pidfile, never restarts the guard and ends non-zero; the real guard process killed with SIGKILL while a stand-in microphone streams closes the microphone within 2 s; `scripts/jarvis_check.py` behaves the same and exits 1 | `test_jarvis_loop.py::GuardDeathTest.test_a_guard_that_dies_while_listening_closes_the_microphone_at_once`, `test_jarvis_loop.py::GuardDeathTest.test_the_tool_returns_no_indicator_unsuccessfully`, `test_jarvis_loop.py::GuardDeathTest.test_a_beat_that_raises_counts_as_a_dead_guard`, `test_jarvis_loop.py::GuardDeathTest.test_a_beat_that_does_not_arrive_counts_as_a_dead_guard`, `test_jarvis_loop.py::GuardDeathTest.test_an_error_while_asking_whether_the_guard_lives_counts_as_dead`, `test_jarvis_core.py::GuardDeathProcessTest.test_killing_the_real_guard_closes_the_microphone_within_two_seconds`, `test_jarvis_check_script.py::JarvisCheckScriptTest.test_a_guard_that_dies_while_listening_closes_the_microphone_and_exits_1` |
| **3b.** The guard watches its own window: if the window is destroyed or unmapped for three ticks while it should be showing, or Tk raises, it kills every microphone it has seen and exits with status 1 (a brief flicker and a window not expected do not count) | `test_jarvis_core.py::GuardLogicTest.test_a_window_that_disappears_kills_the_microphones_and_fails`, `test_jarvis_core.py::GuardLogicTest.test_tk_raising_counts_as_a_lost_window`, `test_jarvis_core.py::GuardLogicTest.test_a_brief_flicker_does_not_count`, `test_jarvis_core.py::GuardLogicTest.test_no_window_expected_means_no_loss`, `test_jarvis_core.py::GuardLogicTest.test_main_stops_with_status_1_and_kills_the_microphone_when_tk_raises` |
| **3b / 6.** If the window for the request cannot be shown, no request is recorded (`microphone_listen` is never called and listening does not resume); a guard that dies during the request ends that recording within one chunk and the next listening refuses to start | `test_jarvis_loop.py::GuardDeathTest.test_when_the_recording_window_cannot_be_shown_no_request_is_recorded`, `test_jarvis_loop.py::GuardDeathTest.test_a_guard_that_dies_during_the_request_ends_that_recording` |
| **1.1.** The first frame after a reset (each start of listening, each wake word, each mute, and after a wipe) is computed from its 1,280 real samples only, like upstream; the second frame has 480 samples of real context; every buffer stays at its documented maximum | `test_jarvis_core.py::WakeEngineTest.test_the_first_frame_after_a_reset_has_no_zero_context_like_upstream`, `test_jarvis_core.py::WakeEngineTest.test_the_second_frame_gets_its_context_from_the_real_first_frame`, `test_jarvis_core.py::WakeEngineTest.test_buffers_stay_at_their_maximum_after_the_shorter_first_input` |

**Not covered by any test, and why:** the real model hearing a real "hey jarvis", the false-trigger rate, the real Tk window on the owner's desktop, and the real `arecord` on the real microphone. These need the owner's live run (`scripts/jarvis_check.py`); section 1.2 has synthetic-audio measurements only.
