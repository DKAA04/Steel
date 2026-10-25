"""Always-on consent: its own file, separate from the push-to-talk consent.

docs/JARVIS.md section 3a. No file means the listening microphone never opens.
The file holds one timestamp, with the checks of steel/consent.py (a regular
file, the owner's, not writable by others).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from steel.consent import delete_stamp, read_stamp, write_stamp
from steel.home import WAKE_CONSENT_FILE

# docs/JARVIS.md section 3a.1 quotes this word for word; a test compares them.
CONSENT_TEXT = """STEEL Jarvis loop: ALWAYS-ON LISTENING. Please read.

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

Type yes to agree."""


def consent_path(home: Path) -> Path:
    return home / WAKE_CONSENT_FILE


def has_wake_consent(home: Path) -> bool:
    return read_stamp(consent_path(home)) is not None


def read_wake_consent(home: Path) -> str | None:
    return read_stamp(consent_path(home))


def record_wake_consent(home: Path, *, now: datetime | None = None) -> str:
    return write_stamp(consent_path(home), now=now)


def revoke_wake_consent(home: Path) -> bool:
    return delete_stamp(consent_path(home))
