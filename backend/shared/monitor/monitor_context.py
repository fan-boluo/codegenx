from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class MonitorContext:
    user_id: str
    app_id: str
    trace_id: str | None = None
    request_id: str | None = None
    upstream_instance: str | None = None
    first_chunk_latency_ms: int | None = None
    total_latency_ms: int | None = None
    route_latency_ms: int | None = None
    chunk_count: int = 0


    def set_stream_metrics(
        self,
        *,
        upstream_instance: str | None = None,
        first_chunk_latency_ms: int | None = None,
        total_latency_ms: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        if upstream_instance:
            self.upstream_instance = upstream_instance
        if first_chunk_latency_ms is not None:
            self.first_chunk_latency_ms = first_chunk_latency_ms
        if total_latency_ms is not None:
            self.total_latency_ms = total_latency_ms
        if chunk_count is not None:
            self.chunk_count = chunk_count


_context_holder: ContextVar[MonitorContext | None] = ContextVar("monitor_context", default=None)


class MonitorContextHolder:
    @staticmethod
    def set_context(context: MonitorContext) -> None:
        _context_holder.set(context)

    @staticmethod
    def get_context() -> MonitorContext | None:
        return _context_holder.get()

    @staticmethod
    def clear_context() -> None:
        _context_holder.set(None)