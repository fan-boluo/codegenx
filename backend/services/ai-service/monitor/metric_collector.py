from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monitor.telemetry_schema import SessionTelemetry, TurnTelemetry

if TYPE_CHECKING:
    pass


class MetricCollector:
    """In-memory 指标 buffer for one agent session."""

    def __init__(self) -> None:
        self._session_buffer: list[SessionTelemetry] = []
        self._turn_buffer: list[TurnTelemetry] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_session(self, tele: SessionTelemetry) -> None:
        self._session_buffer.append(tele)

    def add_turn(self, tele: TurnTelemetry) -> None:
        self._turn_buffer.append(tele)

    def update_turn(self, tele: TurnTelemetry) -> None:
        for item in reversed(self._turn_buffer):
            if item.turn_id == tele.turn_id:
                item.ended_at = tele.ended_at
                item.duration_ms = tele.duration_ms
                item.status = tele.status
                item.llm_prompt_tokens = tele.llm_prompt_tokens
                item.llm_completion_tokens = tele.llm_completion_tokens
                item.llm_total_ms = tele.llm_total_ms
                item.llm_first_token_ms = tele.llm_first_token_ms
                item.llm_recovery_count = tele.llm_recovery_count
                item.llm_recovery_kind = tele.llm_recovery_kind
                item.llm_is_error = tele.llm_is_error
                item.tool_calls = tele.tool_calls
                item.memory_hits = tele.memory_hits
                item.memory_latency_ms = tele.memory_latency_ms
                item.memory_is_error = tele.memory_is_error
                item.context_token_count = tele.context_token_count
                item.context_token_usage = tele.context_token_usage
                item.context_is_compress = tele.context_is_compress
                return
