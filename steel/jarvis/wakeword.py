# Ported from openWakeWord (https://github.com/dscripka/openWakeWord),
# openwakeword/utils.py, class AudioFeatures, and openwakeword/model.py,
# Model.predict, at version 0.6.0. Copyright 2022 David Scripka.
# Licensed under the Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
# Changed for STEEL (docs/PROVENANCE.md): only the streaming path for one model,
# 80 ms frames only, much smaller fixed buffers, the buffers can be zeroed, the
# sessions are injectable for tests, and there is no downloader.
# Compared as text with upstream v0.6.0 (commit c8ef6912c5feccf1037b852d9bc6c7ed644135ba)
# on 2026-10-26: frame size and sample rate, the mel input and its x/10 + 2
# transform, the raw context (1,280 on the first frame after a reset, then
# 480 + 1,280), the 76-frame window with stride 8, the embedding input shape,
# 16 embeddings into the classifier, the first 5 scores forced to 0, and the
# output handling all match. Deviations: the initial feature history comes from
# seeded noise (default_rng(0)) instead of unseeded noise; the 16 is fixed, not
# read from the model; no Speex noise suppression and no voice-activity
# detection (both are off by default upstream). Not compared: anything else.
"""The wake-word engine: openWakeWord's three ONNX models on onnxruntime.

Each 80 ms frame (1280 samples of 16 kHz 16-bit mono) becomes mel frames, the
last 76 mel frames one speech embedding, and the last 16 embeddings one score
from 0 to 1 for "hey jarvis" (docs/JARVIS.md sections 1.1 and 2).

Nothing here downloads, writes or keeps audio beyond the fixed buffers below,
and onnxruntime is imported only after its telemetry is switched off.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

FRAME_SAMPLES = 1280  # 80 ms at 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2
_CONTEXT_SAMPLES = 480  # 30 ms of earlier audio the mel model needs (upstream: 160*3)
# The only raw audio kept: the new frame and its context (110 ms, 3,520 bytes).
RAW_RING_SAMPLES = FRAME_SAMPLES + _CONTEXT_SAMPLES
MEL_WINDOW = 76  # mel frames per embedding (about 0.76 s)
MEL_BINS = 32
EMBEDDINGS = 16  # embeddings the "hey jarvis" model reads (about 1.3 s)
EMBEDDING_SIZE = 96
_WARMUP_PREDICTIONS = 5  # upstream forces the first 5 scores to 0
_NOISE_SECONDS = 4  # upstream fills the feature history from 4 s of noise

Session = Callable[[Any], Any]  # numpy array in, model output out


class WakeEngine(Protocol):
    def score(self, frame: bytes) -> float:
        """The wake-word score for one 80 ms frame (FRAME_BYTES of PCM)."""

    def reset(self) -> None:
        """Zero every buffer and start afresh (after a detection or a pause)."""

    def wipe(self) -> None:
        """Zero every buffer (best effort; at shutdown)."""


def _load_sessions(
    directory: Path, wake_model: str
) -> tuple[Session, Session, Session]:
    """Open the three ONNX models. Telemetry is off before onnxruntime loads."""
    from steel.offline import telemetry_off

    telemetry_off()  # must come first: onnxruntime reads it when it starts
    ort = importlib.import_module("onnxruntime")

    def session(name: str, input_name: str | None = None) -> Session:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1  # one core is plenty, and leaves the rest
        options.inter_op_num_threads = 1
        sess = ort.InferenceSession(
            str(directory / name),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        key = input_name or sess.get_inputs()[0].name
        return lambda x: sess.run(None, {key: x})

    return (
        session("melspectrogram.onnx", "input"),
        session("embedding_model.onnx", "input_1"),
        session(wake_model),
    )


class OpenWakeWord:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        wake_model: str = "hey_jarvis_v0.1.onnx",
        sessions: tuple[Session, Session, Session] | None = None,
    ) -> None:
        # Imported by name: numpy's type stubs need Python 3.12, mypy checks 3.11.
        np = importlib.import_module("numpy")
        self._np = np
        if sessions is None:
            if directory is None:
                raise ValueError("the model directory is needed")
            sessions = _load_sessions(directory, wake_model)
        self._mel_model, self._embedding_model, self._wake_model = sessions
        self._raw = np.zeros(RAW_RING_SAMPLES, dtype=np.int16)
        self._mel = np.ones((MEL_WINDOW, MEL_BINS), dtype=np.float32)
        self._features = np.zeros((EMBEDDINGS, EMBEDDING_SIZE), dtype=np.float32)
        self._scored = 0
        self._fresh = True  # no real audio yet: the first frame has no context
        self.reset()

    # --- buffers -------------------------------------------------------------

    @property
    def buffer_sizes(self) -> dict[str, int]:
        """What is held now, for the tests and docs/JARVIS.md section 2."""
        return {
            "raw_samples": int(self._raw.shape[0]),
            "mel_frames": int(self._mel.shape[0]),
            "embeddings": int(self._features.shape[0]),
        }

    def wipe(self) -> None:
        self._raw[:] = 0
        self._mel[:] = 0
        self._features[:] = 0
        self._scored = 0
        self._fresh = True

    def reset(self) -> None:
        """Zero everything, then refill the feature history as upstream does
        (embeddings of seeded noise), so a score is meaningful from the start."""
        np = self._np
        self.wipe()
        self._mel[:] = 1.0
        noise = np.random.default_rng(0).integers(
            -1000, 1000, 16000 * _NOISE_SECONDS, dtype=np.int16
        )
        mel = self._melspectrogram(noise)
        windows = [
            mel[i : i + MEL_WINDOW]
            for i in range(0, mel.shape[0], 8)
            if mel[i : i + MEL_WINDOW].shape[0] == MEL_WINDOW
        ]
        batch = np.array(windows, dtype=np.float32)[..., None]
        embeddings = self._embed(batch)
        self._features[:] = embeddings[-EMBEDDINGS:]
        self._scored = 0
        self._fresh = True  # the noise above is not audio that was heard

    # --- the pipeline --------------------------------------------------------

    def _melspectrogram(self, samples: Any) -> Any:
        np = self._np
        out = self._mel_model(samples[None, :].astype(np.float32))
        spec = np.squeeze(out[0])
        return (spec / 10 + 2).astype(np.float32)  # upstream's transform

    def _embed(self, windows: Any) -> Any:
        np = self._np
        out = self._embedding_model(windows)
        return np.asarray(out[0], dtype=np.float32).reshape(-1, EMBEDDING_SIZE)

    def score(self, frame: bytes) -> float:
        np = self._np
        if len(frame) != FRAME_BYTES:
            raise ValueError("a frame is exactly 80 ms (2,560 bytes)")
        samples = np.frombuffer(frame, dtype="<i2")
        # Shift the ring: keep the 30 ms context, then the new frame.
        self._raw[:_CONTEXT_SAMPLES] = self._raw[-_CONTEXT_SAMPLES:]
        self._raw[_CONTEXT_SAMPLES:] = samples
        if self._fresh:
            # The first frame after a reset has no earlier audio: like
            # upstream (whose raw buffer is cleared by reset), the mel
            # spectrogram sees only the 1,280 real samples, not 480 zeros
            # before them. From the second frame on it is 480 + 1,280.
            mel = self._melspectrogram(self._raw[_CONTEXT_SAMPLES:])
            self._fresh = False
        else:
            mel = self._melspectrogram(self._raw)
        new = mel.reshape(-1, MEL_BINS)
        self._mel = np.vstack((self._mel, new))[-MEL_WINDOW:]
        embedding = self._embed(self._mel[None, :, :, None])
        self._features = np.vstack((self._features, embedding))[-EMBEDDINGS:]
        result = self._wake_model(self._features[None, :, :].astype(np.float32))
        value = float(np.asarray(result[0]).reshape(-1)[0])
        self._scored += 1
        if self._scored <= _WARMUP_PREDICTIONS:
            return 0.0
        return value
