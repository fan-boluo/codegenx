from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
from bot.agent.agent_schema import AgentState
import secrets

class OperationType(str, Enum):
    SESSION = "agent.session"   # session 和 turn 用于统计
    TURN = "agent.turn"
    CONTEXT = "agent.context"
    LLM = "llm.call"
    TOOL = "tool"               # prefix; actual value is "tool.<name>"
    MEMORY = "memory.retrieve"


class ContextMetrics(BaseModel):
    token_count: int = 0        # token 预估长度
    token_usage: float = 0.0    # 使用百分比
    is_compress: bool = False   # 是否压缩


class LLMMetrics(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_token_ms: int = 0
    recovery_count: int = 0
    recovery_kind: str = ""
    is_error: bool = False


class ToolMetrics(BaseModel):
    tool_name: str = ""
    is_error: bool = False


class MemoryMetrics(BaseModel):
    hits: int = 0
    is_error: bool = False


class SpanRecord(BaseModel):
    span_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    parent_span_id: str | None = None
    trace_id: str
    session_id: str
    app_id: str = ""
    user_id: str = ""
    request_id: str = ""
    step_counter: int = 0                   # 第几次迭代
    operation_type: str                     # e.g. OperationName.LLM.value
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)  # metrics


class BaseTelemetry(BaseModel):
    trace_id: str = ""
    session_id: str = ""
    request_id: str = ""
    app_id: str = ""
    user_id: str = ""
    model: str = "unknown"
    span_id: str = ""


class SessionTelemetry(BaseModel):
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: AgentState = AgentState.IDLE   # 初始状态
    end_reason: str = ""

    turn_number: int = 0                   # 第几轮
    # 上下文的情况
    token_count: int = 0                   # token 预估长度
    token_usage: float = 0.0               # 使用百分比
    is_compress: bool = False              # 是否压缩

    # llm
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    max_duration_ms: int | None = None      # 截止目前的延迟统计指标
    min_duration_ms: int | None = None
    recovery_count: int = 0                 # 重新恢复次数
    last_recovery_kind: str = ""

    # tool
    total_tool_calls: int = 0
    total_tool_call_errors: int = 0

    # mem
    total_memory_hits: int = 0


class TurnTelemetry(SessionTelemetry):
    turn_id: str = ""  # 只加一个 turn_id


"""
memory hit指标应该在哪里加
入口还是在hook吗
spanrecord缓存，turn session结束刷到库mysql
两个telemetry实时更新 OpenTelemetry Prometheus
告警的还没想好

"""