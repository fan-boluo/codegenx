from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monitor.telemetry_schema import SessionTelemetry, TurnTelemetry

if TYPE_CHECKING:
    pass


class MetricCollector:
    """In-memory metric buffer for one agent session, keyed by span_id."""

    def __init__(self) -> None:
        self._session_telemetry: SessionTelemetry | None = None
        self._turn_telemetries: dict[str, TurnTelemetry] = {}

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def set_session_telemetry(self, tele: SessionTelemetry) -> None:
        self._session_telemetry = tele

    def get_session_telemetry(self) -> SessionTelemetry | None:
        return self._session_telemetry

    # ------------------------------------------------------------------
    # Turn
    # ------------------------------------------------------------------

    def add_turn_telemetry(self, span_id: str, tele: TurnTelemetry) -> None:
        self._turn_telemetries[span_id] = tele

    def get_turn_telemetry(self, span_id: str) -> TurnTelemetry | None:
        return self._turn_telemetries.get(span_id)

    def update_turn(self, span_id: str, tele: TurnTelemetry) -> None:
        """Merge runtime-updated fields into the buffered TurnTelemetry."""
        item = self._turn_telemetries.get(span_id)
        if item is None:
            return
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
