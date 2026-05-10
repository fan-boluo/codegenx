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

from monitor.telemetry_schema import OperationName, SpanRecord, TurnTelemetry

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
        return [s for s in self._buffer if s.turn_id == turn_id]

    def get_all(self) -> list[SpanRecord]:
        return list(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    # ------------------------------------------------------------------
    # Derive turn_metrics row from spans + live TurnTelemetry
    # ------------------------------------------------------------------

    def derive_turn_metrics(self, turn_telemetry: TurnTelemetry) -> dict[str, Any]:
        """
        Build the turn_metrics INSERT payload.

        Counts that are simpler to derive from spans (error_count,
        tool_calls_detail) come from spans; token / latency data comes
        from the already-maintained TurnTelemetry sub-models.
        """
        spans = self.get_turn_spans(turn_telemetry.turn_id)
        tool_spans = [s for s in spans if s.operation_name.startswith(OperationName.TOOL.value)]
        error_count = sum(1 for s in spans if s.status == "error")
        tool_calls_detail = [
            {
                "name": s.attributes.get("tool.name", ""),
                "latency_ms": s.duration_ms or 0,
                "status": s.status,
            }
            for s in tool_spans
        ]
        return {
            "trace_id": turn_telemetry.trace_id,
            "session_id": turn_telemetry.session_id,
            "request_id": turn_telemetry.request_id,
            "turn_id": turn_telemetry.turn_id,
            "turn_number": turn_telemetry.turn_number,
            "status": turn_telemetry.status.value,
            "prompt_tokens": turn_telemetry.llm.prompt_tokens,
            "completion_tokens": turn_telemetry.llm.completion_tokens,
            "llm_latency_ms": turn_telemetry.llm.total_ms,
            "first_token_ms": turn_telemetry.llm.first_token_ms,
            "llm_recovery_count": turn_telemetry.llm.recovery_count,
            "llm_recovery_kind": turn_telemetry.llm.recovery_kind,
            "tool_calls_count": len(turn_telemetry.tool),
            "tool_calls_detail": tool_calls_detail,
            "memory_hits": turn_telemetry.memory.hits,
            "memory_retrieval_ms": turn_telemetry.memory.latency_ms,
            "context_tokens": turn_telemetry.context.token_count,
            "context_token_usage": int(turn_telemetry.context.token_usage),
            "error_count": error_count,
            "started_at": turn_telemetry.started_at,
            "ended_at": turn_telemetry.ended_at,
            "duration_ms": turn_telemetry.duration_ms,
        }


