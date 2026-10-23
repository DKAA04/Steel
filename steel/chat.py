"""``steel chat``: a plain chat loop with OpenJarvis automatic memory.

No agent and no tools: every turn is one direct ``engine.generate`` call.
After each turn the exchange is published on the OpenJarvis event bus, where
the MemoryService picks it up, extracts facts with the local model in a
background thread and stores them in SteelFactStore.

The conversation itself is kept only in RAM and is never written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role
from openjarvis.memory.extractor import FactExtractor
from openjarvis.memory.service import MemoryService, publish_completed_exchange
from openjarvis.tools.storage.context import ContextConfig, inject_context

from steel.memory import SteelMemory

# Keep at most this many past messages in the prompt (RAM only).
MAX_HISTORY_MESSAGES = 20
# How long to wait on exit for pending fact extraction (slow on CPU).
EXTRACTION_DRAIN_SECONDS = 180.0

BASE_PROMPT = (
    "You are STEEL, a local personal assistant. You have no tools: you cannot "
    "browse, run code, read files or act outside this conversation. Answer "
    "briefly. If the user asks what you remember, answer only from the "
    "remembered facts and notes below; if nothing relevant is there, say so."
)


def _build_scanner() -> Any:
    """OpenJarvis' prompt-injection scanner, or None if it cannot load."""
    try:
        from openjarvis.security.injection_scanner import InjectionScanner

        return InjectionScanner()
    except Exception:  # noqa: BLE001 - the scanner is an optional defence layer
        return None


@dataclass
class ChatSession:
    """One conversation. History lives in RAM and dies with the process."""

    engine: Any
    model: str
    memory: SteelMemory
    bus: EventBus
    service: MemoryService | None
    history: list[Message] = field(default_factory=list)

    def _system_prompt(self) -> str:
        """Base prompt plus the user-authored MEMORY.md / USER.md entries."""
        parts = [BASE_PROMPT]
        for label, md in (
            ("Agent memory", self.memory.memory_md),
            ("User profile", self.memory.user_md),
        ):
            lines = [text for _id, text in md.entries()]
            if lines:
                parts.append(f"{label}:\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def ask(self, user_text: str) -> str:
        """Send one user message and return the reply."""
        messages = [Message(role=Role.SYSTEM, content=self._system_prompt())]
        messages += self.history[-MAX_HISTORY_MESSAGES:]
        messages.append(Message(role=Role.USER, content=user_text))
        # Add remembered facts. inject_context drops quarantined (untrusted)
        # facts; backend=None means no document store (memory.db) is opened.
        messages = inject_context(
            user_text,
            messages,
            None,
            config=ContextConfig(),
            facts=self.memory.facts.list(),
        )
        result = self.engine.generate(messages, model=self.model)
        reply = str(result.get("content", "") if isinstance(result, dict) else result)
        # Remember the turn in RAM for the rest of this session only.
        self.history.append(Message(role=Role.USER, content=user_text))
        self.history.append(Message(role=Role.ASSISTANT, content=reply))
        # Hand the exchange to the MemoryService (non-blocking).
        publish_completed_exchange(self.bus, user_text, reply, source="steel")
        return reply

    def close(self, timeout: float = EXTRACTION_DRAIN_SECONDS) -> None:
        """Stop the memory worker after it finishes queued extractions."""
        if self.service is not None:
            self.service.stop(timeout=timeout)


def open_session(
    config: Any,
    home: Path,
    *,
    engine: Any = None,
    model: str = "",
) -> ChatSession:
    """Wire engine, guardrails and memory exactly as ``steel chat`` uses them.

    Tests pass a fake ``engine``; the real CLI lets OpenJarvis pick one.
    """
    bus = EventBus()
    if engine is None:
        from openjarvis.engine import get_engine

        resolved = get_engine(config)
        if resolved is None:
            raise RuntimeError("No inference engine available (is Ollama running?).")
        _name, engine = resolved
    model = model or config.intelligence.default_model
    if not model:
        raise RuntimeError("No model configured ([intelligence] default_model).")

    # OpenJarvis guardrails: redact secrets/PII in model input and output.
    # Its audit logger writes pattern names (not text) to audit.db.
    from openjarvis.security import setup_security

    engine = setup_security(config, engine, bus).engine

    memory = SteelMemory(home, max_facts=config.memory.max_facts)
    service = None
    if config.memory.enabled:
        extractor = FactExtractor(engine, config.memory.extraction_model or model)
        # Our own store instead of build_memory_service(), which would create a
        # plain LocalFactStore without forget-by-id or the forgotten list.
        service = MemoryService(
            memory.facts, extractor, event_bus=bus, scanner=_build_scanner()
        )
        service.start()
    return ChatSession(
        engine=engine, model=model, memory=memory, bus=bus, service=service
    )


def run_repl(session: ChatSession) -> None:
    """Interactive loop. Type /quit (or Ctrl-D) to leave."""
    print(f"STEEL chat - model {session.model}. No tools. Type /quit to exit.")
    if session.service is None:
        print("Automatic memory is OFF ([memory] enabled = false).")
    try:
        while True:
            try:
                text = input("you> ").strip()
            except EOFError:
                break
            if text in ("/quit", "/exit"):
                break
            if not text:
                continue
            print("steel>", session.ask(text))
    except KeyboardInterrupt:
        print()
    finally:
        print("Saving memory (waiting for fact extraction to finish)...")
        session.close()
