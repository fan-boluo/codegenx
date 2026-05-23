from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from adapter.session_context import SessionContext
from monitor.telemetry_schema import SessionTelemetry, TurnTelemetry, SpanRecord
from session.manager import SessionManager
from shared.schema.ai_service import AiServiceGenerateRequest

if TYPE_CHECKING:
    from agent.runtime import AgentRuntime


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class TurnContext(BaseModel):
    """LLM-call context for a single turn."""
    plan_summary: str = ""
    tool: list[Any] = Field(default_factory=list)
    skill: list[Any] = Field(default_factory=list)
    memory: list[Any] = Field(default_factory=list)
    workspace_metadata: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = ""
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    user_input: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    event_type: str
    data: Any = None
    state: AgentState


class TurnStoppedError(Exception):
    pass


@dataclass
class RuntimeTurnState:
    """State for one LLM turn. No back-reference to request object."""
    turn_id: str
    turn_number: int
    request_id: str

    context: TurnContext
    telemetry: TurnTelemetry|None = None

    # code_dir: str = ""
    # safe_paths: list[str] = field(default_factory=list)
    # workspace_metadata: dict[str, Any] = field(default_factory=dict)
    # knowledge_cache: dict[str, Any] = field(default_factory=dict)
    # prompt_template: str = ""
    # plan_summary: str = ""

    snapshot_path: str = ""
    state: AgentState = AgentState.IDLE
    transition_reason: str = ""
    error_text: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    # 记录各个span的状态
    active_span_refs: dict[str, Any] = field(default_factory=dict)
    # 是否调用工具
    requires_followup: bool = False
    turn_record:SpanRecord =  None

@dataclass
class ActivateTurn:
    step_counter: int = 0  # 一次请求（turn）的迭代次数
    # active_steps: dict[str, RuntimeTurnState] = field(default_factory=dict)  # 活跃的step_id
    active_step_id: str = ""
    active_steps: list[str] = field(default_factory=list)  # 活跃的step_id
    state: AgentState = AgentState.IDLE  # 一次turn的状态
    requires_followup:bool = False  # 是否需要继续，如果没有工具调用则停止
    started_at: float = 0.0
    finished_at: float = 0.0
    telemetry: TurnTelemetry | None = None


@dataclass
class RuntimeSessionState:
    """Persistent session state. Per-request fields are reset by _reset_request_state."""
    session_id: str
    request: AiServiceGenerateRequest | None
    runtime: AgentRuntime
    context_manager:SessionContext | None  # 整个会话的上下文管理
    session_manager: SessionManager | None = None  # TODO 转移到后台监控模块，负责落盘的


    # Monitoring (initialised by on_session_start hook)
    telemetry: SessionTelemetry | None = None
    session_record: SpanRecord | None = None
    # span_collector: Any = None
    root_span_id: str = ""
    root_span_started_at: datetime | None = None

    # session 中的请求任务
    request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # 获取请求任务锁
    pending_requests: list[AiServiceGenerateRequest] = field(default_factory=list)  # 等待的请求
    active_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)  # 活跃的请求任务
    activate_turn:ActivateTurn = field(default_factory=ActivateTurn)  # 当前获取的turn

    # tool
    tool_iterations: int = 0
    last_tool_signature: str | None = None
    consecutive_same_tool_calls: int = 0


    # Session lifecycle
    started_at: datetime = ""
    last_activity_at: float = 0.0
    state: AgentState = AgentState.IDLE
    processing: bool = False
    closed: bool = False
    stop_signal: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str = ""
    worker_task: asyncio.Task | None = None  # 当前的session worker

    # Task board (s12) — per-app_id, initialised on session start
    # task_manager: Any = None  # bot.agent.task.task_manager.TaskManager

    # Per-request fields (reset per request)

    # request_id: str = ""
    # context: TurnContext | None = None
    # code_dir: str = ""
    # safe_paths: list[str] = field(default_factory=list)
    # knowledge_cache: dict[str, Any] = field(default_factory=dict)
    # prompt_template: str = ""
    # plan_summary: str = ""


    def touch(self) -> None:
        self.last_activity_at = time.time()

    @property
    def trace_id(self) -> str:
        return str(getattr(self.request, "trace_id", "") or "")

    @property
    def app_id(self) -> str:
        return str(getattr(self.request, "app_id", "main") or "main")

    @property
    def user_id(self) -> str:
        return str(getattr(self.request, "user_id", "") or "")

    @property
    def request_id(self) -> str:
        return str(getattr(self.request, "request_id", "") or "")

    @property
    def client_version(self) -> str:
        return str(getattr(self.request, "client_version", "ai-service") or "ai-service")

    @property
    def audit_context(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "app_id": self.app_id,
            "user_id": self.user_id,
            "request_id": self.request.request_id,
        }
