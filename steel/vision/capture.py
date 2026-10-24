"""One capture: the only code that opens a FrameSource.

Order, every time: show the CAMERA OPEN line, then open the camera, read ONE
frame, then close it (in a ``finally``) and say so. There is no loop and no way
to ask for a second frame.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from steel.vision.frame import Frame
from steel.vision.indicator import CameraIndicator
from steel.vision.source import FrameSource


@dataclass
class Capture:
    frame: Frame  # the caller must wipe() it
    seconds: float  # how long the camera was open


def capture_one(
    source: FrameSource,
    indicator: CameraIndicator,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Capture:
    """Show the line, open the camera, read one frame, close it. Raises on failure.

    If the line cannot be shown, CameraIndicatorError is raised and the camera is
    never opened.
    """
    shown = indicator.show()  # 1. visible first ...
    try:
        source.open(shown)  # 2. ... then open
    except BaseException:
        indicator.failed()
        raise
    started = clock()
    try:
        frame = source.read_frame()
    finally:
        source.close()
        indicator.closed(clock() - started)
    return Capture(frame=frame, seconds=clock() - started)
