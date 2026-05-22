"""
bot.backend.compact — context compaction package.

Public API
──────────
Thresholds:
  estimate_tokens(messages)           — rough token count
  should_auto_compact(messages)       — True when above threshold
  calculate_warning_state(messages)   — status-line info dict

Prompt:
  BASE_COMPACT_PROMPT                 — 9-section summarisation prompt
  format_compact_summary(raw)         — strip <analysis>, extract <summary>
  get_compact_user_summary_message(s) — wrap summary as prior-context user turn

Micro-compaction (no LLM):
  microcompact_messages(messages)     — clear oversized tool results

Full compaction:
  CompactionEngine                    — stateful session compactor
  compact_conversation(...)           — one-shot helper

Constants:
  COMPACTABLE_TOOLS                   — set of tools whose results are clearable
  CLEARED_MARKER                      — text inserted when a result is cleared
"""

from compact.thresholds import (
    AUTOCOMPACT_THRESHOLD,
    EFFECTIVE_CONTEXT_WINDOW,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    estimate_tokens,
    should_auto_compact,
    calculate_warning_state,
)

from compact.prompt import (
    BASE_COMPACT_PROMPT,
    PARTIAL_COMPACT_PROMPT,
    format_compact_summary,
    get_compact_prompt,
    get_compact_user_summary_message,
)

from compact.micro import (
    COMPACTABLE_TOOLS,
    CLEARED_MARKER,
    MAX_TOOL_RESULT_TOKENS,
    microcompact_messages,
    microcompact_stats,
)

from compact.compact import (
    CompactResult,
    CompactionEngine,
    compact_conversation,
    MAX_TOKENS_AFTER,
    MIN_TEXT_MESSAGES,
)

__all__ = [
    # thresholds
    "AUTOCOMPACT_THRESHOLD",
    "EFFECTIVE_CONTEXT_WINDOW",
    "MAX_OUTPUT_TOKENS_FOR_SUMMARY",
    "estimate_tokens",
    "should_auto_compact",
    "calculate_warning_state",
    # prompt
    "BASE_COMPACT_PROMPT",
    "PARTIAL_COMPACT_PROMPT",
    "format_compact_summary",
    "get_compact_prompt",
    "get_compact_user_summary_message",
    # micro
    "COMPACTABLE_TOOLS",
    "CLEARED_MARKER",
    "MAX_TOOL_RESULT_TOKENS",
    "microcompact_messages",
    "microcompact_stats",
    # full compaction
    "CompactResult",
    "CompactionEngine",
    "compact_conversation",
    "MAX_TOKENS_AFTER",
    "MIN_TEXT_MESSAGES",
]
