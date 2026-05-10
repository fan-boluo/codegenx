from __future__ import annotations

import secrets
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
    SESSION = "agent.session"
    CONTEXT = "agent.context"
    LLM = "llm.call"
    TOOL = "tool"          # prefix; actual value is "tool.<name>"
    MEMORY = "memory.retrieve"
    TURN = "agent.turn"


# ---------------------------------------------------------------------------
# Per-turn metric sub-models (tracked live in handlers)
# ---------------------------------------------------------------------------

class TurnContextMetrics(BaseModel):
    token_count: int = 0
    token_usage: float = 0.0
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
    latency_ms: int = 0
    is_error: bool = False


class TurnMemoryMetrics(BaseModel):
    hits: int = 0
    latency_ms: int = 0
    is_error: bool = False


# ---------------------------------------------------------------------------
# SpanRecord: one row in the `spans` table, buffered in SpanCollector
# ---------------------------------------------------------------------------

class SpanRecord(BaseModel):
    span_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    parent_span_id: str | None = None
    trace_id: str
    session_id: str
    app_id: str = "main"
    user_id: str = ""
    turn_id: str = ""
    turn_number: int = 0
    operation_name: str          # e.g. OperationName.LLM.value
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int | None = None
    status: str = "running"
    attributes: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# TurnTelemetry: per-turn tracking object (lives on turn state in handlers)
# ---------------------------------------------------------------------------

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
    context: TurnContextMetrics = Field(default_factory=TurnContextMetrics)
    llm: TurnLLMMetrics = Field(default_factory=TurnLLMMetrics)
    tool: list[TurnToolMetrics] = Field(default_factory=list)
    memory: TurnMemoryMetrics = Field(default_factory=TurnMemoryMetrics)


# ---------------------------------------------------------------------------
# SessionTelemetry: session-level metric accumulator (lives on session state)
# ---------------------------------------------------------------------------

class SessionTelemetry(BaseModel):
    trace_id: str = ""
    session_id: str
    app_id: str = "main"
    user_id: str = ""
    model: str = "unknown"
    token_budget: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: TelemetryStatus = TelemetryStatus.RUNNING
    end_reason: str = ""
    # Aggregated from turns
    total_turns: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    sum_llm_latency_ms: int = 0
    sum_first_token_ms: int = 0
    max_llm_latency_ms: int = 0
    min_llm_latency_ms: int = 999999
    total_tool_calls: int = 0
    total_errors: int = 0
    total_memory_hits: int = 0
    recovery_count: int = 0
    last_recovery_kind: str = ""

    def record_turn(self, turn: TurnTelemetry) -> None:
        """Accumulate per-turn metrics into this session summary."""
        self.total_turns += 1
        self.total_prompt_tokens += turn.llm.prompt_tokens
        self.total_completion_tokens += turn.llm.completion_tokens
        llm_ms = turn.llm.total_ms
        self.sum_llm_latency_ms += llm_ms
        self.sum_first_token_ms += turn.llm.first_token_ms
        if llm_ms > 0:
            self.max_llm_latency_ms = max(self.max_llm_latency_ms, llm_ms)
            self.min_llm_latency_ms = min(self.min_llm_latency_ms, llm_ms)
        self.total_tool_calls += len(turn.tool)
        if turn.status == TelemetryStatus.ERROR:
            self.total_errors += 1
        self.total_memory_hits += turn.memory.hits
        if turn.llm.recovery_count > 0:
            self.recovery_count += turn.llm.recovery_count
            self.last_recovery_kind = turn.llm.recovery_kind



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



