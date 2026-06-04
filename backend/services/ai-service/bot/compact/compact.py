"""
Full compaction system — session-memory fast path + LLM fallback + circuit breaker.

Mirrors:
  src/services/compact/autoCompact.ts   — autoCompactIfNeeded()
  src/services/compact/compact.ts       — compactConversation()
  src/services/compact/sessionMemoryCompact.ts — trySessionMemoryCompaction()

Three compaction paths (tried in order):
──────────────────────────────────────────
Path A — Session-memory fast path
  • Reads existing session MEMORY.md summary
  • Keeps last N messages that fit in MIN_TEXT_MESSAGES + MAX_TOKENS_AFTER
  • No LLM call — immediate, zero cost
  • Used when session memory has already been extracted at least once

Path B — LLM summarization
  • Sends the full conversation to the LLM with BASE_COMPACT_PROMPT
  • Strips <analysis> block, uses <summary> block as context replacement
  • PTL (Prompt Too Long) retry: truncate oldest 20 % of messages and retry,
    up to MAX_COMPACT_RETRIES times
  • Falls back gracefully if the LLM itself is unavailable

Circuit breaker
  • After MAX_CONSECUTIVE_FAILURES consecutive path-B failures, compaction is
    disabled for the session (mirrors autoCompact.ts circuit-breaker logic)
  • Path A is not gated by the circuit breaker (it's always safe)
"""
from __future__ import annotations

import asyncio
from shared.config.log_config import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from compact.thresholds import (
    MAX_CONSECUTIVE_FAILURES,
    estimate_tokens,
)
from compact.prompt import (
    BASE_COMPACT_PROMPT,
    format_compact_summary,
    get_compact_user_summary_message,
)

from shared.config.log_config import log

# ── Session-memory fast-path constants ────────────────────────────────────────
# Mirrors config in sessionMemoryCompact.ts (getSessionMemoryCompactConfig)

MIN_TEXT_MESSAGES = 5        # always keep at least this many user/assistant turns
MAX_TOKENS_AFTER  = 1_200    # token budget for kept messages (scale up for real LLM)
MIN_TOKENS_AFTER  = 200      # don't truncate below this even if over budget

# ── LLM path constants ────────────────────────────────────────────────────────

MAX_COMPACT_RETRIES  = 3     # PTL retry attempts
PTL_TRUNCATE_RATIO   = 0.20  # remove this fraction of oldest messages per retry


# ── CompactResult type ────────────────────────────────────────────────────────

@dataclass
class CompactResult:
    """Outcome of a single compaction run."""
    messages:    list[dict]
    summary:     str
    path_used:   str   # "session_memory" | "llm" | "none"
    messages_removed: int = 0
    tokens_before:    int = 0
    tokens_after:     int = 0


# ── Circuit breaker state ──────────────────────────────────────────────────────

@dataclass
class _CircuitBreaker:
    consecutive_failures: int = 0
    disabled:             bool = False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.disabled = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.disabled = True
            log.warning(
                "Auto-compact circuit breaker opened after {} consecutive failures.",
                self.consecutive_failures,
            )


# Module-level circuit breaker (one per process / session).
# engine.py creates a CompactionEngine per session which carries its own.
_default_breaker = _CircuitBreaker()


# ── Path A — session-memory fast path ─────────────────────────────────────────

def _has_text_content(msg: dict) -> bool:
    """True if this message has at least one plain-text content item."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(item, str) and item.strip()
            or (isinstance(item, dict) and item.get("type") == "text" and item.get("text", "").strip())
            for item in content
        )
    return False


def _keep_last_n_messages(
    messages: list[dict],
    min_keep: int = MIN_TEXT_MESSAGES,
    max_tokens: int = MAX_TOKENS_AFTER,
) -> list[dict]:
    """
    从尾部保留尽可能多的消息，在不超过 max_tokens 且至少 min_keep 条文本消息的前提下。
    保持与 _session_memory_compact 相同的截断逻辑。
    """
    kept: list[dict] = []
    text_count = 0
    token_acc = 0

    for msg in reversed(messages):
        msg_tokens = estimate_tokens([msg])
        if (
            token_acc + msg_tokens > max_tokens
            and text_count >= min_keep
            and token_acc > MIN_TOKENS_AFTER
        ):
            break
        kept.insert(0, msg)
        token_acc += msg_tokens
        if _has_text_content(msg):
            text_count += 1
    return kept


def _session_memory_compact(
    messages: list[dict], session_summary: str
) -> CompactResult:
    """
    Fast-path compaction using an already-extracted session summary.

    Keeps the most recent messages that fit in MAX_TOKENS_AFTER, ensuring at
    least MIN_TEXT_MESSAGES text-bearing messages are kept.
    Prepends a synthetic (user, assistant) pair that restores context from the
    summary (mirrors buildFastPathMessages() in sessionMemoryCompact.ts).
    """
    tokens_before = estimate_tokens(messages)
    # Build the prior-context pair first so we can subtract its cost from the tail budget
    user_context = {
        "role": "user",
        "content": get_compact_user_summary_message(session_summary),
    }
    assistant_ack = {
        "role": "assistant",
        "content": (
            "I'll continue from where we left off. "
            "I have the context from the previous session."
        ),
    }
    synthetic_tokens = estimate_tokens([user_context, assistant_ack])
    tail_budget = max(MIN_TOKENS_AFTER, MAX_TOKENS_AFTER - synthetic_tokens)
    kept = _keep_last_n_messages(messages, max_tokens=tail_budget)
    # 最多保留 N-2 条消息，避免压缩后消息数反而增加
    max_tail = max(MIN_TEXT_MESSAGES, len(messages) - 2)
    if len(kept) > max_tail:
        kept = kept[-max_tail:]

    new_messages = [user_context, assistant_ack] + kept
    return CompactResult(
        messages=new_messages,
        summary=session_summary,
        path_used="session_memory",
        messages_removed=max(0, len(messages) - len(new_messages)),
        tokens_before=tokens_before,
        tokens_after=estimate_tokens(new_messages),
    )


# ── Path B — LLM summarization ────────────────────────────────────────────────

async def _call_llm_for_summary(
    messages: list[dict], llm_fn: Any
) -> str:
    """
    Call the provided async LLM function with the full conversation plus the
    compact prompt, then return the cleaned summary string.

    llm_fn signature accepted:
      - async generator (messages: list[dict]) -> AsyncIterator[str]
      - async callable (messages: list[dict]) -> str   (e.g. AsyncLLMClient.invoke)
    """
    compact_messages = list(messages) + [
        {"role": "user", "content": BASE_COMPACT_PROMPT}
    ]
    # 标准化内容，避免 dict/list 等非字符串 content 导致 LLM API 拒绝
    for msg in compact_messages:
        content = msg.get("content")
        if not isinstance(content, str):
            msg["content"] = str(content)

    result = llm_fn(compact_messages)

    # 兼容两种签名：async generator 和返回 str 的 async callable
    if hasattr(result, '__aiter__'):
        chunks: list[str] = []
        async for chunk in result:
            chunks.append(chunk)
        raw = "".join(chunks)
    else:
        raw = await result

    return format_compact_summary(raw)


async def _llm_compact(
    messages: list[dict],
    llm_fn: Any,
    breaker: _CircuitBreaker,
) -> CompactResult | None:
    """
    Path B: ask the LLM to summarise the conversation, then rebuild messages.
    Retries up to MAX_COMPACT_RETRIES times, each time trimming the oldest
    PTL_TRUNCATE_RATIO of messages (PTL = Prompt Too Long).
    Returns None on total failure (caller should record_failure on breaker).
    """
    tokens_before = estimate_tokens(messages)
    work_messages = list(messages)

    for attempt in range(1, MAX_COMPACT_RETRIES + 1):
        try:
            summary = await _call_llm_for_summary(work_messages, llm_fn)

            if not summary.strip():
                raise ValueError("LLM returned empty summary")

            user_context = {
                "role": "user",
                "content": get_compact_user_summary_message(summary),
            }
            assistant_ack = {
                "role": "assistant",
                "content": (
                    "Understood. I have the context from the previous session "
                    "and will continue from where we left off."
                ),
            }

            # 保留最后 N 条非系统消息，避免摘要丢失最近的细节上下文
            synthetic_tokens = estimate_tokens([user_context, assistant_ack])
            tail_budget = max(MIN_TOKENS_AFTER, MAX_TOKENS_AFTER - synthetic_tokens)
            # 最多保留 N-2 条消息，避免压缩后消息数反而增加
            max_tail = max(MIN_TEXT_MESSAGES, len(messages) - 2)
            kept_tail = _keep_last_n_messages(messages, min_keep=MIN_TEXT_MESSAGES, max_tokens=tail_budget)
            if len(kept_tail) > max_tail:
                kept_tail = kept_tail[-max_tail:]
            new_messages = [user_context, assistant_ack]
            if kept_tail:
                new_messages.extend(kept_tail)
            breaker.record_success()
            return CompactResult(
                messages=new_messages,
                summary=summary,
                path_used="llm",
                messages_removed=max(0, len(messages) - len(new_messages)),
                tokens_before=tokens_before,
                tokens_after=estimate_tokens(new_messages),
            )

        except Exception as exc:
            log.warning(
                "LLM compact attempt {}/{} failed: {}",
                attempt, MAX_COMPACT_RETRIES, exc,
            )
            if attempt < MAX_COMPACT_RETRIES:
                # PTL retry: drop oldest fraction, ensuring we don't split
                # an assistant message from its follow-up tool messages.
                n_drop = max(1, int(len(work_messages) * PTL_TRUNCATE_RATIO))
                # Find safe boundary: if the cut lands on a tool message,
                # walk forward until we find a non-tool message.
                safe_idx = n_drop
                while safe_idx < len(work_messages) and work_messages[safe_idx].get("role") == "tool":
                    safe_idx += 1
                work_messages = work_messages[safe_idx:]
                log.info("PTL retry: dropped {} oldest messages (safe boundary at {})", safe_idx, safe_idx)

    return None


# ── Unified auto-compact entry point ──────────────────────────────────────────

class CompactionEngine:
    """
    Stateful compaction engine for one session.

    Usage in engine.py::

        self._compaction = CompactionEngine(session_id, llm_fn)

        # before each query:
        messages = self._compaction.microcompact(messages)

        # after token check:
        if self._compaction.should_compact(messages):
            messages = await self._compaction.compact(messages)
    """

    def __init__(
        self,
        session_id: str,
        llm_fn: Any = None,                 # async generator: messages -> token strings
        session_memory: Any = None,  # SessionMemory instance (optional)
    ) -> None:
        self.session_id = session_id
        self._llm_fn = llm_fn
        self._session_memory = session_memory
        self._breaker = _CircuitBreaker()

    # ------------------------------------------------------------------ public

    async def compact_if_needed(
        self, messages: list[dict]
    ) -> tuple[list[dict], CompactResult | None]:
        """
        Run the full compaction pipeline if the threshold is exceeded.

        Returns (possibly_compacted_messages, result_or_None).
        result is None when compaction was skipped (not needed, or blocked).
        """
        from compact.thresholds import should_auto_compact

        if not should_auto_compact(messages):
            return messages, None

        if self._breaker.disabled:
            log.warning("Auto-compact disabled (circuit breaker open); skipping.")
            return messages, None

        tokens_before = estimate_tokens(messages)
        result = await self._run_compaction(messages)
        if result is None:
            self._breaker.record_failure()
            # Conservative truncation: keep last messages that fit within the effective
            # context window to prevent API errors from overly large context.

            from compact.thresholds import EFFECTIVE_CONTEXT_WINDOW
            truncated = _keep_last_n_messages(messages, min_keep=MIN_TEXT_MESSAGES, max_tokens=EFFECTIVE_CONTEXT_WINDOW)
            log.warning(
                "All compaction paths failed; falling back to conservative truncation: {}→{} messages.",
                len(messages), len(truncated),
            )
            return truncated, CompactResult(
                messages=truncated,
                summary="",
                path_used="none",
                messages_removed=len(messages) - len(truncated),
                tokens_before=tokens_before,
                tokens_after=estimate_tokens(truncated),
            )

        log.info(
            "Compaction complete via {}: {}→{} tokens, removed {} messages.",
            result.path_used,
            result.tokens_before,
            result.tokens_after,
            result.messages_removed,
        )
        return result.messages, result

    # ----------------------------------------------------------------- private

    async def _run_compaction(
        self, messages: list[dict]
    ) -> CompactResult | None:
        # Path A — session memory fast path
        if self._session_memory is not None:
            try:
                summary = self._session_memory.load()
                if summary and summary.strip():
                    log.info("Using session-memory fast-path compaction.")
                    return _session_memory_compact(messages, summary)
            except Exception as exc:
                log.warning("Session-memory fast-path failed: {}; trying LLM.", exc)

        # Path B — LLM summarization
        if self._llm_fn is not None:
            log.debug("Using LLM compaction.")
            result = await _llm_compact(messages, self._llm_fn, self._breaker)
            if result is not None:
                return result

        log.error("All compaction paths failed.")
        return None


# ── Standalone convenience helpers (mirrors memory/compact.py public API) ─────

async def compact_conversation(
    messages: list[dict],
    llm_fn: Any,
    session_summary: str = "",
) -> CompactResult:
    """
    Compact a message list.  Tries the session-memory fast-path when
    session_summary is provided, otherwise runs the LLM path.

    Suitable for one-off calls without a full CompactionEngine.
    """
    if session_summary.strip():
        return _session_memory_compact(messages, session_summary)

    breaker = _CircuitBreaker()
    result = await _llm_compact(messages, llm_fn, breaker)
    if result is not None:
        return result

    # Total failure: return original messages unchanged
    return CompactResult(
        messages=messages,
        summary="",
        path_used="none",
        tokens_before=estimate_tokens(messages),
        tokens_after=estimate_tokens(messages),
    )
