from __future__ import annotations

from threading import Lock

from monitor.monitor_store import MonitorStore, get_monitor_store
from monitor.span_collector import SpanCollector
from monitor.telemetry_schema import (
    MonitorAlertRecord,
    SessionTelemetry,
    SpanRecord,
    TurnTelemetry,
)

_PIPELINE_SINGLETON: "MonitorPipeline | None" = None
_PIPELINE_LOCK = Lock()


class MonitorPipeline:
    """
    Facade that coordinates SpanCollector, SessionTelemetry, and MonitorStore.

    A single process-level singleton is sufficient because session-level state
    (SpanCollector, SessionTelemetry) lives on the RuntimeSessionState object
    that is passed into every hook call.
    """

    def __init__(self) -> None:
        self.store: MonitorStore = get_monitor_store()

    # ------------------------------------------------------------------
    # Per-session lifecycle helpers
    # ------------------------------------------------------------------

    @staticmethod
    def new_span_collector() -> SpanCollector:
        """Create a fresh SpanCollector for a new session."""
        return SpanCollector()

    # ------------------------------------------------------------------
    # Span management
    # ------------------------------------------------------------------

    def add_span(self, collector: SpanCollector, record: SpanRecord) -> None:
        """Buffer a span record into the session's collector."""
        collector.add(record)

    def update_span(
        self,
        collector: SpanCollector,
        span_id: str,
        *,
        end_time,
        duration_ms: int,
        status: str,
        attributes: dict | None = None,
    ) -> None:
        """Update an already-buffered span with completion data."""
        collector.update_end(span_id, end_time=end_time, duration_ms=duration_ms, status=status, attributes=attributes)

    # ------------------------------------------------------------------
    # Turn end: derive + persist turn_metrics, then accumulate into session
    # ------------------------------------------------------------------

    async def on_turn_end(
        self,
        collector: SpanCollector,
        session_telemetry: SessionTelemetry,
        turn_telemetry: TurnTelemetry,
    ) -> None:
        """Persist turn_metrics derived from spans + telemetry, then roll up into session."""
        metrics = collector.derive_turn_metrics(turn_telemetry)
        await self.store.replace_turn_metrics(metrics)
        session_telemetry.record_turn(turn_telemetry)

    # ------------------------------------------------------------------
    # Session end: flush spans + persist session_metrics
    # ------------------------------------------------------------------

    async def on_session_end(
        self,
        collector: SpanCollector,
        session_telemetry: SessionTelemetry,
    ) -> None:
        """Batch-insert all buffered spans, then upsert the session summary."""
        await self.store.insert_spans(collector.get_all())
        collector.clear()
        await self.store.upsert_session_metrics(session_telemetry)

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    async def persist_alert(self, record: MonitorAlertRecord) -> None:
        await self.store.upsert_alert(record)


def get_monitor_pipeline() -> MonitorPipeline:
    global _PIPELINE_SINGLETON
    if _PIPELINE_SINGLETON is not None:
        return _PIPELINE_SINGLETON
    with _PIPELINE_LOCK:
        if _PIPELINE_SINGLETON is None:
            _PIPELINE_SINGLETON = MonitorPipeline()
    return _PIPELINE_SINGLETON
