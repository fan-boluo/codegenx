"""
SpanCollector
=============
Per-session in-memory buffer for SpanRecord objects.

Usage:
  - handlers call ``collector.add(span_record)`` as operations start / end.
  - At turn end, ``collector.derive_turn_metrics(turn_id, turn_telemetry)`` builds
    the turn_metrics row dict from buffered span attributes + live telemetry.
  - At session end, ``collector.get_all()`` returns the full span list which
    MonitorStore flushes to the ``spans`` table in one batch.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monitor.telemetry_schema import OperationType, SpanRecord, TurnTelemetry

if TYPE_CHECKING:
    pass


class SpanCollector:
    """In-memory span buffer for one agent session."""

    def __init__(self) -> None:
        self._buffer: list[SpanRecord] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, record: SpanRecord) -> None:
        self._buffer.append(record)

    def update_end(
        self,
        span_id: str,
        *,
        end_time: Any,
        duration_ms: int,
        status: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Mutate an existing buffered span with completion data."""
        for span in reversed(self._buffer):
            if span.span_id == span_id:
                span.end_time = end_time
                span.duration_ms = duration_ms
                span.status = status
                if attributes:
                    span.attributes.update(attributes)
                return

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_turn_spans(self, turn_id: str) -> list[SpanRecord]:
        return [s for s in self._buffer if s.request_id == turn_id]

    def get_all(self) -> list[SpanRecord]:
        return list(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    # ------------------------------------------------------------------
    # Derive turn_metrics row from spans + live TurnTelemetry
    # ------------------------------------------------------------------

    def derive_turn_metrics(self, turn_telemetry: TurnTelemetry) -> dict[str, Any]:
        """Build the turn_metrics INSERT payload from TurnTelemetry fields."""
        return {
            "turn_id": turn_telemetry.turn_id,
            "session_id": turn_telemetry.session_id,
            "trace_id": turn_telemetry.trace_id,
            "request_id": turn_telemetry.request_id,
            "app_id": turn_telemetry.app_id,
            "user_id": turn_telemetry.user_id,
            "model": turn_telemetry.model,
            "span_id": turn_telemetry.span_id,
            "turn_number": turn_telemetry.turn_number,
            "status": turn_telemetry.status.value if hasattr(turn_telemetry.status, "value") else str(turn_telemetry.status),
            "end_reason": turn_telemetry.end_reason,
            "token_count": turn_telemetry.token_count,
            "token_usage": turn_telemetry.token_usage,
            "is_compress": turn_telemetry.context_is_compress,
            "total_prompt_tokens": turn_telemetry.total_prompt_tokens,
            "total_completion_tokens": turn_telemetry.total_completion_tokens,
            "total_tokens": turn_telemetry.total_tokens,
            "llm_recovery_count": turn_telemetry.llm_recovery_count,
            "last_recovery_kind": turn_telemetry.last_recovery_kind,
            "total_tool_calls": turn_telemetry.total_tool_calls,
            "total_tool_call_errors": turn_telemetry.total_tool_call_errors,
            "total_memory_hits": turn_telemetry.total_memory_hits,
            "memory_is_error": turn_telemetry.memory_is_error,
            "started_at": turn_telemetry.started_at,
            "ended_at": turn_telemetry.ended_at,
            "duration_ms": turn_telemetry.duration_ms,
        }


