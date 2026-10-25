"""Phase 5b-2: one context window for every request, and a warm prompt prefix.

An httpx.MockTransport stands in for the Ollama server behind a real OpenJarvis
OllamaEngine. No model, Ollama, network, engine or player is used.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import threading
from typing import Any
from unittest import mock

import test_steel as base
from openjarvis.core.types import Message, Role
from test_latency import OllamaFake, no_network

from steel import warm as warm_module
from steel.chat import BASE_PROMPT, MAX_HISTORY_MESSAGES, open_session
from steel.cli import _open_chat
from steel.context import REPLY_RESERVE_TOKENS, estimate_tokens, fit_to_context
from steel.memory import SteelMemory
from steel.vision.config import vision_config
from steel.vision.describe import OllamaDescriber
from steel.warm import (
    DEFAULT_NUM_CTX,
    KEEP_ALIVE_ENV,
    NUM_CTX_ENV,
    WARMUP_TEXT,
    num_ctx_setting,
    start_warmup,
)


class NumCtxSettingTest(base.HomeTestCase):
    def test_only_a_number_from_2048_to_32768_is_accepted(self) -> None:
        for good in ("2048", "4096", "8192", "32768"):
            self.assertEqual(num_ctx_setting({NUM_CTX_ENV: good}), (int(good), ""))
        self.assertEqual(num_ctx_setting({}), (DEFAULT_NUM_CTX, ""))
        for bad in ("1024", "2047", "32769", "65536", "-1", "0", "4k", "40.5", " x "):
            value, problem = num_ctx_setting({NUM_CTX_ENV: bad})
            self.assertEqual(value, 4096, bad)
            self.assertIn("is not a whole number from 2048 to 32768", problem)

    def test_a_bad_setting_is_reported_at_start_and_4096_kept(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {NUM_CTX_ENV: "huge"}),
            mock.patch("steel.chat.open_session"),
            mock.patch("steel.warm.start_warmup"),
            contextlib.redirect_stderr(err),
        ):
            _open_chat(self.config(), self.home)
        self.assertIn(
            "STEEL_OLLAMA_NUM_CTX='huge' is not a whole number", err.getvalue()
        )
        self.assertEqual(vision_config({NUM_CTX_ENV: "huge"}).num_ctx, 4096)


class SameContextEverywhereTest(base.HomeTestCase):
    """One num_ctx on the chat turn, the warm-up, the extractor and the vision
    call, whatever OpenJarvis' own default (16384, or $JARVIS_NUM_CTX) is."""

    def setUp(self) -> None:
        super().setUp()
        self.ollama = OllamaFake()

    def run_all_four(self, env: dict[str, str]) -> dict[str, Any]:
        engine = self.ollama.engine()
        full_env = {"JARVIS_NUM_CTX": "16384", **env}
        with (
            mock.patch.dict(os.environ, full_env),
            mock.patch.object(
                warm_module.httpx, "HTTPTransport", self.ollama.transport
            ),
            no_network(),
        ):
            session = self.session(engine)  # type: ignore[arg-type]
            warm = start_warmup(
                session.engine, session.model, session.warmup_messages()
            )
            warm.join(5)
            session.ask("hello there")
            session.close(5)  # waits for the extraction request
            # The vision call builds its own engine, like `steel look` does.
            describer = OllamaDescriber(vision_config(), "http://127.0.0.1:11434")
            describer.describe(base64.b64encode(b"x").decode(), "what is there?")
        found: dict[str, Any] = {}
        for body in self.ollama.bodies:
            first, last = body["messages"][0], body["messages"][-1]
            if body["messages"][-1].get("images"):
                kind = "vision"
            elif first["content"] == base.EXTRACTOR_PROMPT:
                kind = "extractor"
            elif last["content"] == WARMUP_TEXT:
                kind = "warm-up"
            else:
                kind = "chat"
            found[kind] = body["options"]["num_ctx"]
        return found

    def test_the_same_num_ctx_reaches_all_four_call_sites(self) -> None:
        found = self.run_all_four({NUM_CTX_ENV: "6144"})
        self.assertEqual(
            found,
            {"warm-up": 6144, "chat": 6144, "extractor": 6144, "vision": 6144},
        )

    def test_the_default_is_4096_even_when_openjarvis_would_ask_for_16384(
        self,
    ) -> None:
        found = self.run_all_four({})
        self.assertEqual(set(found), {"warm-up", "chat", "extractor", "vision"})
        self.assertEqual(set(found.values()), {4096})

    def test_the_vision_engine_also_carries_the_keep_alive(self) -> None:
        self.run_all_four({KEEP_ALIVE_ENV: "30m"})
        self.assertEqual({b["keep_alive"] for b in self.ollama.bodies}, {"30m"})


def long_history(count: int, size: int) -> list[Message]:
    roles = (Role.USER, Role.ASSISTANT)
    return [
        Message(role=roles[i % 2], content=f"m{i:02d} " + "word " * size)
        for i in range(count)
    ]


class FitToContextTest(base.HomeTestCase):
    def test_a_long_history_is_cut_from_the_oldest_end_to_fit(self) -> None:
        for num_ctx in (2048, 4096, 8192):
            session = self.session()
            self.addCleanup(session.close, 5)
            session.num_ctx = num_ctx
            session.history = long_history(MAX_HISTORY_MESSAGES + 6, 200)
            messages = session.build_messages("the newest question")
            self.assertLessEqual(
                estimate_tokens(messages), num_ctx - REPLY_RESERVE_TOKENS, num_ctx
            )
            self.assertIs(messages[0].role, Role.SYSTEM)
            self.assertIn(BASE_PROMPT, messages[0].content)
            self.assertEqual(messages[-1].content, "the newest question")
            kept = [m.content[:3] for m in messages[1:-1]]
            if num_ctx < 8192:
                self.assertLess(len(kept), MAX_HISTORY_MESSAGES, num_ctx)
            # What stays is the newest end of the history, in order.
            newest = [m.content[:3] for m in session.history[-len(kept) :]]
            self.assertEqual(kept, newest if kept else [], num_ctx)
            if kept:
                self.assertIsNot(messages[1].role, Role.ASSISTANT)

    def test_a_short_history_is_sent_whole(self) -> None:
        session = self.session()
        self.addCleanup(session.close, 5)
        session.history = long_history(4, 5)
        messages = session.build_messages("hi")
        self.assertEqual(len(messages), 1 + 4 + 1)

    def test_the_system_prompt_and_new_message_are_never_dropped(self) -> None:
        huge = [
            Message(role=Role.SYSTEM, content="s" * 20000),
            *long_history(4, 10),
            Message(role=Role.USER, content="q" * 20000),
        ]
        kept = fit_to_context(huge, 2048)
        self.assertEqual([m.content[:1] for m in kept], ["s", "q"])

    def test_the_facts_get_at_most_a_quarter_of_the_window(self) -> None:
        memory = SteelMemory(self.home)
        for i in range(60):
            memory.remember(f"Fact number {i} is " + "long " * 80 + f"end{i}.")
        session = self.session()
        self.addCleanup(session.close, 5)
        session.num_ctx = 2048
        messages = session.build_messages("hello")
        self.assertLessEqual(
            estimate_tokens(messages), 2048 - REPLY_RESERVE_TOKENS + 600
        )
        words = len(messages[0].content.split())
        self.assertLess(words, 512 + len(BASE_PROMPT.split()) + 50)


class WarmPrefixTest(base.HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ollama = OllamaFake()
        os.environ.pop(KEEP_ALIVE_ENV, None)
        patch = mock.patch.object(
            warm_module.httpx, "HTTPTransport", self.ollama.transport
        )
        patch.start()
        self.addCleanup(patch.stop)
        memory = SteelMemory(self.home)
        memory.remember("My cat is called Biscuit.")
        memory.remember("Dentist visit", days=2)
        self.session_ = self.session(self.ollama.engine())  # type: ignore[arg-type]
        self.addCleanup(self.session_.close, 5)

    def warm_then_ask(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with no_network():
            warm = start_warmup(
                self.session_.engine,
                self.session_.model,
                self.session_.warmup_messages(),
            )
            warm.join(5)
            self.session_.ask("hello there")
        warm, chat = self.ollama.bodies[0], self.ollama.bodies[1]
        return warm, chat

    def test_the_warm_up_is_byte_identical_to_the_first_turn_before_the_message(
        self,
    ) -> None:
        warm, chat = self.warm_then_ask()
        self.assertEqual(warm["path"], "/api/chat")
        self.assertEqual(warm["messages"][:-1], chat["messages"][:-1])
        system = warm["messages"][0]["content"]
        self.assertIn(BASE_PROMPT, system)
        self.assertIn("Biscuit", system)  # remembered facts are in the prefix
        self.assertIn("Dentist visit", system)  # and so are the live notes
        self.assertEqual(warm["options"]["num_predict"], 1)
        self.assertEqual(warm["options"]["num_ctx"], chat["options"]["num_ctx"])

    def test_the_two_requests_differ_only_in_the_last_message_and_the_token_limit(
        self,
    ) -> None:
        """Everything Ollama sees apart from those two is identical (docs/LATENCY.md
        section 10): model, stream, think, keep-alive, temperature, num_ctx, and
        no seed, format or tools on either."""
        os.environ[KEEP_ALIVE_ENV] = "30m"
        self.addCleanup(os.environ.pop, KEEP_ALIVE_ENV, None)
        session = self.session(self.ollama.engine())  # type: ignore[arg-type]
        self.addCleanup(session.close, 5)
        with no_network():
            start_warmup(session.engine, session.model, session.warmup_messages()).join(
                5
            )
            session.ask("hello there")
        warm, chat = self.ollama.bodies[-2], self.ollama.bodies[-1]
        self.assertEqual(warm["messages"][-1]["content"], WARMUP_TEXT)
        self.assertEqual(chat["messages"][-1]["content"], "hello there")
        self.assertEqual(warm["options"]["num_predict"], 1)
        self.assertEqual(chat["options"]["num_predict"], 1024)
        for body in (warm, chat):
            body["messages"] = body["messages"][:-1]
            del body["options"]["num_predict"]
        self.assertEqual(warm, chat)
        self.assertEqual(
            sorted(warm),
            ["keep_alive", "messages", "model", "options", "path", "stream", "think"],
        )
        self.assertEqual(sorted(warm["options"]), ["num_ctx", "temperature"])
        self.assertIs(warm["stream"], False)
        self.assertIs(warm["think"], False)

    def test_the_warm_up_ends_in_the_fixed_word_and_holds_no_message_of_the_owner(
        self,
    ) -> None:
        warm, _ = self.warm_then_ask()
        users = [m for m in warm["messages"] if m["role"] == "user"]
        self.assertEqual(users, [{"role": "user", "content": WARMUP_TEXT}])
        self.assertEqual(warm["messages"][-1]["content"], WARMUP_TEXT)

    def test_the_start_up_sends_that_warm_up_in_a_background_thread(self) -> None:
        self.ollama.gate.clear()  # keep the request open so the thread can be seen
        self.addCleanup(self.ollama.gate.set)
        engine = self.ollama.engine()
        with (
            mock.patch(
                "steel.chat.open_session",
                side_effect=lambda config, home: open_session(
                    config, home, engine=engine
                ),
            ),
            no_network(),
        ):
            session = _open_chat(self.config(), self.home)
        self.addCleanup(session.close, 5)
        (warm,) = [t for t in threading.enumerate() if t.name == "steel-warmup"]
        self.ollama.gate.set()
        warm.join(5)
        (body,) = self.ollama.bodies
        self.assertIn("Biscuit", body["messages"][0]["content"])
        self.assertEqual(body["messages"][-1]["content"], WARMUP_TEXT)

    def test_a_stuck_warm_up_is_a_daemon_that_never_holds_up_the_chat_or_exit(
        self,
    ) -> None:
        self.ollama.gate.clear()  # Ollama never answers
        self.addCleanup(self.ollama.gate.set)
        with no_network():
            warm = start_warmup(
                self.session_.engine,
                self.session_.model,
                self.session_.warmup_messages(),
            )
        assert warm.thread is not None
        self.assertTrue(warm.thread.daemon)  # the process can end while it waits
        self.assertTrue(warm.thread.is_alive())
        self.assertEqual(warm.status, "running")

    def test_a_remote_engine_gets_no_warm_up_with_memory_in_it(self) -> None:
        engine = self.ollama.engine("http://192.0.2.10:11434")
        with no_network():
            warm = start_warmup(engine, "m", self.session_.warmup_messages())
            self.assertEqual((warm.status, warm.reason), ("skipped", "not-local"))
        self.assertEqual(self.ollama.bodies, [])
