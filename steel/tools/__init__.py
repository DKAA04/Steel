"""STEEL's tools. None is registered anywhere: build them here, run them with
``steel.security.build_executor`` (docs/POLICY.md)."""

from __future__ import annotations

from pathlib import Path

from openjarvis.tools._stubs import BaseTool

from steel.memory import SteelMemory
from steel.tools.memory_search import MemorySearchFacts, MemorySearchNotes


def get_memory_tools(home: Path) -> list[BaseTool]:
    """The read-only memory tools for the memory in *home*.

    Building them reads and writes nothing; each call to a tool re-reads the
    stores, so it sees changes made after this function ran.
    """
    memory = SteelMemory(home)
    return [MemorySearchFacts(memory), MemorySearchNotes(memory)]


__all__ = ["MemorySearchFacts", "MemorySearchNotes", "get_memory_tools"]
