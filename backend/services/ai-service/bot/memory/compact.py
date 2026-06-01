"""
Context compaction — trim a long conversation while preserving session memory.

Mirrors Claude Code's src/services/compact/sessionMemoryCompact.ts:

  1. Detect when total context tokens exceed COMPACT_TRIGGER_TOKENS.
  2. Walk recent messages backwards, keeping the minimum needed to stay
     coherent (MIN_TEXT_MESSAGES) and as much as fits in MAX_TOKENS_AFTER.
  3. Prepend a synthetic "prior context" exchange carrying the session
     summary so the model is not amnesic after compaction.
  4. Return the new, shorter message list.  (The caller replaces its own
     list with the result.)

Mirrors Claude Code constants (scaled to mock-LLM size):
  Production:  COMPACT_TRIGGER = context_limit × 0.95  (~190K tokens)
               MAX_TOKENS_AFTER = 40_000
               MIN_TEXT_MESSAGES = 5
  Here:        COMPACT_TRIGGER = 2_000 (rough chars/4 estimate)
               MAX_TOKENS_AFTER = 800
               MIN_TEXT_MESSAGES = 5

Raises no exceptions — callers should treat compaction as best-effort.
"""
from __future__ import annotations

from bot.utils.context_utils import rough_tokens as estimate_tokens

# ── Thresholds ────────────────────────────────────────────────────────────────
# Rough token estimate = total chars / 4.
# These numbers are intentionally small so the mock LLM triggers compaction
# within a short demo session. Raise them for a real LLM deployment.

COMPACT_TRIGGER_TOKENS = 2_000    # fire compaction above this
MAX_TOKENS_AFTER_COMPACT = 800    # hard cap on kept context after compaction
MIN_TEXT_MESSAGES = 5             # always keep this many text exchanges


def should_compact(messages: list[dict]) -> bool:
    """Return True when compaction should be triggered."""
    return estimate_tokens(messages) >= COMPACT_TRIGGER_TOKENS


# ── Compaction ────────────────────────────────────────────────────────────────

def compact(messages: list[dict], session_summary: str) -> list[dict]:
    """
    Compact *messages*, prepending *session_summary* as prior context.

    Strategy (mirrors buildPostCompactMessages + annotateBoundaryWithPreservedSegment):
      1. Walk backwards through messages, accumulating recent ones.
      2. Always keep at least MIN_TEXT_MESSAGES text-bearing messages.
      3. Stop once adding another message would exceed MAX_TOKENS_AFTER_COMPACT.
      4. Prepend two synthetic messages:
           user:      "[Prior context summary — conversation was compacted]\\n<summary>"
           assistant: "Understood. I have the prior context."
      5. Return the new list.  The caller replaces self._messages with it.

    Args:
        messages:        Full conversation history.
        session_summary: Content of the session MEMORY.md (may be empty).

    Returns:
        Compacted message list.
    """
    if not messages:
        return messages

    kept: list[dict] = []
    tokens = 0
    text_count = 0

    for msg in reversed(messages):
        msg_tokens = estimate_tokens([msg])
        has_text = _has_text(msg)

        # Always take the minimum required messages
        if text_count < MIN_TEXT_MESSAGES:
            kept.insert(0, msg)
            tokens += msg_tokens
            if has_text:
                text_count += 1
            continue

        # Beyond the minimum, stop if we would exceed the cap
        if tokens + msg_tokens > MAX_TOKENS_AFTER_COMPACT:
            break

        kept.insert(0, msg)
        tokens += msg_tokens
        if has_text:
            text_count += 1

    # Build compacted message list
    result: list[dict] = []

    if session_summary:
        result.append({
            "role": "user",
            "content": (
                "[Prior context summary — conversation was compacted]\n\n"
                + session_summary
            ),
        })
        result.append({
            "role": "assistant",
            "content": (
                "Understood. I have read the prior session context and will "
                "continue from where we left off."
            ),
        })

    result.extend(kept)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_text(msg: dict) -> bool:
    """True if *msg* carries human-readable text (not only tool results)."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(item, dict)
            and item.get("type") == "text"
            and str(item.get("text", "")).strip()
            for item in content
        )
    return False
