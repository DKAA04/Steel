"""Measure the wake-word engine on SYNTHETIC audio (docs/JARVIS.md section 1.2).

    unshare --user --map-root-user --net python scripts/wake_bench.py

Run by hand, once, to fill in docs/JARVIS.md section 1.2; never by pytest. No
microphone, speaker or window: the audio is generated here (silence, white
noise, a tone sweep, speech-like bursts), 60 s of each. It reads the pinned
model files from ~/.openjarvis/steel_models/wake (read only) and writes nothing.

It reports the onnxruntime import time, the engine start-up time (three ONNX
sessions plus the noise warm-up), CPU time and wall time per second of audio
on one thread, resident memory before and after, and the highest score each
kind of audio reached (a tiny sample, not a false-trigger rate: that needs
real rooms and scripts/jarvis_check.py).

Network: telemetry_off() runs before onnxruntime is imported; a Python audit
hook records every socket, connect and name lookup made from Python; at the end
every open file descriptor is checked for sockets. Run it inside
``unshare --user --map-root-user --net`` so that native code cannot reach the
network either; the hook cannot see native code, the namespace can stop it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

SECONDS = 60
FRAME_SAMPLES = 1280
RATE = 16000

network_events: list[str] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith(("socket.", "http.", "urllib.")) or event in (
        "socket.getaddrinfo",
        "socket.gethostbyname",
    ):
        network_events.append(event)


def rss_mib() -> tuple[float, float]:
    values = {"VmRSS": 0.0, "VmHWM": 0.0}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in values:
            values[key] = int(rest.split()[0]) / 1024
    return values["VmRSS"], values["VmHWM"]


def open_sockets() -> set[str]:
    """The sockets this process holds now (as /proc names, e.g. socket:[123])."""
    found = set()
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith("socket:"):
            found.add(target)
    return found


def in_network_namespace_without_network() -> bool:
    """True if this process sees only the loopback interface (a fresh network
    namespace). /proc/self/net reflects the namespace; /sys/class/net may not."""
    try:
        lines = Path("/proc/self/net/dev").read_text().splitlines()[2:]
    except OSError:
        return False
    return [line.split(":")[0].strip() for line in lines] == ["lo"]


def synthetic(kind: str, np: Any) -> Any:
    rng = np.random.default_rng(12345)
    n = SECONDS * RATE
    t = np.arange(n) / RATE
    if kind == "silence":
        audio = np.zeros(n)
    elif kind == "white noise":
        audio = rng.normal(0, 3000, n)
    elif kind == "tone sweep":
        freq = 100 + 3900 * (t / SECONDS)  # 100 Hz to 4 kHz
        audio = 8000 * np.sin(2 * np.pi * np.cumsum(freq) / RATE)
    else:  # speech-like: 300 ms bursts of harmonics with noise, 200 ms gaps
        audio = np.zeros(n)
        for start in range(0, n, int(0.5 * RATE)):
            end = min(start + int(0.3 * RATE), n)
            seg = t[start:end]
            f0 = 120 + 80 * rng.random()
            burst = sum(np.sin(2 * np.pi * f0 * h * seg) / h for h in range(1, 8))
            audio[start:end] = 6000 * burst + rng.normal(0, 500, end - start)
    return np.clip(audio, -32768, 32767).astype("<i2")


def main() -> int:
    sys.addaudithook(_audit)
    sockets_before = open_sockets()  # inherited ones do not count
    queue = Path.home() / ".cache" / "Microsoft" / "DeveloperTools" / ".onnxruntime"
    queue_before = queue.exists()

    from steel.offline import telemetry_off

    telemetry_off()  # before anything can import onnxruntime
    rss0, _ = rss_mib()
    started = time.monotonic()
    import onnxruntime

    import_s = time.monotonic() - started
    import numpy as np

    from steel.home import steel_home
    from steel.jarvis.models import models_ready, wake_dir
    from steel.jarvis.wakeword import FRAME_BYTES, OpenWakeWord

    home = steel_home()
    if not models_ready(home):
        print("wake-word models missing or wrong: python scripts/wake_setup.py")
        return 1
    started = time.monotonic()
    engine = OpenWakeWord(wake_dir(home))
    startup_s = time.monotonic() - started
    rss1, _ = rss_mib()

    print(f"onnxruntime {onnxruntime.__version__}, numpy {np.__version__}")
    print(
        f"network namespace without network: {in_network_namespace_without_network()}"
    )
    print(f"onnxruntime import: {import_s:.2f} s")
    print(f"engine start-up (3 sessions + warm-up): {startup_s:.2f} s")
    print(f"resident memory: {rss0:.0f} MiB before, {rss1:.0f} MiB after loading")
    for kind in ("silence", "white noise", "tone sweep", "speech-like"):
        audio = synthetic(kind, np).tobytes()
        engine.reset()
        frames = len(audio) // FRAME_BYTES
        best = 0.0
        cpu0, wall0 = time.process_time(), time.monotonic()
        for i in range(frames):
            best = max(
                best, engine.score(audio[i * FRAME_BYTES : (i + 1) * FRAME_BYTES])
            )
        cpu, wall = time.process_time() - cpu0, time.monotonic() - wall0
        audio_s = frames * FRAME_SAMPLES / RATE
        print(
            f"{kind:12s}: {cpu / audio_s * 1000:6.1f} ms CPU and "
            f"{wall / audio_s * 1000:6.1f} ms wall per second of audio, "
            f"highest score {best:.4f} ({frames} frames)"
        )
    engine.wipe()
    rss2, peak = rss_mib()
    print(f"resident memory after the run: {rss2:.0f} MiB (peak {peak:.0f} MiB)")
    print(f"Python network events: {sorted(set(network_events)) or 'none'}")
    new_sockets = open_sockets() - sockets_before
    print(
        f"sockets opened during the run and still open: {len(new_sockets)} "
        f"(inherited and ignored: {len(sockets_before)})"
    )
    print(
        f"telemetry queue {queue}: "
        f"{'existed before' if queue_before else 'absent before'}, "
        f"{'EXISTS after' if queue.exists() else 'absent after'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
