"""
Hot memory tier — MEMORY.md loaded at session start.

Mirrors Claude Code's loadMemoryPrompt():
  - Reads ~/.bot/memory/MEMORY.md
  - Caps at MAX_LINES (200 lines) and MAX_BYTES (25 KB)
  - Injected into the system prompt at session start and every turn

This is the cheapest tier: no LLM call, no disk scan, just one file read.
"""
from __future__ import annotations

from pathlib import Path

from .paths import get_hot_memory_path

MAX_LINES = 200
MAX_BYTES = 25 * 1024


def load_hot_memory(app_id,path: Path | None = None) -> str:
    """
    Read MEMORY.md (hot tier).

    Returns the content as a plain string, or "" if the file does not exist.
    Enforces MAX_LINES and MAX_BYTES caps (same as Claude Code).
    """
    path = path or get_hot_memory_path(app_id)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Byte cap first (prevents huge files from blowing the context window)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        text = encoded[:MAX_BYTES].decode("utf-8", errors="ignore")

    # Line cap
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]

    return "\n".join(lines).strip()


def format_hot_memory_prompt(content: str) -> str:
    """Wrap hot memory content in a labelled system-prompt section."""
    if not content:
        return ""
    return f"# Persistent Memory (MEMORY.md)\n{content}"


def append_to_hot_memory(fact: str, path: Path | None = None) -> None:
    """
    Append a single fact line to MEMORY.md (persists across all sessions).

    Mirrors the `remember()` contract from the old MemoryManager stub,
    now backed by disk instead of an in-process list.
    """
    path = path or get_hot_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- {fact}")
