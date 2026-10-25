"""steel chat --speak, steel speak check and steel speak say (docs/SPEAK.md 6).

Fake engine, fake player, fake keys, scripted input, temporary home. Nothing
is played and no engine or player program is started.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from test_chat_slash import ReplyEngine
from test_speak_tool import LOG, SpeakCase, speak_grant

from steel.chat import run_repl
from steel.cli import main
from steel.speak import commands
from steel.speak.commands import REMINDER, ChatSpeaker, speak_check, speak_say
from steel.speak.voices import PinnedFile, Voice, voice_dir
from steel.tooldata import prepare


class ChatSpeakTest(SpeakCase):
    def chat(
        self,
        lines: list[Any],
        replies: tuple[str, ...] = ("Noted.",),
        speaker: Any = "default",
    ) -> tuple[str, Any]:
        engine = ReplyEngine(*replies)
        session = self.session(engine)
        self.addCleanup(session.close, 5)
        if speaker == "default":
            speaker = ChatSpeaker(self.home, tool=self.tool(), keys=self.keys)
        out = io.StringIO()
        with (
            mock.patch("builtins.input", side_effect=lines),
            mock.patch.dict("os.environ", {"STEEL_EXTRACT_WAIT_SECONDS": "5"}),
            contextlib.redirect_stdout(out),
        ):
            run_repl(session, None, speaker)
        return out.getvalue(), engine

    def test_speaking_is_off_unless_speak_is_passed(self) -> None:
        for argv, expected in ((["chat"], False), (["chat", "--speak"], True)):
            with (
                mock.patch("steel.chat.open_session"),
                mock.patch("steel.chat.run_repl") as repl,
            ):
                self.assertEqual(main(argv), 0)
            speaker = repl.call_args.args[2]
            self.assertEqual(isinstance(speaker, ChatSpeaker), expected, argv)
            self.assertEqual(speaker is None, not expected)
        # And --voice without --speak builds no speaker either.
        from steel.voice import commands as voice_commands

        self.assertFalse(
            voice_commands.run_voice_repl.__kwdefaults__["speak"]  # type: ignore[index]
        )

    def test_with_the_grant_each_reply_is_spoken_after_it_is_printed(self) -> None:
        self.write_grants(speak_grant())
        out, _ = self.chat(
            ["hello", "and you?", "/quit"], replies=("Hello there.", "Fine.")
        )
        self.assertIn(REMINDER, out)
        self.assertEqual(self.engine.texts, ["Hello there.", "Fine."])
        self.assertIn("anyone nearby can hear them", out)

    def test_no_grant_says_so_once_logs_once_and_stays_silent(self) -> None:
        out, _ = self.chat(["one", "two", "three", "/quit"])
        self.assertEqual(out.count("is not granted to agent 'voice'"), 1)
        self.assertEqual(self.engine.texts, [])
        lines = [json.loads(x) for x in (self.home / LOG).read_text().splitlines()]
        self.assertEqual(
            [(x["capability"], x["decision"]) for x in lines], [("audio:play", "deny")]
        )

    def test_a_line_typed_to_stop_a_reply_is_the_next_message(self) -> None:
        self.write_grants(speak_grant())
        self.keys.stop_after, self.keys.line = 1, "and tomorrow?"
        out, engine = self.chat(["what is on today?", "/quit"])
        self.assertIn("you> and tomorrow?", out)
        self.assertEqual(engine.turn, 2)  # both lines reached the model

    def test_a_typed_exit_while_speaking_leaves(self) -> None:
        self.write_grants(speak_grant())
        self.keys.stop_after, self.keys.line = 1, "exit"
        _, engine = self.chat(["hi"])  # nothing else typed: input() is not asked
        self.assertEqual(engine.turn, 1)

    def test_ctrl_c_while_speaking_stops_the_reply_and_the_chat_goes_on(self) -> None:
        self.write_grants(speak_grant())
        speaker = ChatSpeaker(self.home, tool=self.tool(), keys=self.keys)
        speaker.tool = mock.Mock(wraps=speaker.tool)
        with mock.patch.object(
            speaker._executor, "execute", side_effect=KeyboardInterrupt
        ):
            out, engine = self.chat(["one", "two", "/quit"], speaker=speaker)
        speaker.tool.cancel.assert_called()
        self.assertEqual(engine.turn, 2)  # the chat went on to the second line
        self.assertIn("Saving memory", out)

    def test_a_reply_that_repeats_tool_data_is_not_spoken(self) -> None:
        self.write_grants(speak_grant())
        echoed = (
            "Sure: " + prepare("memory_search_room", "n-1: A blue folder.").wrapped()
        )
        out, _ = self.chat(["what did you see?", "/quit"], replies=(echoed,))
        self.assertEqual(self.engine.texts, [])
        self.assertIn("never read aloud", out)


class SpeakCheckTest(SpeakCase):
    def small_voice(self, language: str = "en") -> Voice:
        """A voice with real (tiny) files, so hashes can match."""
        files = {}
        name = f"{language}_XX-test-tiny"
        directory = voice_dir(
            self.home,
            Voice(
                name,
                language,
                "",
                16000,
                True,
                "",
                *(PinnedFile("x", 1, "") for _ in range(3)),
            ),
        )
        directory.mkdir(parents=True, exist_ok=True)
        for file_name, data in (
            (f"{name}.onnx", b"model"),
            (f"{name}.onnx.json", b"{}"),
            ("MODEL_CARD", b"card"),
        ):
            (directory / file_name).write_bytes(data)
            files[file_name] = PinnedFile(
                file_name, len(data), hashlib.sha256(data).hexdigest()
            )
        return Voice(name, language, "x", 16000, True, "test licence", *files.values())

    def install_engine(self) -> None:
        directory = self.home.parent / "piper"
        (directory / "bin").mkdir(parents=True)
        (directory / "bin" / "python").write_text("")
        meta = directory / "lib/python3.13/site-packages/piper_tts-1.8.0.dist-info"
        meta.mkdir(parents=True)
        (meta / "METADATA").write_text("Version: 1.8.0\n")

    def check(self, code: int, *, which: Any = lambda n: f"/usr/bin/{n}") -> str:
        out = io.StringIO()
        with (
            mock.patch.object(
                subprocess, "Popen", side_effect=AssertionError("started")
            ),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(speak_check(self.home, which=which), code)
        return out.getvalue()

    def test_check_is_read_only_starts_nothing_and_logs_nothing(self) -> None:
        before = sorted(p.name for p in self.home.rglob("*"))
        out = self.check(1)
        self.assertIn("nothing is played, no program is started", out)
        self.assertEqual(sorted(p.name for p in self.home.rglob("*")), before)
        self.assertFalse((self.home / LOG).exists())

    def test_check_lists_what_is_missing(self) -> None:
        out = self.check(1, which=lambda _n: None)
        self.assertIn("engine: NOT installed", out)
        self.assertIn("unshare NOT found", out)
        self.assertIn("aplay NOT found", out)
        self.assertIn("grant: NOT granted to agent 'voice'", out)
        self.assertIn('"capability": "audio:play", "pattern": "speak"', out)
        self.assertIn("SHA-256 MISMATCH", out)  # the fake voice files of SpeakCase

    def test_with_everything_in_place_it_is_ready(self) -> None:
        self.install_engine()
        self.write_grants(speak_grant())
        voices = (
            self.small_voice("en"),
            self.small_voice("es"),
            self.small_voice("it"),
        )
        with mock.patch.object(commands, "VOICES", voices):
            out = self.check(0)
        self.assertIn("engine: piper-tts 1.8.0", out)
        self.assertIn("GPL-3.0-or-later; run as a separate program", out)
        self.assertIn("--net (the engine runs with no network)", out)
        self.assertIn("SHA-256 OK", out)
        self.assertIn("grant: agent 'voice' may use speak", out)
        self.assertIn("at most 600 characters", out)
        self.assertIn("ready: yes", out)

    def test_without_an_english_voice_it_is_not_ready(self) -> None:
        self.install_engine()
        self.write_grants(speak_grant())
        with mock.patch.object(commands, "VOICES", (self.small_voice("es"),)):
            out = self.check(1)
        self.assertIn("English: none installed", out)


class SpeakSayTest(SpeakCase):
    def say(self, text: str, code: int) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(speak_say(self.home, text, tool=self.tool()), code)
        return out.getvalue(), err.getvalue()

    def test_say_speaks_one_sentence_through_the_policy(self) -> None:
        self.write_grants(speak_grant())
        out, _ = self.say("Testing one two three.", 0)
        self.assertIn("Spoken (en,", out)
        self.assertEqual(self.engine.texts, ["Testing one two three."])

    def test_say_without_a_grant_is_denied_and_logged(self) -> None:
        _, err = self.say("Testing.", 1)
        self.assertIn("Capability 'audio:play' denied", err)
        self.assertEqual(self.engine.texts, [])
        self.assertTrue((self.home / LOG).exists())

    def test_say_takes_one_short_sentence_only(self) -> None:
        self.write_grants(speak_grant())
        self.say("x" * 201, 2)
        self.say("   ", 2)
        self.assertEqual(self.engine.texts, [])

    def test_the_command_line_needs_text_for_say_and_none_for_check(self) -> None:
        for argv in (["speak", "say"], ["speak", "check", "extra"]):
            with (
                self.assertRaises(SystemExit) as raised,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                main(argv)
            self.assertEqual(raised.exception.code, 2)


class LiveStateTest(base.HomeTestCase):
    def test_the_engine_dir_and_home_are_the_temporary_ones(self) -> None:
        from steel.speak.synth import engine_dir

        self.assertNotEqual(engine_dir(), Path.home() / ".local/share/steel/piper")
        self.assertTrue(str(engine_dir()).startswith(str(self.home.parent)))
