"""
Filesystem path helpers for all memory tiers.

Layout:
  ~/.bot/memory/MEMORY.md          ← hot tier  (global, session-start load)
  ~/.bot/memory/topics/*.md        ← warm tier (per-query keyword recall)
  ~/.bot/sessions/<id>/MEMORY.md   ← session tier (background extraction)
"""
from __future__ import annotations

from pathlib import Path


def get_bot_home() -> Path:
    """Root directory for bot persistent data: ~/.bot"""
    return Path.home() / ".bot"


def get_memory_dir() -> Path:
    """Hot + warm parent directory: ~/.bot/memory/  (created on demand)"""
    d = get_bot_home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_hot_memory_path() -> Path:
    """Global persistent memory: ~/.bot/memory/MEMORY.md"""
    return get_memory_dir() / "MEMORY.md"


def get_topics_dir() -> Path:
    """Warm topic files: ~/.bot/memory/topics/  (created on demand)"""
    d = get_memory_dir() / "topics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_session_dir(session_id: str) -> Path:
    """Per-session directory: ~/.bot/sessions/<session_id>/  (created on demand)"""
    d = get_bot_home() / "sessions" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_session_memory_path(session_id: str) -> Path:
    """Session memory file: ~/.bot/sessions/<session_id>/MEMORY.md"""
    return get_session_dir(session_id) / "MEMORY.md"
