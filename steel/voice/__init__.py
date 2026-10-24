"""Push-to-talk voice input for STEEL, fully local (docs/VOICE.md).

Nothing here listens in the background: the microphone opens only for one
recording, after a key press, with a visible REC line, and only if the owner
gave consent and granted the ``microphone_listen`` tool by hand.

Deliberately not imported here: ``openjarvis.speech`` (its discovery functions
can choose a cloud backend), ``sounddevice`` (importing it initialises
PortAudio) and ``faster_whisper`` (loaded only when a transcription starts).
"""
