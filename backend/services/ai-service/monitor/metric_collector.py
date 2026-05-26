from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monitor.telemetry_schema import SessionTelemetry, TurnTelemetry

if TYPE_CHECKING:
    pass


class MetricCollector:
    """In-memory metric buffer for one agent session."""

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
                item.total_prompt_tokens = tele.total_prompt_tokens
                item.total_completion_tokens = tele.total_completion_tokens
                item.total_tokens = tele.total_tokens
                item.llm_recovery_count = tele.llm_recovery_count
                item.last_recovery_kind = tele.last_recovery_kind
                item.total_tool_calls = tele.total_tool_calls
                item.total_tool_call_errors = tele.total_tool_call_errors
                item.total_memory_hits = tele.total_memory_hits
                item.memory_is_error = tele.memory_is_error
                item.token_count = tele.token_count
                item.token_usage = tele.token_usage
                item.context_is_compress = tele.context_is_compress
                return
