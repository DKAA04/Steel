"""Camera consent: one small file with a timestamp, which the owner can delete.

No consent file means no camera. It is a different file from the microphone's, so
neither grants, revokes or affects the other. The checks on the file are shared
(steel/consent.py).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from steel.consent import delete_stamp, read_stamp, write_stamp
from steel.home import CAMERA_CONSENT_FILE

# docs/VISION.md section 5.3 quotes this word for word; a test compares them.
CONSENT_TEXT = """STEEL camera: please read

STEEL can take one picture with the camera of this computer and describe it in words, using a model that runs on this computer.

- The camera opens only when you run a STEEL command that asks for it (steel look). It takes a single picture and closes again. There is no video, no always-on watching and no motion detection.
- Before it opens, STEEL prints a "● CAMERA OPEN" line. If that line cannot be printed, the camera is not opened.
- The picture is kept in memory only. It is made smaller, shown to a model running on this computer, and then thrown away. No picture file is written, and nothing is sent over the network.
- You see the description. It is saved only if you run steel look --save and confirm it, and then only for the time you chose (24 hours unless you choose otherwise, 7 days at most).
- The camera sees everyone and everything in view, not only you. Be careful with guests, and with papers or screens in view. STEEL does not identify people, but they are in the picture.
- A small model can be wrong, and it may not obey its instructions: it can describe things that are not there, and it can mention what it was told not to.
- This permission is stored as a date and time in a file in your OpenJarvis folder. You can withdraw it at any time with: steel vision revoke
- You must also grant the camera tool by hand in steel_policy.json. This consent does not do that.

Type yes to agree."""


def consent_path(home: Path) -> Path:
    return home / CAMERA_CONSENT_FILE


def read_consent(home: Path) -> str | None:
    """The timestamp in a well-formed consent file that is ours and private, else None."""
    return read_stamp(consent_path(home))


def has_consent(home: Path) -> bool:
    return read_consent(home) is not None


def record_consent(home: Path, *, now: datetime | None = None) -> str:
    """Write the consent file (mode 600) and return the timestamp it holds."""
    return write_stamp(consent_path(home), now=now)


def revoke_consent(home: Path) -> bool:
    """Delete the consent file; return whether there was one."""
    return delete_stamp(consent_path(home))
