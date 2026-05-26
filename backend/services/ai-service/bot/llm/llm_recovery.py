"""LLM error-recovery mixin (s11).

Three recovery paths wired into the main invoke loop:
  1. finish_reason length/max_tokens → inject CONTINUATION_MESSAGE and retry.
  2. Context-too-long API error      → compact history and retry.
  3. Transient transport error        → exponential backoff and retry.
"""
from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any
from agent.agent_schema import AgentEvent, AgentState, AgentEventType
from agent.runtime_schema import  RuntimeSessionState, TurnStoppedError, \
    ActivateTurn
from shared.config.log_config import log

if TYPE_CHECKING:
    pass


class LLMRecoveryMixin:
    """Mixin for AgentRuntime — provides ``_invoke_llm_with_recovery``."""

    # These attributes are satisfied by AgentRuntime; declared here for type checkers.
    CONTINUATION_MESSAGE: str
    agent_config: Any
    context_assembler: Any
    context_compactor: Any

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
        from bot.llm.async_client import AsyncLLMClient

        cfg = self.agent_config
        context = session_state.context_manager
        tools = session_state.runtime.tools
        continuation_attempts = 0
        compact_attempts = 0
        transport_attempts = 0
        accumulated_content = ""

        while True:
            try:
                llm_client = AsyncLLMClient()
                round_response: dict[str, Any] = {
                    "content": "",
                    "tool_calls": [],
                    "finish_reason": None,
                }

                async for chunk in llm_client.invoke_stream(messages,tools):
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
                        context.add_assistant_message(accumulated_content)
                        context.add_user_message(self.CONTINUATION_MESSAGE)
                        messages = await self.context_assembler.assemble(context)
                        continue
                    log.error(
                        "[Recovery] Continuation exhausted ({} attempts), "
                        "returning partial response",
                        continuation_attempts,
                    )

                transport_attempts = 0  # reset on clean success
                round_response["content"] = accumulated_content + round_response["content"]
                return round_response

            except (TurnStoppedError, asyncio.CancelledError):
                raise

            except Exception as exc:
                err_text = str(exc).lower()

                # Strategy 2: context too long — compact and retry
                if self._is_context_too_long_error(err_text):
                    if compact_attempts < cfg.max_compact_attempts:
                        compact_attempts += 1
                        self._record_recovery(
                            turn_state,
                            "compact",
                            continuation_attempts + compact_attempts + transport_attempts,
                        )
                        log.warning(
                            "[Recovery] Context too long, compacting history "
                            "(attempt {}/{})",
                            compact_attempts,
                            cfg.max_compact_attempts,
                        )
                        await self.context_compactor.compact_history(
                            context, reason="recovery-context-too-long"
                        )
                        messages = await self.context_assembler.assemble(context)
                        continue
                    raise

                # Strategy 3: transient transport error — exponential backoff and retry
                if self._is_transport_error(err_text):
                    if transport_attempts < cfg.max_transport_attempts:
                        delay = _recovery_backoff_delay(
                            transport_attempts,
                            cfg.transport_backoff_base_seconds,
                            cfg.transport_backoff_max_seconds,
                        )
                        transport_attempts += 1
                        self._record_recovery(
                            turn_state,
                            "backoff",
                            continuation_attempts + compact_attempts + transport_attempts,
                        )
                        log.warning(
                            "[Recovery] Transport error: {}. Backing off {:.1f}s "
                            "(attempt {}/{})",
                            exc,
                            delay,
                            transport_attempts,
                            cfg.max_transport_attempts,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise

                raise

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _is_context_too_long_error(err_text: str) -> bool:
        keywords = {
            "context_length_exceeded",
            "overlong_prompt",
            "too long",
            "maximum context",
        }
        return any(kw in err_text for kw in keywords) or (
            "prompt" in err_text and "long" in err_text
        )

    @staticmethod
    def _is_transport_error(err_text: str) -> bool:
        keywords = {
            "timeout",
            "rate limit",
            "rate_limit",
            "429",
            "503",
            "502",
            "504",
            "connection",
            "unavailable",
        }
        return any(kw in err_text for kw in keywords)

    @staticmethod
    def _record_recovery(
        turn_state: ActivateTurn, kind: str, total_count: int
    ) -> None:
        if turn_state.telemetry is not None:
            turn_state.telemetry.llm_recovery_count = total_count
            turn_state.telemetry.last_recovery_kind = kind


def _recovery_backoff_delay(attempt: int, base: float, max_delay: float) -> float:
    return min(base * (2**attempt), max_delay) + random.uniform(0, 1)
