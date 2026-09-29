"""LLM 业务语义恢复协作器（组合于 AgentRuntime，非继承）。

本类只保留 LLM 调用的**业务语义**恢复：
  1. finish_reason length/max_tokens → inject CONTINUATION_MESSAGE and retry.
  2. Context-too-long API error      → compact history and retry.
传输类瞬态错误的重试/降级/熔断已移交韧性层（llm/resilience.py）：
首 chunk 前由 executor 透明重试或切换 fallback 模型；首 chunk 后失败直接抛出，
本层不再重试（否则前端会出现重复内容）。

模型链来自 runtime.resolve_agent_chain（组件自持：agents[].model，缺省回落默认链）。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from codegenx.ai_service.agent.agent_schema import AgentEvent, AgentState, AgentEventType
from codegenx.ai_service.agent.runtime_schema import TurnStoppedError, ActivateTurn
from codegenx.ai_service.llm.errors import LLMErrorClass, classify_llm_error
from codegenx.ai_service.llm.resilience import resilient_invoke_stream
from shared import log

if TYPE_CHECKING:
    from codegenx.ai_service.agent.runtime import AgentRuntime
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState


class LLMRecovery:
    """AgentRuntime 的 LLM 调用协作器（组合）。

    持有 runtime 引用（鸭子类型访问 tools/agent_config/事件发布/停止检查），
    AgentRuntime.__init__ 中构造：self._llm_recovery = LLMRecovery(self)。
    """

    def __init__(self, runtime: "AgentRuntime") -> None:
        self._runtime = runtime

    # ------------------------------------------------------------------ public entry

    async def invoke(
        self,
        messages: list[dict[str, Any]],
        turn_state: ActivateTurn,
        session_state: "RuntimeSessionState",
    ) -> dict[str, Any]:
        """Invoke the LLM with error recovery (s11)."""
        runtime = self._runtime
        cfg = runtime.agent_config
        context = session_state.context_manager
        tools = runtime.tools
        # 模型链：会话智能体的 model 优先（组件配置 agents[].model），缺省回落默认链
        chain = runtime.resolve_agent_chain(session_state.agent_name)
        # P4 §10.3：spec.limits.temperature 覆盖
        agent_temperature = None
        agent_name = (getattr(session_state, "agent_name", "") or "").strip()
        if agent_name:
            from codegenx.ai_service.system_app import get_app

            registry = get_app().agents
            if registry is not None:
                spec = registry.get(agent_name)
                if spec.limits is not None:
                    agent_temperature = spec.limits.temperature
        continuation_attempts = 0
        compact_attempts = 0
        accumulated_content = ""
        # 本地追踪 continuation 注入的消息，避免污染 context.chat_messages，因为是属于错误重试的消息
        continuation_messages: list[dict[str, Any]] = []

        while True:
            try:
                runtime._raise_if_stop_requested(session_state)
                round_response: dict[str, Any] = {
                    "content": "",
                    "tool_calls": [],
                    "finish_reason": None,
                }

                # 重试/降级/熔断由韧性层负责（组件解析的模型链）
                async for chunk in resilient_invoke_stream(
                    messages,
                    chain=chain,
                    label="agent",
                    tools=tools,
                    timeout=cfg.llm_stream_timeout_seconds,
                    temperature=agent_temperature,
                ):
                    runtime._raise_if_stop_requested(session_state)
                    if chunk["type"] == "content":
                        round_response["content"] += chunk["data"]
                        await runtime._publish_runtime_event(
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
                        info = chunk.get("data") or {}
                        round_response["finish_reason"] = info.get("finish_reason")
                        # BUG-5 接线：真实 usage / 实际服务模型随末尾 response_info 回传
                        round_response["usage"] = info.get("usage")
                        round_response["model"] = info.get("model")

                # Strategy 1: output truncated — inject continuation message and retry
                finish_reason = str(round_response.get("finish_reason") or "").lower()
                if finish_reason in {"length", "max_tokens"}:
                    if continuation_attempts < cfg.max_continuation_attempts:
                        continuation_attempts += 1
                        accumulated_content += round_response["content"]
                        self._record_recovery(
                            turn_state,
                            "continue",
                            continuation_attempts + compact_attempts,
                        )
                        log.warning(
                            "[Recovery] Output truncated, injecting continuation "
                            "(attempt {}/{})",
                            continuation_attempts,
                            cfg.max_continuation_attempts,
                        )
                        # 不在 context.chat_messages 中写入中间消息，在本地构建
                        continuation_messages.append({"role": "assistant", "content": round_response["content"]})
                        continuation_messages.append({"role": "user", "content": runtime.CONTINUATION_MESSAGE})
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
