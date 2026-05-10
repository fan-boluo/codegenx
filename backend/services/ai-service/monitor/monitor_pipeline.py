from __future__ import annotations

from threading import Lock
from typing import Any

from monitor.span_collector import SpanCollector
from monitor.metrics_collector import MetricsCollector
from monitor.monitor_store import get_monitor_store
from monitor.span_context import SpanContext
from monitor.telemetry_schema import TurnTelemetry
from monitor.trace_manager import TraceManager
from shared.schema.ai_service import AiServiceGenerateRequest

_PIPELINE_SINGLETON: "MonitorPipeline | None" = None
_PIPELINE_LOCK = Lock()


class MonitorPipeline:
    """监控管线门面，保持原有接口不变，内部委托给 OTel SDK"""

    def __init__(self):
        self.tracer = TraceManager()
        self.metrics = MetricsCollector()
        self.store = get_monitor_store()
        self.span_collector = SpanCollector()
        self._active_root_spans: dict[str, SpanContext] = {}
        self._active_turn_spans: dict[tuple[str, str], SpanContext] = {}

    def _get_parent_span(self, session_id: str, turn_id: str | None = None) -> SpanContext | None:
        if turn_id:
            turn_span = self._active_turn_spans.get((session_id, turn_id))
            if turn_span is not None:
                return turn_span
        return self._active_root_spans.get(session_id)

    # --- SessionStart ---
    def on_session_start(
        self,
        request:AiServiceGenerateRequest
    ) -> SpanContext:
        root = self.tracer.create_root_span(request)
        self.metrics.record_quota_usage(session_id, tokens_remaining)
        self._active_root_spans[session_id] = root

        return root

    def on_turn_start(
        self,
        session_id: str,
        turn_id: str,
        turn_number: int,
    ) -> SpanContext | None:
        parent = self._active_root_spans.get(session_id)
        if parent is None:
            return None

        span = self.tracer.start_child_span(
            parent,
            "agent.turn",
            {
                "session.id": session_id,
                "turn.id": turn_id,
                "turn.number": turn_number,
            },
        )
        self._active_turn_spans[(session_id, turn_id)] = span
        return span

    def on_turn_end(
        self,
        session_id: str,
        turn_id: str,
        status: str,
        duration_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        tool_call_count: int,
        memory_hits: int,
    ) -> None:
        span = self._active_turn_spans.pop((session_id, turn_id), None)
        if span is None:
            return

        self.tracer.end_span(
            span,
            status,
            {
                "turn.id": turn_id,
                "turn.duration_ms": duration_ms,
                "turn.prompt_tokens": prompt_tokens,
                "turn.completion_tokens": completion_tokens,
                "turn.tool_call_count": tool_call_count,
                "turn.memory_hits": memory_hits,
            },
        )

    # --- 每轮 LLM 调用 ---
    def on_llm_call_start(
        self,
        session_id: str,
        turn: int,
        turn_id: str | None = None,
    ) -> SpanContext | None:
        root = self._get_parent_span(session_id, turn_id)
        if root is None:
            return None
        return self.tracer.start_child_span(
            root,
            "llm.call",
            {"session.id": session_id, "turn": turn, "turn.id": turn_id or ""}
        )

    def on_llm_first_token(self, span_ctx: SpanContext | None, first_token_ms: int) -> None:
        if span_ctx is None:
            return
        self.tracer.add_event(span_ctx, "llm.first_token", {"llm.first_token_ms": first_token_ms})

    def on_llm_call_end(
        self,
        span_ctx: SpanContext | None,
        session_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        first_token_ms: int,
        total_ms: int,
        status: str
    ) -> None:
        if span_ctx is not None:
            self.tracer.end_span(span_ctx, status, {
                "llm.prompt_tokens": prompt_tokens,
                "llm.completion_tokens": completion_tokens,
                "llm.first_token_ms": first_token_ms,
                "llm.total_ms": total_ms,
            })
        self.metrics.record_turn(session_id)
        self.metrics.record_token_usage(session_id, prompt_tokens, completion_tokens)
        self.metrics.record_llm_latency(session_id, model, first_token_ms, total_ms)

    # --- 每次工具调用 ---
    def on_tool_call_start(
        self,
        session_id: str,
        tool_name: str,
        turn_id: str | None = None,
    ) -> SpanContext | None:
        root = self._get_parent_span(session_id, turn_id)
        if root is None:
            return None
        return self.tracer.start_child_span(
            root,
            f"tool.{tool_name}",
            {"session.id": session_id, "turn.id": turn_id or "", "tool.name": tool_name}
        )

    def on_tool_call_end(
        self,
        span_ctx: SpanContext | None,
        session_id: str,
        tool_name: str,
        latency_ms: int,
        status: str
    ) -> None:
        if span_ctx is not None:
            self.tracer.end_span(span_ctx, status, {
                "tool.name": tool_name,
                "tool.latency_ms": latency_ms,
            })
        self.metrics.record_tool_call(session_id, tool_name, latency_ms, status)

    # --- 记忆检索 ---
    def on_memory_retrieval(
        self,
        session_id: str,
        hits: int,
        latency_ms: int,
        turn_id: str | None = None,
        source: str = "search",
    ) -> None:
        self.metrics.record_memory_retrieval(session_id, hits, latency_ms)
        parent = self._get_parent_span(session_id, turn_id)
        if parent is not None:
            self.tracer.add_event(
                parent,
                "memory.retrieval",
                {
                    "memory.hits": hits,
                    "memory.latency_ms": latency_ms,
                    "memory.source": source,
                },
            )

    # --- 上下文 ---
    def on_context_assembly(
        self,
        session_id: str,
        token_count: int,
        turn_id: str | None = None,
    ) -> None:
        self.metrics.record_context_size(session_id, token_count)
        parent = self._get_parent_span(session_id, turn_id)
        if parent is not None:
            self.tracer.set_attribute(parent, "context.token_count", token_count)

    # --- 配额 ---
    def on_quota_update(
        self,
        session_id: str,
        tokens_remaining: int
    ) -> None:
        self.metrics.record_quota_usage(session_id, tokens_remaining)

    # --- 错误 ---
    def on_error(
        self,
        session_id: str,
        span_ctx: SpanContext | None,
        exception: Exception,
        error_type: str
    ) -> None:
        effective_span = span_ctx or self._active_root_spans.get(session_id)
        if effective_span is not None:
            self.tracer.record_exception(effective_span, exception)
        self.metrics.record_error(session_id, error_type)

    # --- SessionEnd ---
    def on_session_end(
        self,
        session_id: str,
        root_span: SpanContext | None,
        end_reason: str,
        total_turns: int,
        total_tokens: int,
        status: str = "ok",
    ) -> None:
        effective_root = root_span or self._active_root_spans.get(session_id)
        if effective_root is not None:
            self.tracer.end_span(effective_root, status, {
                "session.end_reason": end_reason,
                "session.total_turns": total_turns,
                "session.total_tokens": total_tokens,
            })
        self.metrics.record_session_end(session_id, end_reason, total_turns, total_tokens)
        stale_turn_keys = [key for key in self._active_turn_spans if key[0] == session_id]
        for key in stale_turn_keys:
            self._active_turn_spans.pop(key, None)
        self._active_root_spans.pop(session_id, None)

    async def persist_span(self, **kwargs: Any) -> bool:
        return await self.store.replace_span(**kwargs)

    async def persist_session_metrics(self, session_telemetry: SessionTelemetry) -> bool:
        return await self.store.upsert_session_metrics(session_telemetry)

    async def persist_request_metrics(self, request_telemetry: RequestTelemetry) -> bool:
        return await self.store.upsert_request_metrics(request_telemetry)

    async def persist_turn_metrics(self, turn_telemetry: TurnTelemetry) -> bool:
        return await self.store.replace_turn_metrics(turn_telemetry)


def get_monitor_pipeline() -> MonitorPipeline:
    global _PIPELINE_SINGLETON
    if _PIPELINE_SINGLETON is not None:
        return _PIPELINE_SINGLETON

    with _PIPELINE_LOCK:
        if _PIPELINE_SINGLETON is None:
            _PIPELINE_SINGLETON = MonitorPipeline()
    return _PIPELINE_SINGLETON

