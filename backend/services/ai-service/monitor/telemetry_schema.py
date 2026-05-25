from __future__ import annotations

import secrets
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agent.runtime_schema import RuntimeSessionState


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

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


class OperationType(str, Enum):
    SESSION = "agent.session"
    TURN = "agent.turn"
    CONTEXT = "agent.context"
    LLM = "llm.call"
    TOOL = "tool"               # prefix; actual value is "tool.<name>"
    MEMORY = "memory.retrieve"


# ---------------------------------------------------------------------------
# Span attributes — 对应 SpanRecord.attributes，按 operation_type 选用
# ---------------------------------------------------------------------------

class ContextMetrics(BaseModel):
    """CONTEXT span 的 attributes"""
    token_count: int = 0
    token_usage: float = 0.0
    is_compress: bool = False


class LLMMetrics(BaseModel):
    """LLM span 的 attributes"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_token_ms: int = 0
    recovery_count: int = 0
    recovery_kind: str = ""
    is_error: bool = False


class ToolMetrics(BaseModel):
    """TOOL span 的 attributes"""
    tool_name: str = ""
    is_error: bool = False


class MemoryMetrics(BaseModel):
    """MEMORY span 的 attributes"""
    hits: int = 0
    is_error: bool = False


# ---------------------------------------------------------------------------
# SpanRecord: one row in the `spans` table
# ---------------------------------------------------------------------------

def _new_span_id() -> str:
    return secrets.token_hex(8)


class SpanRecord(BaseModel):
    span_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    parent_span_id: str | None = None
    trace_id: str
    session_id: str
    app_id: str = "main"
    user_id: str = ""
    request_id: str = ""
    step_counter: int = 0
    operation_type: str                  # e.g. OperationType.LLM.value
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int | None = None
    status: str = "running"
    attributes: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def new_session_span_recorder(session: RuntimeSessionState) -> SpanRecord:
        return SpanRecord(
            span_id=_new_span_id(),
            parent_span_id=None,
            trace_id=session.trace_id,
            session_id=session.session_id,
            app_id=session.app_id,
            user_id=session.user_id,
            request_id=session.request_id,
            operation_type=OperationType.SESSION.value,
            start_time=session.started_at,
        )

    @staticmethod
    def set_turn_info(record: SpanRecord, request_id: str, step_counter: int) -> None:
        record.request_id = request_id
        record.step_counter = step_counter

    @staticmethod
    def set_end_info(record: SpanRecord) -> None:
        now = datetime.utcnow()
        record.end_time = now
        record.duration_ms = max(0, int((now - record.start_time).total_seconds() * 1000))


# ---------------------------------------------------------------------------
# SessionTelemetry: session-level metric accumulator (lives on session state)
# ---------------------------------------------------------------------------

class SessionTelemetry(BaseModel):
    """Session-level aggregate metrics.  Flat model — no nested sub-models."""

    # Identification
    trace_id: str = ""
    session_id: str
    app_id: str = "main"
    user_id: str = ""
    model: str = "unknown"
    token_budget: int = 0

    # Timing / lifecycle
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

    @staticmethod
    def new_tel(session: RuntimeSessionState) -> SessionTelemetry:
        return SessionTelemetry(
            trace_id=session.trace_id,
            session_id=session.session_id,
            app_id=session.app_id,
            user_id=session.user_id,
            token_budget=getattr(session.runtime.agent_config, "context_max_tokens", 0),
            started_at=session.started_at,
        )

    def record_turn(self, turn: TurnTelemetry) -> None:
        """Accumulate per-turn metrics into this session summary."""
        self.total_turns += 1
        self.total_prompt_tokens += turn.llm_prompt_tokens
        self.total_completion_tokens += turn.llm_completion_tokens
        llm_ms = turn.llm_total_ms
        self.sum_llm_latency_ms += llm_ms
        self.sum_first_token_ms += turn.llm_first_token_ms
        if llm_ms > 0:
            self.max_llm_latency_ms = max(self.max_llm_latency_ms, llm_ms)
            self.min_llm_latency_ms = min(self.min_llm_latency_ms, llm_ms)
        self.total_tool_calls += len(turn.tool_calls)
        if turn.status == TelemetryStatus.ERROR:
            self.total_errors += 1
        self.total_memory_hits += turn.memory_hits
        if turn.llm_recovery_count > 0:
            self.recovery_count += turn.llm_recovery_count
            self.last_recovery_kind = turn.llm_recovery_kind


# ---------------------------------------------------------------------------
# TurnTelemetry: per-turn tracking object (lives on turn state in handlers)
# ---------------------------------------------------------------------------

class TurnTelemetry(BaseModel):
    """Per-turn metrics.  Flat model — no nested sub-models."""

    # Identification
    trace_id: str = ""
    session_id: str = ""
    request_id: str = ""
    turn_id: str = ""
    turn_number: int = 0
    app_id: str = "main"
    user_id: str = ""
    model: str = "unknown"
    token_budget: int = 0

    # Timing / lifecycle
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    status: TelemetryStatus = TelemetryStatus.RUNNING

    # Per-turn LLM
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_first_token_ms: int = 0
    llm_total_ms: int = 0
    llm_recovery_count: int = 0
    llm_recovery_kind: str = ""
    llm_is_error: bool = False

    # Per-turn Context
    context_token_count: int = 0
    context_token_usage: float = 0.0
    context_is_compress: bool = False

    # Per-turn Tool calls
    tool_calls: list[dict] = Field(default_factory=list)

    # Per-turn Memory
    memory_hits: int = 0
    memory_latency_ms: int = 0
    memory_is_error: bool = False

    @staticmethod
    def new_tel(session_tel: SessionTelemetry) -> TurnTelemetry:
        return TurnTelemetry(
            trace_id=session_tel.trace_id,
            session_id=session_tel.session_id,
            app_id=session_tel.app_id,
            user_id=session_tel.user_id,
            model=session_tel.model,
            token_budget=session_tel.token_budget,
            started_at=datetime.utcnow(),
        )


# ---------------------------------------------------------------------------
# MonitorAlertRecord
# ---------------------------------------------------------------------------

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
