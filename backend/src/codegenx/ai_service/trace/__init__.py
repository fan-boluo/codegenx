"""Trace 门面：模块名推荐使用 `from codegenx.ai_service import trace`。

主动 span API（api）：压缩/agent 边界直埋；LLM 韧性层 / tool_executor 直埋 llm/tool span。
生命周期直调（recorder）：turn_start/turn_end/turn_error/session_start/session_end——
流程点直调，不经 hook 通道。
上下文（context）：turn 绑定 + parent 栈 + 当前 agent 名（contextvars）。
指标（metrics）：Prometheus 喂入与 telemetry 同源（LLM 调用/调用指标由韧性层喂）。
存储（store）：批量入库（traces/trace_spans）。
"""
from codegenx.ai_service.trace import api, context, metrics
from codegenx.ai_service.trace.api import (
    SpanHandle,
    add_turn_tags,
    current_app_id,
    report_context_usage,
    start_span,
)
from codegenx.ai_service.trace.context import (
    get_current_agent,
    get_current_parent,
    get_turn_binding,
)
from codegenx.ai_service.trace.recorder import (
    background_trace,
    session_end,
    session_start,
    turn_end,
    turn_error,
    turn_start,
)
from codegenx.ai_service.trace.store import TraceStore

__all__ = [
    "api", "context", "metrics",
    "start_span", "SpanHandle", "add_turn_tags", "report_context_usage", "current_app_id",
    "get_current_agent", "get_current_parent", "get_turn_binding",
    "turn_start", "turn_end", "turn_error", "session_start", "session_end",
    "background_trace",
    "TraceStore",
]
