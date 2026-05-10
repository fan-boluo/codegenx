from typing import Any
from opentelemetry.trace import Span

class SpanContext:
    """封装 OTel Span 和 ID，方便传递"""

    def __init__(self, trace_id: str, span_id: str, span: Span) -> None:
        self.trace_id = trace_id
        self.span_id = span_id
        self.span = span