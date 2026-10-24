"""Optional spoken replies through a local ``espeak-ng``, if it is installed.

``espeak-ng`` is not installed on the owner's machine (docs/VOICE.md section 5),
so this has only been tested with a fake runner. The text goes in on standard
input, never on the command line, so it does not show up in ``ps``. No cloud
voice is used or reachable from here.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

_LANGUAGE_RE = re.compile(r"[a-z]{2,3}")
MAX_SPOKEN_CHARS = 2000
PROGRAM = "espeak-ng"


class EspeakSpeaker:
    def __init__(
        self, program: str = PROGRAM, *, run: Callable[..., Any] = subprocess.run
    ) -> None:
        self._program = program
        self._run = run

    def say(self, text: str, language: str | None = None) -> bool:
        """Speak *text*; True if the program ran and succeeded."""
        argv = [self._program, "--stdin"]
        if language and _LANGUAGE_RE.fullmatch(language):
            argv += ["-v", language]
        try:
            done = self._run(
                argv,
                input=text[:MAX_SPOKEN_CHARS].encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0


def find_speaker(
    which: Callable[[str], str | None] = shutil.which,
) -> EspeakSpeaker | None:
    """An ``EspeakSpeaker`` if ``espeak-ng`` is on the PATH, else None."""
    return EspeakSpeaker() if which(PROGRAM) else None
