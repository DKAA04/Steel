"""What the capture needs from a camera. Tests use a fake; the real one is ``camera.py``."""

from __future__ import annotations

from typing import Protocol

from steel.vision.frame import Frame
from steel.vision.indicator import CameraShown


class FrameSource(Protocol):
    """One frame, in RAM only."""

    def open(self, shown: CameraShown) -> None:
        """Open the camera. *shown* is the proof the CAMERA OPEN line is on screen."""

    def read_frame(self) -> Frame:
        """The one frame, blocking. Raises OSError if the camera gives none."""

    def close(self) -> None:
        """Close the camera. Safe to call more than once."""
