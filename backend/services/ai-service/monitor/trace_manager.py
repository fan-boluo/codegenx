import secrets

from opentelemetry import trace

from monitor.span_context import SpanContext
from shared.schema.ai_service import AiServiceGenerateRequest

# ============================================================
# 2. Trace 管理
# ============================================================

class TraceManager:
    """基于 OTel Tracer 的 Span 管理"""

    def __init__(self):
        self.tracer = trace.get_tracer(__name__)

    @staticmethod
    def _normalize_trace_id(trace_id: int) -> str:
        if trace_id:
            return format(trace_id, '032x')
        return secrets.token_hex(16)

    @staticmethod
    def _normalize_span_id(span_id: int) -> str:
        if span_id:
            return format(span_id, '016x')
        return secrets.token_hex(8)

    def create_root_span(
        self,
        request:AiServiceGenerateRequest
    ) -> "SpanContext":
        """
        创建会话根 Span。
        返回 SpanContext，主循环后续操作通过 parent_span_id 挂载。
        """
        span = self.tracer.start_span(
            name="agent.session",
            attributes={
                "session.id": session_id,
                "trace_id":trace_id,
                "user.id": user_id,
                "span.type": "root"
            }
        )
        return SpanContext(
            trace_id=trace_id,
            span_id=self._normalize_span_id(span.get_span_context().span_id),
            span=span
        )

    def start_child_span(
        self,
        parent_ctx: "SpanContext",
        operation_name: str,
        attributes: dict = None
    ) -> "SpanContext":
        """
        创建子 Span，自动继承父 Span 的 trace_id。
        """
        parent_otel_ctx = trace.set_span_in_context(parent_ctx.span)
        span = self.tracer.start_span(
            name=operation_name,
            context=parent_otel_ctx,
            attributes=attributes or {}
        )
        return SpanContext(
            trace_id=parent_ctx.trace_id,
            span_id=self._normalize_span_id(span.get_span_context().span_id),
            span=span
        )

    def end_span(
        self,
        span_ctx: "SpanContext",
        status: str = "ok",
        attributes: dict = None
    ) -> None:
        """结束 Span 并设置最终属性"""
        span = span_ctx.span
        if attributes:
            span.set_attributes(attributes)
        if status == "error":
            span.set_status(trace.Status(trace.StatusCode.ERROR))
        else:
            span.set_status(trace.Status(trace.StatusCode.OK))
        span.end()

    def set_attribute(
        self,
        span_ctx: "SpanContext",
        key: str,
        value: any
    ) -> None:
        """运行时追加属性"""
        span_ctx.span.set_attribute(key, value)

    def add_event(
        self,
        span_ctx: "SpanContext",
        event_name: str,
        attributes: dict = None
    ) -> None:
        """记录 Span 内的事件点"""
        span_ctx.span.add_event(name=event_name, attributes=attributes or {})

    def record_exception(
        self,
        span_ctx: "SpanContext",
        exception: Exception
    ) -> None:
        """记录异常"""
        span_ctx.span.record_exception(exception)
        span_ctx.span.set_status(trace.Status(trace.StatusCode.ERROR))
