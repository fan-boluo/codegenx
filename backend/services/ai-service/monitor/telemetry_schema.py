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


class SessionTelemetry(BaseModel):
    trace_id: str = ""
    session_id: str
    app_id: str = "main"
    user_id: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: TelemetryStatus = TelemetryStatus.RUNNING
    total_turns: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tool_calls: int = 0
    total_memory_hits: int = 0
    recovery_count: int = 0

class TurnTelemetry(BaseModel):
    trace_id: str = ""
    session_id: str
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

# 指标收集
class TurnContextMetrics(BaseModel):
    token_count: int = 0
    token_usage: int = 0


class TurnLLMMetrics(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_token_ms: int = 0
    total_ms: int = 0
    recovery_count: int = 0
    status: TelemetryStatus = TelemetryStatus.RUNNING
    recovery_kind: str = ""


class TurnToolMetrics(BaseModel):
    tool_name: str = ""
    latency_ms: int = 0
    status: TelemetryStatus = TelemetryStatus.SUCCESS
    call_count: int = 0


class TurnMemoryMetrics(BaseModel):
    hits: int = 0
    latency_ms: int = 0
    source: str = ""



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