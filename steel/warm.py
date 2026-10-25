"""Keep the chat model loaded: a warm-up, a keep-alive and one context size.

These apply only to an OpenJarvis ``OllamaEngine`` on this computer (the
loopback check that vision uses). Another engine, or a remote host, gets
none of them, so no new request ever leaves the machine (docs/LATENCY.md 8).

* The warm-up is one request that carries the system prompt the first real
  turn will send (docs/LATENCY.md 9), plus the fixed word ``ready``. It runs in
  a background thread so it never delays the first prompt. Ollama loads the
  model and processes that prompt before the owner's first message, and keeps
  the processed prefix for the first real turn.
* ``num_ctx``: OpenJarvis asks Ollama for a 16,384-token context unless the
  request says otherwise, and Ollama reloads a model when the context of two
  requests differs. STEEL's transport sets ONE ``num_ctx`` on every request
  its engine sends (chat, warm-up, fact extraction), and vision uses the same.
* Ollama unloads an idle model after its keep-alive (default 5 minutes).
  Every request may carry its own ``keep_alive``, and the latest one wins,
  but OpenJarvis' engine does not send that field. With
  ``STEEL_OLLAMA_KEEP_ALIVE`` set, STEEL adds it to every chat request its
  engine sends. Unset, Ollama's own default (``OLLAMA_KEEP_ALIVE``) applies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from typing import Any

import httpx
from openjarvis.core.types import Message, Role

KEEP_ALIVE_ENV = "STEEL_OLLAMA_KEEP_ALIVE"
NUM_CTX_ENV = "STEEL_OLLAMA_NUM_CTX"
# 4096 is Ollama's own default and costs the least RAM; 2048 is the smallest that
# still holds the system prompt and a few facts; 32768 is far more than a 2 GB
# model on this RAM can use.
DEFAULT_NUM_CTX = 4096
MIN_NUM_CTX, MAX_NUM_CTX = 2048, 32768
# A whole number of seconds, minutes or hours, at most a day. Not "-1"
# (forever) or "0" (unload at once): a typo must not pin 3 GB in RAM for good.
_DURATION = re.compile(r"^([1-9][0-9]{0,4})([smh])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
MAX_KEEP_ALIVE_SECONDS = 24 * 3600
WARMUP_TEXT = "ready"
_PATHS = frozenset({"/api/chat", "/api/generate"})
_log = logging.getLogger(__name__)


def keep_alive_setting(env: Mapping[str, str] | None = None) -> tuple[str | None, str]:
    """($STEEL_OLLAMA_KEEP_ALIVE or None, a problem to show or "")."""
    env = os.environ if env is None else env
    raw = env.get(KEEP_ALIVE_ENV, "").strip()
    if not raw:
        return None, ""
    match = _DURATION.match(raw)
    if match and int(match[1]) * _UNIT_SECONDS[match[2]] <= MAX_KEEP_ALIVE_SECONDS:
        return raw, ""
    return None, (
        f"{KEEP_ALIVE_ENV}={raw!r} is not a duration from 1s to 24h such as 30m; "
        "Ollama's own keep-alive applies."
    )


def num_ctx_setting(env: Mapping[str, str] | None = None) -> tuple[int, str]:
    """($STEEL_OLLAMA_NUM_CTX or 4096, a problem to show or "")."""
    env = os.environ if env is None else env
    raw = env.get(NUM_CTX_ENV, "").strip()
    if not raw:
        return DEFAULT_NUM_CTX, ""
    if raw.isdigit() and MIN_NUM_CTX <= int(raw) <= MAX_NUM_CTX:
        return int(raw), ""
    return DEFAULT_NUM_CTX, (
        f"{NUM_CTX_ENV}={raw!r} is not a whole number from {MIN_NUM_CTX} to "
        f"{MAX_NUM_CTX}; {DEFAULT_NUM_CTX} is used."
    )


def local_ollama(engine: Any) -> Any:
    """The OpenJarvis OllamaEngine behind *engine* if it is on this computer."""
    from openjarvis.engine.ollama import OllamaEngine

    from steel.vision.describe import VisionUnavailable, require_loopback

    # setup_security wraps the engine in GuardrailsEngine, which keeps it here.
    inner = getattr(engine, "_engine", engine)
    if not isinstance(inner, OllamaEngine):
        return None
    try:
        require_loopback(inner._host)
    except VisionUnavailable:
        return None
    return inner


class OllamaRequestTransport(httpx.BaseTransport):
    """Sets ``num_ctx`` (and ``keep_alive``, if given) on Ollama chat and generate
    requests, and changes nothing else."""

    def __init__(
        self, keep_alive: str | None, num_ctx: int, inner: httpx.BaseTransport
    ) -> None:
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path in _PATHS:
            try:
                body = json.loads(request.read())
            except ValueError:
                body = None
            if isinstance(body, dict):
                # Forced, not "if absent": a request with another context would
                # make Ollama reload the model (OpenJarvis' own default is 16384).
                options = body.get("options")
                body["options"] = {
                    **(options if isinstance(options, dict) else {}),
                    "num_ctx": self.num_ctx,
                }
                if self.keep_alive is not None and "keep_alive" not in body:
                    body["keep_alive"] = self.keep_alive
                headers = [
                    (k, v)
                    for k, v in request.headers.multi_items()
                    if k.lower() != "content-length"
                ]
                request = httpx.Request(
                    request.method,
                    request.url,
                    headers=headers,
                    content=json.dumps(body).encode(),
                    extensions=request.extensions,
                )
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def install_request_settings(
    engine: Any,
    keep_alive: str | None,
    num_ctx: int,
    *,
    inner: httpx.BaseTransport | None = None,
) -> bool:
    """Give a local Ollama engine a client that sends *num_ctx* and *keep_alive*.

    Whether it did.
    """
    ollama = local_ollama(engine)
    if ollama is None:
        return False
    old = ollama._client
    # Same address and timeout as OpenJarvis' own client. trust_env=False: a
    # proxy variable must never send a loopback request through a proxy.
    ollama._client = httpx.Client(
        base_url=ollama._host,
        timeout=ollama._timeout,
        transport=OllamaRequestTransport(
            keep_alive, num_ctx, inner or httpx.HTTPTransport()
        ),
        trust_env=False,
    )
    old.close()
    return True


def _warm(engine: Any, model: str, messages: list[Message]) -> None:
    try:
        engine.generate(messages, model=model, max_tokens=1)
    except Exception as exc:  # noqa: BLE001 - a failed warm-up only costs time
        _log.debug("warm-up failed: %s", type(exc).__name__)


def start_warmup(
    engine: Any, model: str, messages: list[Message] | None = None
) -> threading.Thread | None:
    """Load the model in the background; never waits. None if not local Ollama.

    *messages* is the first turn's prompt without the owner's message, ending in
    the fixed word. The default is that word alone.
    """
    if local_ollama(engine) is None:
        return None
    if messages is None:
        messages = [Message(role=Role.USER, content=WARMUP_TEXT)]
    thread = threading.Thread(
        target=_warm, args=(engine, model, messages), name="steel-warmup", daemon=True
    )
    thread.start()
    return thread
