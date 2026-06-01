"""
Token thresholds, auto-compact gate, and circuit breaker.

Mirrors:
  src/services/compact/autoCompact.ts — constants, shouldAutoCompact(),
                                        autoCompactIfNeeded(), circuit breaker

Production numbers (for reference):
  Model context window:         200,000 tokens  (Claude Sonnet)
  Reserved for compact output:   20,000 tokens  (MAX_OUTPUT_TOKENS_FOR_SUMMARY)
  effectiveContextWindow:       180,000 tokens
  AUTOCOMPACT_BUFFER_TOKENS:     13,000 tokens
  Trigger threshold:            167,000 tokens  (effectiveWindow − buffer)
  WARNING_THRESHOLD_BUFFER:      20,000 tokens
  Manual compact buffer:          3,000 tokens

Here we scale to the mock LLM so compaction triggers in short demo sessions.
Set COMPACT_CONTEXT_WINDOW to a larger value for a real deployment.
"""
from __future__ import annotations

import os

# ── Context window configuration ──────────────────────────────────────────────

# Mock: small so compaction triggers quickly.
# Real deployment: set this to your model's context window (e.g. 200_000).
COMPACT_CONTEXT_WINDOW: int = int(
    os.environ.get("BOT_CONTEXT_WINDOW", "4_000")
)

# Tokens reserved for compact summary output (mirrors MAX_OUTPUT_TOKENS_FOR_SUMMARY)
MAX_OUTPUT_TOKENS_FOR_SUMMARY = min(500, COMPACT_CONTEXT_WINDOW // 8)

# Effective context = window − reserved output space
EFFECTIVE_CONTEXT_WINDOW = COMPACT_CONTEXT_WINDOW - MAX_OUTPUT_TOKENS_FOR_SUMMARY

# Headroom before the hard limit where auto-compact fires
AUTOCOMPACT_BUFFER_TOKENS = max(200, EFFECTIVE_CONTEXT_WINDOW // 12)

# Warning / error UI thresholds (for the status-line display)
WARNING_THRESHOLD_BUFFER = max(300, EFFECTIVE_CONTEXT_WINDOW // 8)
ERROR_THRESHOLD_BUFFER = WARNING_THRESHOLD_BUFFER

# Blocking limit: refuse new queries above this (manual compact required)
MANUAL_COMPACT_BUFFER = max(100, EFFECTIVE_CONTEXT_WINDOW // 30)

# The actual token level that triggers auto-compaction
AUTOCOMPACT_THRESHOLD = EFFECTIVE_CONTEXT_WINDOW - AUTOCOMPACT_BUFFER_TOKENS


# ── Circuit breaker ────────────────────────────────────────────────────────────
# After this many consecutive failures stop retrying until the session restarts.
# Mirrors MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3 in autoCompact.ts.

MAX_CONSECUTIVE_FAILURES = 3


# ── Token estimation ───────────────────────────────────────────────────────────

def estimate_tokens(messages: list[dict]) -> int:
    """
    Rough token estimate for a message list: total characters ÷ 4.

    Replace with tiktoken or the Anthropic token-count API for accuracy.
    Mirrors tokenCountWithEstimation() from utils/tokens.ts.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(item.get("content", ""))) for item in content)
        for tc in msg.get("tool_calls", []):
            total += len(str(tc.get("input") or tc.get("function", {}).get("arguments", "")))
    return total // 4


# ── Threshold queries ──────────────────────────────────────────────────────────

def should_auto_compact(messages: list[dict]) -> bool:
    """
    True when the context is above the auto-compact trigger threshold.
    Mirrors shouldAutoCompact() (token check only; recursion guards are in
    engine.py via query_source checks).
    """
    return estimate_tokens(messages) >= AUTOCOMPACT_THRESHOLD


def calculate_warning_state(messages: list[dict]) -> dict:
    """
    Return a status dict for the UI status-line (mirrors calculateTokenWarningState).

    Keys:
      tokens_used          int
      percent_left         int   (0-100)
      is_above_warning     bool
      is_above_error       bool
      is_above_threshold   bool
      is_at_blocking_limit bool
    """
    used = estimate_tokens(messages)
    threshold = AUTOCOMPACT_THRESHOLD
    effective = EFFECTIVE_CONTEXT_WINDOW

    percent_left = max(0, round((threshold - used) / threshold * 100))
    warning_at = effective - WARNING_THRESHOLD_BUFFER
    error_at   = effective - ERROR_THRESHOLD_BUFFER
    block_at   = effective - MANUAL_COMPACT_BUFFER

    return {
        "tokens_used":          used,
        "percent_left":         percent_left,
        "is_above_warning":     used >= warning_at,
        "is_above_error":       used >= error_at,
        "is_above_threshold":   used >= AUTOCOMPACT_THRESHOLD,
        "is_at_blocking_limit": used >= block_at,
    }
