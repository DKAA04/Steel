"""The audio:play capability (docs/SPEAK.md section 6). No sound, no model."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from steel.security import PolicyError, SteelPolicy
from steel.security.policy import EXACT_NAME_CAPS, SENSOR_CAPS, VOCABULARY

SPEAK = "speak"


class AudioPlayPolicyTest(unittest.TestCase):
    def test_audio_play_is_in_the_vocabulary_and_is_not_a_sensor(self) -> None:
        self.assertIn("audio:play", VOCABULARY)
        self.assertNotIn("audio:play", SENSOR_CAPS)
        self.assertIn("audio:play", EXACT_NAME_CAPS)

    def test_a_grant_must_name_the_exact_tool(self) -> None:
        policy = SteelPolicy()
        policy.grant("voice", "audio:play", SPEAK)
        self.assertTrue(policy.check("voice", "audio:play", SPEAK))
        self.assertFalse(policy.check("voice", "audio:play", "other_tool"))
        self.assertFalse(policy.check("chat", "audio:play", SPEAK))  # other agent

    def test_wildcard_audio_grants_are_refused(self) -> None:
        for capability, pattern in (
            ("audio:*", SPEAK),
            ("audio:play", "*"),
            ("*", SPEAK),
        ):
            with (
                self.subTest(capability=capability, pattern=pattern),
                self.assertRaises(PolicyError),
            ):
                SteelPolicy().grant("voice", capability, pattern)

    def test_a_wildcard_audio_grant_makes_the_whole_file_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "steel_policy.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "voice",
                                "grants": [
                                    {
                                        "capability": "sensor:microphone",
                                        "pattern": "microphone_listen",
                                    },
                                    {"capability": "audio:play", "pattern": "*"},
                                ],
                            }
                        ]
                    }
                )
            )
            path.chmod(0o600)
            with self.assertRaises(PolicyError):
                SteelPolicy.from_file(path)

    def test_no_other_grant_covers_audio_play(self) -> None:
        policy = SteelPolicy()
        policy.grant("voice", "memory:read:*")
        policy.grant("voice", "sensor:microphone", "microphone_listen")
        policy.grant("voice", "tool:invoke", SPEAK)
        self.assertFalse(policy.check("voice", "audio:play", SPEAK))

    def test_a_deny_wildcard_covers_audio_play(self) -> None:
        policy = SteelPolicy()
        policy.grant("voice", "audio:play", SPEAK)
        policy.deny("voice", "audio:*")
        self.assertFalse(policy.check("voice", "audio:play", SPEAK))


if __name__ == "__main__":
    unittest.main()
