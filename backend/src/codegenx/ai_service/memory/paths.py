"""
Filesystem path helpers for all memory tiers.

Layout:
  ~/.bot/memory/MEMORY.md          ← hot tier  (global, session-start load)
  ~/.bot/memory/topics/*.md        ← warm tier (per-query keyword recall)
  ~/.bot/sessions/<id>/MEMORY.md   ← session tier (background extraction)
"""
from __future__ import annotations

from pathlib import Path

from shared.constants import get_memory_dir, get_session_dir


def get_hot_memory_path(app_id:str) -> Path:
    """Global persistent memory: ~/.bot/memory/MEMORY.md"""
    return get_memory_dir(app_id) / "MEMORY.md"



def get_session_memory_path(app_id:str,session_id: str) -> Path:
    """Session memory file: ~/.data/app_id/session/<session_id>/MEMORY.md"""
    return get_session_dir(app_id) / session_id / "MEMORY.md"
