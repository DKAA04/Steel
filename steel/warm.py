"""Keep the chat model loaded: a warm-up at start and an optional keep-alive.

Both apply only to an OpenJarvis ``OllamaEngine`` on this computer (the
loopback check that vision uses). Another engine, or a remote host, gets
neither, so no new request ever leaves the machine (docs/LATENCY.md 8).

* The warm-up is one request with a fixed one-word prompt and no user text,
  sent in a background thread so it never delays the first prompt. Ollama
  loads the model to answer it, before the owner's first message.
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


class KeepAliveTransport(httpx.BaseTransport):
    """Adds ``keep_alive`` to Ollama chat and generate requests, nothing else."""

    def __init__(self, keep_alive: str, inner: httpx.BaseTransport) -> None:
        self.keep_alive = keep_alive
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path in _PATHS:
            try:
                body = json.loads(request.read())
            except ValueError:
                body = None
            if isinstance(body, dict) and "keep_alive" not in body:
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


def install_keep_alive(
    engine: Any, keep_alive: str | None, *, inner: httpx.BaseTransport | None = None
) -> bool:
    """Give a local Ollama engine a client that sends *keep_alive*. Whether it did."""
    ollama = local_ollama(engine)
    if keep_alive is None or ollama is None:
        return False
    old = ollama._client
    # Same address and timeout as OpenJarvis' own client. trust_env=False: a
    # proxy variable must never send a loopback request through a proxy.
    ollama._client = httpx.Client(
        base_url=ollama._host,
        timeout=ollama._timeout,
        transport=KeepAliveTransport(keep_alive, inner or httpx.HTTPTransport()),
        trust_env=False,
    )
    old.close()
    return True


def _warm(engine: Any, model: str) -> None:
    try:
        engine.generate(
            [Message(role=Role.USER, content=WARMUP_TEXT)], model=model, max_tokens=1
        )
    except Exception as exc:  # noqa: BLE001 - a failed warm-up only costs time
        _log.debug("warm-up failed: %s", type(exc).__name__)


def start_warmup(engine: Any, model: str) -> threading.Thread | None:
    """Load the model in the background; never waits. None if not local Ollama."""
    if local_ollama(engine) is None:
        return None
    thread = threading.Thread(
        target=_warm, args=(engine, model), name="steel-warmup", daemon=True
    )
    thread.start()
    return thread
