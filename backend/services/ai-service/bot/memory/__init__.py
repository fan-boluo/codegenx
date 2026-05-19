"""
bot.memory — multi-tier memory system for the agent bot.

Architecture (mirrors Claude Code):

  Hot tier    (hot.py):     ~/.bot/memory/MEMORY.md
                            Loaded once at session start → always in system prompt.
                            Cheap: one file read, no LLM.

  Warm tier   (warm.py):    ~/.bot/memory/topics/*.md
                            Topic files recalled per query by keyword overlap.
                            Capped: ≤5 files, ≤4 KB each, ≤60 KB per session.
                            Production: replace keyword scorer with Sonnet sideQuery.

  Session tier (session.py): ~/.bot/sessions/<id>/MEMORY.md
                            Background extractor fires every ~3 tool calls or
                            ~150 new tokens; writes a structured session summary.
                            Non-blocking: runs as an asyncio.Task.
                            Used by compaction to survive context trimming.

  Compact      (compact.py): Trims messages when context > 2 000 est. tokens.
                            Keeps recent exchanges + prepends session summary.

Warm topic file format:
  ---
  type: topic
  description: One-line description for keyword matching
  ---
  # Title
  ... markdown content ...
"""
from .hot import load_hot_memory, format_hot_memory_prompt, append_to_hot_memory
from .warm import find_relevant_topics, format_warm_memory_prompt, scan_topic_files
from .session import SessionMemory
from .compact import should_compact, compact, estimate_tokens

__all__ = [
    # Hot tier
    "load_hot_memory",
    "format_hot_memory_prompt",
    "append_to_hot_memory",
    # Warm tier
    "find_relevant_topics",
    "format_warm_memory_prompt",
    "scan_topic_files",
    # Session tier
    "SessionMemory",
    # Compaction
    "should_compact",
    "compact",
    "estimate_tokens",
]
