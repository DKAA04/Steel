"""steel voice check|consent|revoke|listen and steel chat --voice.

Fake audio, fake keys, a fake model engine. Nothing opens a microphone or a
speaker, loads a speech model, uses the network, or touches the real
~/.openjarvis (every test has a temporary home).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest import mock

import test_steel as base
from test_voice_tool import LOG, SECRET, VoiceCase, voice_grant
from voice_helpers import FakeSource, tone

from steel.cli import main
from steel.memory import SteelMemory
from steel.security import PolicyError, policy_or_deny_all
from steel.voice import commands
from steel.voice.commands import (
    capture_devices,
    run_voice_repl,
    voice_check,
    voice_consent,
    voice_revoke,
)
from steel.voice.config import VoiceConfig
from steel.voice.consent import CONSENT_TEXT, consent_path, has_consent, record_consent
from steel.voice.speak import EspeakSpeaker, find_speaker

PCM_SAMPLE = (
    "00-03: HDMI 0 : HDMI 0 : playback 1\n"
    "01-00: ALC257 Analog : ALC257 Analog : playback 1 : capture 1\n"
)


class CliCase(VoiceCase):
    def run_cli(self, *argv: str, expect: int = 0) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        self.assertEqual(code, expect, out.getvalue() + err.getvalue())
        return out.getvalue(), err.getvalue()

    def with_tool(self, **overrides: Any) -> Any:
        """Make `steel voice listen` use the fake-wired tool."""
        patcher = mock.patch.object(
            commands, "get_microphone_tool", return_value=self.tool(**overrides)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return commands.get_microphone_tool

    def snapshot(self) -> dict[str, tuple[int, int]]:
        return {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in self.home.iterdir()
            if p.is_file()
        }


class CaptureDeviceListTest(CliCase):
    def test_only_devices_that_can_capture_are_listed(self) -> None:
        listing = self.home.parent / "pcm"
        listing.write_text(PCM_SAMPLE)
        self.assertEqual(capture_devices(listing), ["card 01 device 00: ALC257 Analog"])

    def test_an_unreadable_list_is_none_not_an_error(self) -> None:
        self.assertIsNone(capture_devices(self.home.parent / "missing"))


class VoiceCheckTest(CliCase):
    def ready_world(self) -> contextlib.ExitStack:
        """Everything voice needs, faked, so `ready: yes` can be reached."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        listing = self.home.parent / "pcm"
        listing.write_text(PCM_SAMPLE)
        (self.home / "steel_models" / "faster-whisper-small").mkdir(parents=True)
        (self.home / "steel_models" / "faster-whisper-small" / "model.bin").write_bytes(
            b"x" * 2_000_000
        )
        stack.enter_context(
            mock.patch.object(commands.shutil, "which", return_value="/bin/arecord")
        )
        stack.enter_context(
            mock.patch.object(commands, "find_speaker", return_value=None)
        )
        stack.enter_context(
            mock.patch.object(
                commands.importlib.metadata, "version", return_value="1.2.1"
            )
        )
        stack.enter_context(mock.patch.object(commands, "PCM_LIST", listing))
        return stack

    def test_check_shows_the_vocabulary_hint(self) -> None:
        with mock.patch.dict("os.environ", {"STEEL_VOICE_HINT": "STEEL, Ada"}):
            text = self.check(1)
        self.assertIn('vocabulary hint: "STEEL, Ada"', text)

    def check(self, expect: int) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = voice_check(self.home, pcm_list=self.home.parent / "pcm")
        self.assertEqual(code, expect, out.getvalue())
        return out.getvalue()

    def test_check_opens_no_microphone_and_starts_no_process(self) -> None:
        def boom(*_: Any, **__: Any) -> Any:
            raise AssertionError("steel voice check must not do this")

        with (
            mock.patch("steel.voice.mic.ArecordSource", boom),
            mock.patch.object(subprocess, "Popen", boom),
            mock.patch.object(subprocess, "run", boom),
            mock.patch.object(commands, "get_microphone_tool", boom),
            mock.patch("steel.voice.terminal.Terminal", boom),
        ):
            out, _ = self.run_cli("voice", "check", expect=1)  # not ready: no consent
        self.assertIn("the microphone is not opened", out)
        self.assertNotIn("sounddevice", sys.modules)

    def test_check_with_the_fake_source_never_opens_it(self) -> None:
        self.allow_everything()
        with self.ready_world():
            self.check(0)
        self.assertEqual(self.sources, [])  # no source was even created
        self.assertEqual(self.screen.getvalue(), "")  # and no REC line

    def test_check_writes_nothing_not_even_a_decision_log(self) -> None:
        self.allow_everything()
        before = self.snapshot()
        with self.ready_world():
            self.check(0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.home / LOG).exists())

    def test_check_changes_no_permissions_and_creates_nothing(self) -> None:
        # Unlike the commands that write, it must not run harden() on the home.
        self.home.chmod(0o755)
        (self.home / "USER.md").chmod(0o644)
        before = {p.name for p in self.home.iterdir()}
        self.run_cli("voice", "check", expect=1)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.home / "USER.md").stat().st_mode & 0o777, 0o644)
        self.assertEqual({p.name for p in self.home.iterdir()}, before)

    def test_everything_in_place_is_ready(self) -> None:
        self.allow_everything()
        with self.ready_world():
            out = self.check(0)
        self.assertIn("capture devices: card 01 device 00: ALC257 Analog", out)
        self.assertIn("faster-whisper 1.2.1 installed", out)
        self.assertIn("speech model: small, 2 MiB", out)
        self.assertIn("consent: present", out)
        self.assertIn("grant: agent 'voice' may use microphone_listen", out)
        self.assertIn("text to speech: none installed", out)
        self.assertTrue(out.rstrip().endswith("ready: yes"))

    def test_missing_consent_is_reported(self) -> None:
        self.write_grants(voice_grant())
        with self.ready_world():
            out = self.check(1)
        self.assertIn("consent: MISSING (run: steel voice consent)", out)
        self.assertIn("ready: no", out)

    def test_a_missing_grant_is_reported_with_the_example_to_write_by_hand(
        self,
    ) -> None:
        record_consent(self.home)
        with self.ready_world():
            out = self.check(1)
        self.assertIn("NOT granted to agent 'voice' (unknown_agent)", out)
        self.assertIn('"pattern": "microphone_listen"', out)
        self.assertIn("by hand", out)
        self.assertFalse((self.home / "steel_policy.json").exists())  # never written

    def test_an_invalid_grants_file_is_reported_as_everything_denied(self) -> None:
        record_consent(self.home)
        self.write_grants("not json")
        with self.ready_world():
            out = self.check(1)
        self.assertIn("grants file INVALID", out)

    def test_a_missing_model_and_a_missing_capture_program_are_reported(self) -> None:
        self.allow_everything()
        with (
            mock.patch.object(commands.shutil, "which", return_value=None),
            mock.patch.object(commands, "find_speaker", return_value=None),
        ):
            out = self.check(1)
        self.assertIn("arecord NOT found", out)
        self.assertIn("speech model: small MISSING", out)

    def test_a_cloud_key_in_the_environment_is_noted_but_never_printed(self) -> None:
        self.allow_everything()
        with (
            self.ready_world(),
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret-value"}),
        ):
            out = self.check(0)
        self.assertIn("OPENAI_API_KEY is set", out)
        self.assertIn("never reads it", out)
        self.assertNotIn("sk-secret-value", out)


class ConsentCommandTest(CliCase):
    def consent(self, answer: str | None, *, terminal: bool = True) -> tuple[int, str]:
        def ask(_prompt: str) -> str:
            if answer is None:
                raise EOFError
            return answer

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = voice_consent(self.home, input_fn=ask, is_terminal=lambda: terminal)
        return code, out.getvalue() + err.getvalue()

    def test_the_consent_text_is_shown_before_asking(self) -> None:
        _code, text = self.consent("no")
        self.assertIn(CONSENT_TEXT, text)
        self.assertIn("everyone who is speaking near it", text)

    def test_typing_yes_records_consent_with_mode_600(self) -> None:
        code, text = self.consent("yes")
        self.assertEqual(code, 0)
        self.assertTrue(has_consent(self.home))
        self.assertEqual(consent_path(self.home).stat().st_mode & 0o777, 0o600)
        self.assertIn("steel voice revoke", text)

    def test_anything_but_yes_records_nothing(self) -> None:
        for answer in ("no", "y", "", "yes please", "sure", None):
            code, _ = self.consent(answer)
            self.assertEqual(code, 1, answer)
            self.assertFalse(has_consent(self.home), answer)

    def test_consent_cannot_be_given_from_a_pipe_or_script(self) -> None:
        code, text = self.consent("yes", terminal=False)
        self.assertEqual(code, 1)
        self.assertIn("at a terminal", text)
        self.assertFalse(has_consent(self.home))

    def test_consent_does_not_create_a_grant(self) -> None:
        self.consent("yes")
        self.assertFalse((self.home / "steel_policy.json").exists())

    def test_the_command_is_wired_into_steel(self) -> None:
        with (
            mock.patch("builtins.input", return_value="yes"),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
        ):
            self.run_cli("voice", "consent")
        self.assertTrue(has_consent(self.home))

    def test_revoke_deletes_the_file_and_the_microphone_refuses_again(self) -> None:
        self.allow_everything()
        self.with_tool()
        self.run_cli("voice", "listen")
        self.assertEqual(self.opened(), 1)
        out, _ = self.run_cli("voice", "revoke")
        self.assertIn("Consent withdrawn", out)
        self.assertFalse(consent_path(self.home).exists())
        _out, err = self.run_cli("voice", "listen", expect=1)
        self.assertIn("no voice consent", err)
        self.assertEqual(self.opened(), 1)  # the second attempt opened nothing

    def test_revoke_without_consent_says_so(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(voice_revoke(self.home), 0)
        self.assertIn("nothing to withdraw", out.getvalue())


class ListenCommandTest(CliCase):
    def test_no_consent_means_no_tool_is_even_built(self) -> None:
        self.write_grants(voice_grant())
        build = self.with_tool()
        _out, err = self.run_cli("voice", "listen", expect=1)
        self.assertIn("no voice consent", err)
        build.assert_not_called()
        self.assertEqual(self.opened(), 0)
        self.assertFalse((self.home / LOG).exists())  # no policy decision either

    def test_no_grant_is_denied_and_the_denial_is_logged(self) -> None:
        record_consent(self.home)
        self.with_tool()
        _out, err = self.run_cli("voice", "listen", expect=1)
        self.assertIn("Capability 'sensor:microphone' denied", err)
        self.assertEqual(self.opened(), 0)
        (entry,) = self.log_lines()
        self.assertEqual(
            (entry["agent_id"], entry["capability"], entry["decision"]),
            ("voice", "sensor:microphone", "deny"),
        )

    def test_with_consent_and_grant_it_prints_the_transcript(self) -> None:
        self.allow_everything()
        self.with_tool()
        out, _err = self.run_cli("voice", "listen")
        self.assertEqual(out.strip(), SECRET)
        self.assertIn("● REC", self.sources[0].screen_at_open or "")
        self.assertEqual(self.log_lines()[-1]["decision"], "allow")

    def test_nothing_heard_is_said_plainly(self) -> None:
        self.allow_everything()
        self.transcriber.text = ""
        self.with_tool()
        out, _ = self.run_cli("voice", "listen")
        self.assertEqual(out.strip(), "Nothing heard.")

    def test_a_malformed_grants_file_denies_and_logs(self) -> None:
        record_consent(self.home)
        self.write_grants("not json")
        self.with_tool()
        _out, err = self.run_cli("voice", "listen", expect=1)
        self.assertIn("grants file invalid", err)
        self.assertEqual(self.opened(), 0)
        self.assertEqual(self.log_lines()[-1]["decision"], "deny")

    def test_ctrl_c_during_a_recording_stops_and_closes_the_microphone(self) -> None:
        # A process started in the background inherits SIGINT as "ignored", and
        # then Python installs no Ctrl-C handler; install the normal one here.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        self.allow_everything()
        endless = FakeSource(forever=tone(0.1)[0], screen=self.screen, delay=0.01)
        self.with_tool(open_source=lambda: endless, config=VoiceConfig(max_seconds=120))
        timer = threading.Timer(0.3, os.kill, args=(os.getpid(), signal.SIGINT))
        timer.start()
        self.addCleanup(timer.cancel)
        _out, err = self.run_cli("voice", "listen", expect=130)
        self.assertIn("Cancelled", err)
        self.assertGreaterEqual(endless.closed, 1)  # close() is safe to repeat
        self.assertFalse(any(self.transcriber.heard))


class PolicyHelperTest(base.HomeTestCase):
    def test_a_broken_file_gives_an_empty_policy_that_still_logs(self) -> None:
        (self.home / "steel_policy.json").write_text("not json")
        (self.home / "steel_policy.json").chmod(0o600)
        policy, problem = policy_or_deny_all(self.home)
        self.assertIsInstance(problem, PolicyError)
        self.assertFalse(
            policy.check("voice", "sensor:microphone", "microphone_listen")
        )
        self.assertTrue((self.home / LOG).exists())

    def test_a_good_file_loads(self) -> None:
        (self.home / "steel_policy.json").write_text(
            '{"agents": [{"agent_id": "voice", "grants": '
            '[{"capability": "sensor:microphone", "pattern": "microphone_listen"}]}]}'
        )
        (self.home / "steel_policy.json").chmod(0o600)
        policy, problem = policy_or_deny_all(self.home)
        self.assertIsNone(problem)
        self.assertTrue(policy.check("voice", "sensor:microphone", "microphone_listen"))


class FakeTerminal:
    """Scripted keys for the chat loop."""

    def __init__(self, confirms: list[bool]) -> None:
        self.confirms = list(confirms)
        self.armed = 0
        self.asked = 0

    def arm(self) -> None:
        self.armed += 1

    def wait_for_start(self, timeout: float) -> bool:
        return True

    def stop_pressed(self) -> bool:
        return False

    def confirm(self) -> bool:
        self.asked += 1
        return self.confirms.pop(0)


class ChatVoiceTest(VoiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.allow_everything()
        self.engine = base.FakeEngine()
        self.session = base.open_session(self.config(), self.home, engine=self.engine)
        self.addCleanup(self.session.close, 1)
        self.terminal = FakeTerminal([True])
        self.transcriber.text = "My favourite colour is teal."
        self.speaker = mock.Mock(spec=EspeakSpeaker)

    def run_chat(
        self, lines: list[str], *, speak: bool = False, tool: Any = None, **kw: Any
    ) -> str:
        tool = tool or self.tool(push_to_talk=self.terminal)
        out = io.StringIO()
        with (
            mock.patch("builtins.input", side_effect=lines),
            contextlib.redirect_stdout(out),
        ):
            code = run_voice_repl(
                self.session,
                self.home,
                speak=speak,
                terminal=self.terminal,  # type: ignore[arg-type]
                tool=tool,
                **kw,
            )
        self.assertEqual(code, 0)
        return out.getvalue()

    def stored_facts(self) -> list[str]:
        self.session.close()  # waits for the extraction to finish
        return [e.text for e in SteelMemory(self.home).entries()]

    def test_the_transcript_is_shown_before_it_is_used_and_enter_sends_it(
        self,
    ) -> None:
        out = self.run_chat(["", "/quit"])
        self.assertIn('You said: "My favourite colour is teal."', out)
        self.assertIn("Enter = send, Esc = discard", out)
        self.assertLess(out.index("You said"), out.index("steel>"))
        self.assertEqual(self.terminal.armed, 1)  # that Enter was the start key
        self.assertTrue(
            any("teal" in prompt for prompt in self.engine.chat_prompts)
            or self.engine.chat_prompts
        )

    def test_a_confirmed_transcript_goes_through_the_normal_fact_extraction(
        self,
    ) -> None:
        self.run_chat(["", "/quit"])
        self.assertEqual(self.stored_facts(), ["User's favourite colour is teal"])
        # The extractor sees only the user's words, in its usual "User: ..." form.
        self.assertEqual(
            self.engine.extractor_inputs, ["User: My favourite colour is teal."]
        )

    def test_a_confirmed_transcript_is_passed_to_ask_exactly_like_typed_text(
        self,
    ) -> None:
        with mock.patch.object(self.session, "ask", return_value="ok") as ask:
            self.run_chat(["", "typed message", "/quit"])
        self.assertEqual(
            [c.args[0] for c in ask.call_args_list],
            ["My favourite colour is teal.", "typed message"],
        )

    def test_a_typed_slash_command_runs_but_a_transcript_that_reads_like_one_does_not(
        self,
    ) -> None:
        # docs/INJECTION.md rule 3: only typed lines are commands.
        (self.home / "steel_policy.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "agent_id": "voice",
                            "grants": [
                                {
                                    "capability": "sensor:microphone",
                                    "pattern": "microphone_listen",
                                }
                            ],
                        },
                        {
                            "agent_id": "chat",
                            "grants": [
                                {
                                    "capability": "memory:read:facts",
                                    "pattern": "memory_search_facts",
                                }
                            ],
                        },
                    ]
                }
            )
        )
        (self.home / "steel_policy.json").chmod(0o600)
        SteelMemory(self.home).remember("User likes green tea")
        self.transcriber.text = "/search tea"
        self.terminal.confirms = [False, True]  # Esc on the result, Enter on the speech
        # The model's reply reads like a command too; it is printed, never run.
        with mock.patch.object(self.session, "ask", return_value="/search tea") as ask:
            out = self.run_chat(["/search tea", "", "/quit"])
        self.assertIn("Slash commands for agent 'chat': /search allowed", out)
        self.assertIn("User likes green tea", out)  # the typed command ran
        # The spoken "/search tea" went to the model as text and ran nothing.
        self.assertEqual([c.args[0] for c in ask.call_args_list], ["/search tea"])
        log = (self.home / "steel_policy_audit.jsonl").read_text().splitlines()
        searches = [x for x in log if "memory:read:facts" in x]
        self.assertEqual(len(searches), 1)

    def test_discarding_sends_nothing_and_remembers_nothing(self) -> None:
        self.terminal.confirms = [False]
        with mock.patch.object(self.session, "ask") as ask:
            out = self.run_chat(["", "/quit"])
        ask.assert_not_called()
        self.assertIn("Discarded. Nothing was sent or remembered.", out)
        self.assertEqual(self.engine.chat_prompts, [])
        self.assertEqual(self.stored_facts(), [])

    def test_a_discarded_transcript_never_reaches_the_fact_extractor(self) -> None:
        self.terminal.confirms = [False]
        self.run_chat(["", "/quit"])
        self.session.close()
        self.assertEqual(self.engine.extractor_inputs, [])  # not even a call

    def test_the_transcript_is_not_published_to_the_event_bus_when_discarded(
        self,
    ) -> None:
        self.terminal.confirms = [False]
        seen: list[Any] = []
        with mock.patch(
            "steel.chat.publish_completed_exchange",
            side_effect=lambda *a, **k: seen.append(a),
        ):
            self.run_chat(["", "/quit"])
        self.assertEqual(seen, [])

    def test_nothing_heard_sends_nothing(self) -> None:
        self.transcriber.text = ""
        with mock.patch.object(self.session, "ask") as ask:
            out = self.run_chat(["", "/quit"])
        ask.assert_not_called()
        self.assertIn("Nothing heard.", out)
        self.assertEqual(self.terminal.asked, 0)  # nothing to confirm

    def test_a_denied_microphone_is_reported_and_the_chat_goes_on(self) -> None:
        (self.home / "steel_policy.json").unlink()
        with mock.patch.object(self.session, "ask", return_value="ok") as ask:
            out = self.run_chat(["", "typed", "/quit"])
        self.assertIn("Not run: Capability 'sensor:microphone' denied", out)
        self.assertEqual(self.opened(), 0)
        ask.assert_called_once_with("typed")  # typing still works

    def test_typing_never_opens_the_microphone(self) -> None:
        with mock.patch.object(self.session, "ask", return_value="ok"):
            self.run_chat(["hello", "again", "/quit"])
        self.assertEqual(self.opened(), 0)
        self.assertEqual(self.screen.getvalue(), "")
        self.assertEqual(self.terminal.armed, 0)

    def test_the_microphone_opens_once_per_key_press(self) -> None:
        self.terminal.confirms = [True, True]
        with mock.patch.object(self.session, "ask", return_value="ok"):
            self.run_chat(["", "", "/quit"])
        self.assertEqual(self.opened(), 2)
        self.assertEqual(self.terminal.armed, 2)

    def test_leaving_still_waits_for_fact_extraction_at_most_30_seconds(self) -> None:
        with (
            mock.patch.object(self.session, "close", wraps=self.session.close) as close,
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("STEEL_EXTRACT_WAIT_SECONDS", None)
            out = self.run_chat(["/quit"])
        close.assert_called_once_with()  # default wait: extraction_wait_seconds()
        self.assertIn("Saving memory", out)
        from steel.chat import EXTRACTION_DRAIN_SECONDS, extraction_wait_seconds

        self.assertEqual(EXTRACTION_DRAIN_SECONDS, 30.0)
        self.assertEqual(extraction_wait_seconds(), 30.0)

    def test_replies_are_spoken_only_with_speak_and_a_local_backend(self) -> None:
        with mock.patch.object(self.session, "ask", return_value="Hola"):
            self.run_chat(["", "/quit"], speak=False, speaker=self.speaker)
        self.speaker.say.assert_not_called()
        self.terminal.confirms = [True]
        with mock.patch.object(self.session, "ask", return_value="Hola"):
            self.run_chat(["", "/quit"], speak=True, speaker=self.speaker)
        self.speaker.say.assert_called_once_with("Hola", "en")

    def test_speak_without_a_local_backend_says_so_and_stays_silent(self) -> None:
        with mock.patch.object(commands, "find_speaker", return_value=None):
            out = self.run_chat(["/quit"], speak=True)
        self.assertIn("No local text-to-speech is installed", out)


class ChatVoiceCommandTest(CliCase):
    def test_speak_needs_voice(self) -> None:
        with (
            self.assertRaises(SystemExit) as raised,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            main(["chat", "--speak"])
        self.assertEqual(raised.exception.code, 2)

    def test_voice_without_consent_stops_before_loading_a_model(self) -> None:
        with mock.patch("steel.chat.open_session") as opening:
            _out, err = self.run_cli("chat", "--voice", expect=1)
        opening.assert_not_called()
        self.assertIn("no voice consent", err)


class SpeakerTest(base.HomeTestCase):
    def test_the_text_goes_in_on_stdin_and_not_on_the_command_line(self) -> None:
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        EspeakSpeaker(run=run).say("secret reply", "es")
        (argv,), kwargs = run.call_args
        self.assertEqual(argv, ["espeak-ng", "--stdin", "-v", "es"])
        self.assertEqual(kwargs["input"], b"secret reply")
        self.assertNotIn("secret reply", " ".join(argv))

    def test_a_language_that_could_be_an_option_is_dropped(self) -> None:
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        EspeakSpeaker(run=run).say("hi", "--stdout")
        (argv,), _ = run.call_args
        self.assertEqual(argv, ["espeak-ng", "--stdin"])

    def test_long_replies_are_cut(self) -> None:
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        EspeakSpeaker(run=run).say("x" * 5000)
        self.assertEqual(len(run.call_args.kwargs["input"]), 2000)

    def test_failures_are_false_not_exceptions(self) -> None:
        self.assertFalse(EspeakSpeaker(run=mock.Mock(side_effect=OSError)).say("hi"))
        self.assertFalse(
            EspeakSpeaker(run=mock.Mock(return_value=mock.Mock(returncode=1))).say("hi")
        )

    def test_no_program_on_the_path_means_no_speaker(self) -> None:
        self.assertIsNone(find_speaker(which=lambda _name: None))
        self.assertIsInstance(find_speaker(which=lambda _name: "/bin/x"), EspeakSpeaker)


class LiveStateTest(base.HomeTestCase):
    """Guards for this test module itself: it must not touch the real home."""

    def test_the_home_is_the_temporary_one(self) -> None:
        self.assertNotEqual(self.home, Path.home() / ".openjarvis")
        self.assertTrue(str(self.home).startswith(str(self.home.parent)))
