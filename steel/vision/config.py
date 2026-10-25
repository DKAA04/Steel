"""Vision settings from environment variables, with safe fallbacks.

A typo must never pick a path or flag the owner did not mean, so every value is
validated and falls back to its default instead of failing or being used as given.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MODEL = "qwen2.5vl:3b"
DEFAULT_DEVICE = "/dev/video0"
MAX_QUESTION_CHARS = 200
MAX_DESCRIPTION_CHARS = 1500
# Room notes live at most a week, like every note (steel/notes.py).
DEFAULT_SAVE_HOURS = 24.0
MAX_SAVE_HOURS = 168.0
# Frames ffmpeg drops before keeping one, while the camera's auto-exposure
# settles. 6 gave an all-black first picture on the owner's camera; 20 is about
# 0.7 s at 30 frames per second. Override with $STEEL_CAMERA_WARMUP_FRAMES.
DEFAULT_WARMUP_FRAMES = 20
MAX_WARMUP_FRAMES = 60
# A black picture is retried ONCE with this many more warm-up frames.
RETRY_EXTRA_FRAMES = 40
# Mean brightness (0-255, over every red, green and blue byte) at or below which
# a picture counts as black: about 3 percent. A dim room is usually well above
# it; a frame taken before the exposure settled is close to 0.
BLACK_MEAN_BRIGHTNESS = 8.0

# A video node and nothing else: no other path, no option.
_DEVICE_RE = re.compile(r"/dev/video[0-9]{1,2}")
# An Ollama model name such as "qwen2.5vl:3b". It starts with a letter or digit.
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}")


@dataclass(frozen=True)
class VisionConfig:
    device: str = DEFAULT_DEVICE
    model: str = DEFAULT_MODEL
    capture_size: tuple[int, int] = (640, 480)  # asked of the camera
    max_side: int = 448  # longest side of the picture the model sees (multiple of 28)
    warmup_frames: int = DEFAULT_WARMUP_FRAMES  # dropped while the exposure settles
    start_wait_seconds: float = 60.0  # how long ``steel look`` waits for Enter
    capture_timeout: float = 10.0  # ffmpeg is killed after this many seconds
    model_timeout: float = 240.0  # the 3B model takes 100 to 120 s here
    num_ctx: int = 4096  # context window asked of Ollama (its default here is 16384)
    max_tokens: int = 300


def vision_config(env: Mapping[str, str] | None = None) -> VisionConfig:
    """Settings from $STEEL_CAMERA_DEVICE, $STEEL_VISION_MODEL and
    $STEEL_CAMERA_WARMUP_FRAMES."""
    env = os.environ if env is None else env
    base = VisionConfig()
    device = env.get("STEEL_CAMERA_DEVICE", "").strip()
    model = env.get("STEEL_VISION_MODEL", "").strip()
    return VisionConfig(
        device=device if _DEVICE_RE.fullmatch(device) else base.device,
        model=model if _MODEL_RE.fullmatch(model) and ".." not in model else base.model,
        warmup_frames=_warmup(env.get("STEEL_CAMERA_WARMUP_FRAMES", "")),
    )


def _warmup(raw: str) -> int:
    """A whole number from 0 to MAX_WARMUP_FRAMES, else the default."""
    raw = raw.strip()
    if not raw.isdigit() or not 0 <= int(raw) <= MAX_WARMUP_FRAMES:
        return DEFAULT_WARMUP_FRAMES
    return int(raw)
