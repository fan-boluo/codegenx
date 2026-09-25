"""LLM error-recovery mixin (s11)。

业务语义恢复（本 Mixin 职责）：
  1. finish_reason length/max_tokens → inject CONTINUATION_MESSAGE and retry.
  2. Context-too-long API error      → compact history and retry.
传输类瞬态错误的重试/降级/熔断已移交韧性层（llm/resilience.py，P1）：
首 chunk 前由 executor 透明重试或切换 fallback 模型；首 chunk 后失败直接抛出，
本层不再重试（否则前端会出现重复内容，P1-5）。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from codegenx.ai_service.agent.agent_schema import AgentEvent, AgentState, AgentEventType
from codegenx.ai_service.agent.runtime_schema import  RuntimeSessionState, TurnStoppedError, \
    ActivateTurn
from codegenx.ai_service.llm.errors import LLMErrorClass, classify_llm_error
from codegenx.ai_service.llm.resilience import SCENARIO_AGENT, resilient_invoke_stream
from shared import log

if TYPE_CHECKING:
    pass


class LLMRecoveryMixin:
    """Mixin for AgentRuntime — provides ``_invoke_llm_with_recovery``."""

    # These attributes are satisfied by AgentRuntime; declared here for type checkers.
    CONTINUATION_MESSAGE: str
    agent_config: Any

    def _raise_if_stop_requested(self, session_state: RuntimeSessionState) -> None: ...  # provided by AgentRuntime
    async def _publish_runtime_event(self, session_state: RuntimeSessionState, event: AgentEvent) -> None: ...  # provided by AgentRuntime

    # ------------------------------------------------------------------ public entry

    async def _invoke_llm_with_recovery(
        self,
        messages: list[dict[str, Any]],
        turn_state: ActivateTurn,
        session_state: RuntimeSessionState,
    ) -> dict[str, Any]:
        """Invoke the LLM with error recovery (s11)."""
        cfg = self.agent_config
        context = session_state.context_manager
        # P2：RuntimeSessionState 不再持有 runtime；本 Mixin 混入 AgentRuntime，
        # 工具目录直接取 self.tools（start() 时构建）
        tools = self.tools
        # P0-2 修复：使用 agent 配置的模型（原实现漏传 → 永远回落默认模型，AgentConfig.model 成死配置）
        agent_model = (cfg.resolved_model_name or "").strip() or None
        # P4 §10.4：智能体维度模型覆盖（spec.model_override 最高 → {agent}:{scenario} → scenario → 默认）
        agent_name = (getattr(session_state, "agent_name", "") or "").strip() or None
        agent_override = None
        if agent_name:
            from codegenx.ai_service.system_app import get_app

            registry = get_app().agents
            if registry is not None:
                agent_override = registry.get(agent_name).model_override
        continuation_attempts = 0
        compact_attempts = 0
        accumulated_content = ""
        # 本地追踪 continuation 注入的消息，避免污染 context.chat_messages，因为是属于错误重试的消息
        continuation_messages: list[dict[str, Any]] = []

        while True:
            try:
                self._raise_if_stop_requested(session_state)
                round_response: dict[str, Any] = {
                    "content": "",
                    "tool_calls": [],
                    "finish_reason": None,
                }

                # P1：重试/降级/熔断由韧性层负责（含 agent 配置模型 → fallback 链）
                async for chunk in resilient_invoke_stream(
                    SCENARIO_AGENT,
                    messages,
                    tools=tools,
                    primary_model=agent_model,
                    timeout=cfg.llm_stream_timeout_seconds,
                    agent=agent_name,
                    agent_override=agent_override,
                ):
                    self._raise_if_stop_requested(session_state)
                    if chunk["type"] == "content":
                        round_response["content"] += chunk["data"]
                        await self._publish_runtime_event(
                            session_state,
                            AgentEvent(
                                event_type=AgentEventType.LLM_RESPONSE_CHUNK,
                                data=chunk["data"],
                                state=AgentState.RUNNING,
                            ),
                        )
                    elif chunk["type"] == "tool_calls":
                        round_response["tool_calls"] = chunk["data"]
                    elif chunk["type"] == "response_info":
                        round_response["finish_reason"] = (
                            chunk.get("data") or {}
                        ).get("finish_reason")

                # Strategy 1: output truncated — inject continuation message and retry
                finish_reason = str(round_response.get("finish_reason") or "").lower()
                if finish_reason in {"length", "max_tokens"}:
                    if continuation_attempts < cfg.max_continuation_attempts:
                        continuation_attempts += 1
                        accumulated_content += round_response["content"]
                        self._record_recovery(
                            turn_state,
                            "continue",
                            continuation_attempts + compact_attempts + transport_attempts,
                        )
                        log.warning(
                            "[Recovery] Output truncated, injecting continuation "
                            "(attempt {}/{})",
                            continuation_attempts,
                            cfg.max_continuation_attempts,
                        )
                        # 不在 context.chat_messages 中写入中间消息，在本地构建
                        continuation_messages.append({"role": "assistant", "content": round_response["content"]})
                        continuation_messages.append({"role": "user", "content": self.CONTINUATION_MESSAGE})
                        # 重新组装 messages：原始 + 本地累积的 continuation 消息
                        messages = await context.assemble()
                        messages.extend(continuation_messages)
                        continue
                    log.error(
                        "[Recovery] Continuation exhausted ({} attempts), "
                        "returning partial response",
                        continuation_attempts,
                    )

                round_response["content"] = accumulated_content + round_response["content"]
                return round_response

            except (TurnStoppedError, asyncio.CancelledError):
                raise

            except Exception as exc:
                # 按 SDK 类型化异常分类；瞬态错误的重试已在韧性层完成（含首 chunk 前窗口），
                # 这里只保留业务语义恢复：上下文超长 → 压缩历史后重试
                err_class = classify_llm_error(exc)

                # Strategy 2: context too long — compact and retry
                if err_class is LLMErrorClass.CONTEXT_OVERFLOW:
                    if compact_attempts < cfg.max_compact_attempts:
                        compact_attempts += 1
                        self._record_recovery(
                            turn_state,
                            "compact",
                            continuation_attempts + compact_attempts,
                        )
                        log.warning(
                            "[Recovery] Context too long, compacting history "
                            "(attempt {}/{})",
                            compact_attempts,
                            cfg.max_compact_attempts,
                        )
                        await context.force_compact()
                        messages = await context.assemble()
                        # 重新注入 continuation 消息（如有）
                        if continuation_messages:
                            messages.extend(continuation_messages)
                        continue
                    raise

                raise

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _record_recovery(
        turn_state: ActivateTurn, kind: str, total_count: int
    ) -> None:
        turn_state.llm_recovery_count = total_count
        turn_state.last_recovery_kind = kind
