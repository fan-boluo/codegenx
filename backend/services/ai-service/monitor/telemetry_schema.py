from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TelemetryStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class AlertLevel(str, Enum):
    WARN = "WARN"
    ERROR = "ERROR"

class OperationName(str, Enum):
    SESSION = "session"
    CONTEXT = "context"  # 组装上下文
    LLM = "llm"  # 调用llm
    TOOL = "tool"
    MEMORY_RETRIEVE = "memory_retrieve"

"""
指标统计：
runtime中产生的指标在特定触发时机会通过pipeline先写入SpanCollector的缓冲区

"""
# 指标收集
class TurnContextMetrics(BaseModel):
    token_count: int = 0
    token_usage: float = 0.0  # 使用百分比，相对于设置的模型上下文
    is_compress: bool = False


class TurnLLMMetrics(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_token_ms: int = 0
    total_ms: int = 0
    recovery_count: int = 0
    recovery_kind: str = ""
    is_error: bool = False


class TurnToolMetrics(BaseModel):
    tool_name: str = ""
    latency_ms: int = 0  # 调用时长
    is_error: bool = False  # 调用结果


class TurnMemoryMetrics(BaseModel):
    hits: int = 0  # 命中数
    latency_ms: int = 0  # 调用时长
    is_error: bool = False
    # source: str = ""  # 来源，长期？短期


class SpanTelemetry(BaseModel):
    """ 要监测的指标 """
    trace_id: str
    session_id: str
    request_id: str
    turn_id: str
    app_id: str
    user_id: str
    # 监测的每个span
    span_id: str
    span_name: str
    operation_name: OperationName  # span是什么操作，session？llm tool mem_search
    attributes:dict[str, Any]  # 操作产生的指标，上面四类

# 落库的指标
# class SessionTelemetry(BaseModel):
#     trace_id: str = ""
#     session_id: str
#     app_id: str = "main"
#     user_id: str = ""
#     model: str = "unknown"
#     token_budget: int = 0
#     started_at: datetime | None = None
#     ended_at: datetime | None = None
#     status: TelemetryStatus = TelemetryStatus.RUNNING
#     total_turns: int = 0
#     total_prompt_tokens: int = 0
#     total_completion_tokens: int = 0
#     sum_llm_latency_ms: int = 0
#     sum_first_token_ms: int = 0
#     max_llm_latency_ms: int = 0
#     min_llm_latency_ms: int = 999999
#     total_tool_calls: int = 0
#     total_errors: int = 0
#     total_memory_hits: int = 0
#     recovery_count: int = 0
#     last_recovery_kind: str = ""
#     end_reason: str = ""


class TurnTelemetry(BaseModel):
    trace_id: str = ""
    session_id: str
    request_id: str = ""
    turn_id: str
    turn_number: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    status: TelemetryStatus = TelemetryStatus.RUNNING
    context: TurnContextMetrics = Field(default_factory=lambda: TurnContextMetrics())
    llm: TurnLLMMetrics = Field(default_factory=lambda: TurnLLMMetrics())
    tool: list[TurnToolMetrics] = Field(default_factory=list)
    memory: TurnMemoryMetrics = Field(default_factory=lambda: TurnMemoryMetrics())





# 告警记录
class MonitorAlertRecord(BaseModel):
    rule_name: str
    level: AlertLevel
    trace_id: str = ""
    session_id: str
    turn_id: str = ""
    message: str = ""
    observed_value: float | int | str | None = None
    threshold_value: float | int | str | None = None
    triggered_at: datetime | None = None
    resolved_at: datetime | None = None
    status: str = "open"
    payload: dict[str, Any] = Field(default_factory=dict)



