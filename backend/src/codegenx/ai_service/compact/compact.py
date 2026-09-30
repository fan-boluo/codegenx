"""
Full compaction system — session-memory fast path + LLM summarization.

Two compaction paths (tried in order):
──────────────────────────────────────────
Path A — Session-memory fast path
  • Reads existing session MEMORY.md summary
  • Keeps last N messages that fit in MIN_TEXT_MESSAGES + MAX_TOKENS_AFTER
  • No LLM call — immediate, zero cost
  • Used when session memory has already been extracted at least once

Path B — LLM summarization
  • Sends the full conversation to the LLM with BASE_COMPACT_PROMPT
    （经韧性层 resilient_invoke：模型级熔断/同模型重试/链上降级都在其中）
  • Strips <analysis> block, uses <summary> block as context replacement

Failure handling（统一出口）
  • 任何「拿不到有效压缩结果」的场景——Path B 调用失败（韧性层已重试/降级/
    熔断跳过后仍失败）、返回空摘要、压缩无效（压缩后 token 不降反升）——
    都由 compact_if_needed 汇入同一个兜底：保守截断（保底可用，绝不向上抛错）。
    压缩无效属于业务结果而非模型故障，不进熔断。
  • Path A 不经 LLM、不受熔断影响（永远安全且零成本）；其读取失败仅降级
    尝试 Path B，不直接进入兜底。
"""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from codegenx.ai_service.llm.errors import classify_llm_error
from codegenx.ai_service.llm.resilience import get_breaker, resilient_invoke
from codegenx.ai_service.llm.async_client import get_llm
from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.compact.thresholds import estimate_tokens
from codegenx.ai_service.compact.prompt import (
    BASE_COMPACT_PROMPT,
    format_compact_summary,
    get_compact_user_summary_message,
)

from shared import log

# ── Session-memory fast-path constants ────────────────────────────────────────
# Mirrors config in sessionMemoryCompact.ts (getSessionMemoryCompactConfig)

MIN_TEXT_MESSAGES = 5        # always keep at least this many user/assistant turns
MAX_TOKENS_AFTER  = 1_200    # token budget for kept messages (scale up for real LLM)
MIN_TOKENS_AFTER  = 200      # don't truncate below this even if over budget

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

async def _call_llm_for_summary(messages: list[dict], chain: list[str]) -> str:
    """
    Send the full conversation plus the compact prompt to the compaction model
    chain（经韧性层 resilient_invoke：模型级熔断/同模型重试/链上降级都在其中），
    then return the cleaned summary string. 失败向上抛出，由调用方统一兜底。
    """
    compact_messages = list(messages) + [
        {"role": "user", "content": BASE_COMPACT_PROMPT}
    ]
    # 标准化内容，避免 dict/list 等非字符串 content 导致 LLM API 拒绝
    for msg in compact_messages:
        content = msg.get("content")
        if not isinstance(content, str):
            msg["content"] = str(content)

    raw = await resilient_invoke(compact_messages, chain=chain, label="compact")
    return format_compact_summary(raw)


async def _llm_compact(messages: list[dict], chain: list[str]) -> CompactResult | None:
    """
    Path B: ask the LLM to summarise the conversation, then rebuild messages.
    同模型重试/链上降级/熔断跳过已由韧性层（resilient_invoke）负责，此处不再
    自带重试循环；任何失败（含空摘要）只记日志并返回 None，由调用方
    compact_if_needed 统一走保守截断兜底。
    """
    tokens_before = estimate_tokens(messages)
    try:
        summary = await _call_llm_for_summary(messages, chain)
    except Exception as exc:
        log.warning(
            "LLM compact failed ({}): {}",
            classify_llm_error(exc).value, exc,
        )
        return None

    if not summary.strip():
        log.warning("LLM compact returned empty summary.")
        return None

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
    return CompactResult(
        messages=new_messages,
        summary=summary,
        path_used="llm",
        messages_removed=max(0, len(messages) - len(new_messages)),
        tokens_before=tokens_before,
        tokens_after=estimate_tokens(new_messages),
    )


# ── Unified auto-compact entry point ──────────────────────────────────────────


class CompactionService(BaseComponent):
    """压缩服务（服务化，原 CompactionEngine，docs/SystemApp架构设计.md §4.3）。

    - 模型：初始化时从 compact.model_name 解析（缺省回落默认模型），
      经韧性层 resilient_invoke 调用（模型级熔断/重试/降级都在其中）；
    - 失败兜底：调用失败/空摘要/压缩无效统一保守截断——压缩无效是业务结果
      而非模型故障，不进熔断（模型调用成败由韧性层模型级熔断器记录）；
    - 会话摘要（Path A 快速通道）经 summary_loader 读取（app.summary.load(ids)）。
    """

    name = ComponentType.COMPACTION

    def __init__(self, system_app=None) -> None:
        BaseComponent.__init__(self, system_app)
        # 初始化即解析压缩模型（compact.model_name，空则默认模型）
        model = (config.compact.model_name or "").strip() or config.get_default_model()
        self._model_chain: list[str] = [model]

    def init_app(self, system_app) -> None:
        self.system_app = system_app

    async def async_before_start(self) -> None:
        """预热压缩模型的客户端与熔断器（构造即注册，不发起网络请求；失败不阻断启动）。"""
        for model in self._model_chain:
            with suppress(Exception):
                get_llm(model)
                get_breaker(model)
                log.info("[compaction] 压缩模型客户端已预热: {}", model)

    @property
    def model_chain(self) -> list[str]:
        return list(self._model_chain)

    # ------------------------------------------------------------------ public

    async def compact_if_needed(
        self,
        messages: list[dict],
        *,
        summary_loader: Any = None,          # Callable[[], str] | None
    ) -> tuple[list[dict], CompactResult | None]:
        """
        Run the full compaction pipeline if the threshold is exceeded.

        Returns (possibly_compacted_messages, result_or_None).
        result is None when compaction was skipped (not needed, or blocked).
        """
        from codegenx.ai_service.compact.thresholds import should_auto_compact

        if not should_auto_compact(messages):
            return messages, None

        tokens_before = estimate_tokens(messages)
        result = await self._run_compaction(messages, summary_loader=summary_loader)
        if result is None or result.tokens_after >= result.tokens_before:
            # 统一失败出口：调用失败/空摘要（result=None）与压缩无效同等对待，
            # 保守截断兜底（压缩无效是业务结果，不属模型故障、不计熔断）
            if result is not None:
                log.info(
                    "Compaction ineffective ({}→{} tokens, +{:.0f}%); falling back to truncation.",
                    result.tokens_before, result.tokens_after,
                    (result.tokens_after - result.tokens_before) / max(1, result.tokens_before) * 100,
                )
            # Conservative truncation: keep last messages that fit within the effective
            # context window to prevent API errors from overly large context.

            from codegenx.ai_service.compact.thresholds import EFFECTIVE_CONTEXT_WINDOW
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
        self,
        messages: list[dict],
        *,
        summary_loader: Any = None,
    ) -> CompactResult | None:
        # Path A — session memory fast path（不经 LLM；读取失败仅降级尝试 Path B）
        if summary_loader is not None:
            try:
                summary = summary_loader()
                if summary and summary.strip():
                    log.info("Compaction: using session-memory fast-path.")
                    return _session_memory_compact(messages, summary)
            except Exception as exc:
                log.warning("Session-memory fast-path failed: {}; trying LLM.", exc)

        # Path B — LLM summarization（模型级熔断/重试/降级在韧性层内）
        result = await _llm_compact(messages, self._model_chain)
        if result is not None:
            return result

        log.warning("Compaction: all paths failed.")
        return None


def initialize_compaction(system_app) -> CompactionService:
    """注册压缩服务组件（system_app.initialize_components 调用）。"""
    return system_app.register(CompactionService)
