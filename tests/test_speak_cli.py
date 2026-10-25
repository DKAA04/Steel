"""steel chat --speak, steel speak check and steel speak say (docs/SPEAK.md 6).

Fake engine, fake player, fake keys, scripted input, temporary home. Nothing
is played and no engine or player program is started.
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import io
import json
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from test_chat_slash import ReplyEngine
from test_speak_tool import LOG, SpeakCase, speak_grant

from steel.chat import open_session, run_repl
from steel.cli import main
from steel.speak import commands
from steel.speak.commands import REMINDER, ChatSpeaker, speak_check, speak_say
from steel.speak.voices import PinnedFile, Voice, find_voice, voice_dir
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


class EarlyStartChatTest(SpeakCase):
    """The engine is started before the model is asked (docs/LATENCY.md 8)."""

    chat = ChatSpeakTest.chat

    def test_the_engine_is_started_before_the_model_is_asked(self) -> None:
        self.write_grants(speak_grant())
        order = self.engine.events

        class Recording(ReplyEngine):
            def generate(self, messages: Any, *, model: str, **kw: Any) -> Any:
                if messages[0].content != base.EXTRACTOR_PROMPT:
                    order.append("model")
                return super().generate(messages, model=model, **kw)

        engine = Recording("Fine, thanks.")
        session = self.session(engine)
        self.addCleanup(session.close, 5)
        speaker = ChatSpeaker(self.home, tool=self.tool(), keys=self.keys)
        with (
            mock.patch("builtins.input", side_effect=["how are you?", "/quit"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run_repl(session, None, speaker)
        self.assertEqual(order, ["prepare", "model", "start", "close"])
        english = voice_dir(self.home, find_voice("en_US-john-medium"))
        self.assertEqual(self.engine.prepared, [english / "en_US-john-medium.onnx"])

    def test_the_voice_started_early_follows_the_language_of_the_message(
        self,
    ) -> None:
        self.write_grants(speak_grant())
        self.chat(["¿Qué tiempo hace hoy en Valencia?", "/quit"], replies=("Sol.",))
        (model,) = self.engine.prepared
        self.assertEqual(model.name, "es_ES-carlfm-x_low.onnx")

    def test_the_message_never_reaches_the_engine_only_the_reply_does(self) -> None:
        self.write_grants(speak_grant())
        self.chat(["my bank PIN is 4711", "/quit"], replies=("Noted.",))
        self.assertTrue(all(isinstance(p, Path) for p in self.engine.prepared))
        self.assertEqual(self.engine.texts, ["Noted."])
        self.assertNotIn("4711", " ".join(map(str, self.engine.prepared)))

    def test_without_the_grant_no_engine_is_started_early(self) -> None:
        self.chat(["one", "two", "/quit"])
        self.assertEqual(self.engine.prepared, [])
        self.assertEqual(self.engine.texts, [])

    def test_no_engine_is_left_waiting_after_quit_ctrl_d_ctrl_c_or_an_error(
        self,
    ) -> None:
        self.write_grants(speak_grant())
        for ending in ("/quit", EOFError(), KeyboardInterrupt(), RuntimeError("x")):
            self.engine.closed = 0
            try:
                self.chat(["hello", ending])
            except RuntimeError:
                pass
            self.assertEqual(self.engine.closed, 1, repr(ending))

    def test_a_whole_spoken_turn_uses_no_network_and_writes_no_file(self) -> None:
        self.write_grants(speak_grant())
        speaker = ChatSpeaker(self.home, tool=self.tool(), keys=self.keys)
        speaker.say("warm up")  # creates the decision log first
        config = self.config()
        config.memory.enabled = False  # memory writes are not this test's subject
        session = open_session(config, self.home, engine=ReplyEngine("Sure."))
        before = {p: p.stat().st_mtime_ns for p in self.home.rglob("*")}
        real_open = builtins.open

        def guarded(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
            if any(flag in mode for flag in "wx+") or (
                "a" in mode and not str(file).endswith(LOG)
            ):
                raise AssertionError(f"tried to write {file!r}")
            return real_open(file, mode, *a, **k)

        def forbidden(*_: Any, **__: Any) -> Any:
            raise AssertionError("no network and no temporary file in a turn")

        with (
            mock.patch("builtins.input", side_effect=["hello", "/quit"]),
            mock.patch.object(builtins, "open", guarded),
            mock.patch.object(tempfile, "mkstemp", forbidden),
            mock.patch.object(tempfile, "NamedTemporaryFile", forbidden),
            mock.patch.object(socket.socket, "connect", forbidden),
            mock.patch.object(socket.socket, "connect_ex", forbidden),
            mock.patch.object(socket, "create_connection", forbidden),
            mock.patch.object(socket, "getaddrinfo", forbidden),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run_repl(session, None, speaker)
        self.assertEqual(self.engine.texts[-1], "Sure.")
        after = {p: p.stat().st_mtime_ns for p in self.home.rglob("*")}
        changed = {p.name for p in after if before.get(p) != after[p]}
        self.assertLessEqual(changed, {LOG, "audit.db", "audit.db-journal"})


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
