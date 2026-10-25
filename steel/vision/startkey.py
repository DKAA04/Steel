"""The Enter key that takes the picture (docs/VISION.md 5.1).

The same key handling as the microphone's start key, with the camera's words.
In ``steel chat`` the typed ``/look`` line is the key press (``arm``), as an
empty line is for the microphone in ``steel chat --voice``.
"""

from __future__ import annotations

from steel.voice.terminal import Terminal

PROMPT = "Press Enter to take one picture, Ctrl-C cancels. "
GAVE_UP = "No key pressed; the camera was not opened."


class PictureKey:
    """Waits for Enter at a real terminal. Without a terminal it never starts."""

    def __init__(self, terminal: Terminal | None = None) -> None:
        self._terminal = terminal or Terminal()

    def arm(self) -> None:
        """The owner just pressed Enter on a line that asks for a picture."""
        self._terminal.arm()

    def wait_for_start(self, timeout: float) -> bool:
        return self._terminal.wait_for_start(timeout, prompt=PROMPT, gave_up=GAVE_UP)
