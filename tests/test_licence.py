"""LICENSE is the standard Apache-2.0 text, and NOTICE names what STEEL uses."""

from __future__ import annotations

import hashlib
import tomllib
import unittest
from pathlib import Path

from steel.speak.voices import VOICES

ROOT = Path(__file__).resolve().parent.parent
# SHA-256 of https://www.apache.org/licenses/LICENSE-2.0.txt
APACHE_2_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


class LicenceTest(unittest.TestCase):
    def test_the_licence_is_the_standard_apache_text_after_one_copyright_line(
        self,
    ) -> None:
        first, blank, rest = (ROOT / "LICENSE").read_bytes().split(b"\n", 2)
        self.assertTrue(first.startswith(b"Copyright 2026 "))
        self.assertEqual(blank, b"")
        self.assertEqual(hashlib.sha256(rest).hexdigest(), APACHE_2_SHA256)

    def test_the_package_declares_the_licence_and_ships_both_files(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(project["license-files"], ["LICENSE", "NOTICE"])

    def test_the_notice_names_every_component_and_default_voice(self) -> None:
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        for needed in (
            "OpenJarvis",
            "docs/PROVENANCE.md",
            "faster-whisper",
            "Whisper models, MIT",
            "onnxruntime, MIT",
            "GNU General Public License v3.0 or later",
            "not part of this repository",
            "THIS DATA IS PROVIDED BY THE COPYRIGHT HOLDERS",
        ):
            self.assertIn(needed, notice)
        for voice in VOICES:
            if voice.default:
                self.assertIn(voice.name, notice)
        self.assertNotIn("legal advice.", notice.replace("not legal advice.", ""))


if __name__ == "__main__":
    unittest.main()
