from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agent.agent_schema import AgentState
from task.task_manager import TaskManager
from context.session_context import SessionContext
from monitor.telemetry_schema import SpanRecord
from session.manager import SessionManager
from shared.schema.ai_service import AiServiceGenerateRequest

if TYPE_CHECKING:
    from agent.runtime import AgentRuntime


class TurnStoppedError(Exception):
    pass

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
    active_span_refs:dict = field(default_factory=dict)
    llm_recovery_count: int = 0
    last_recovery_kind: str = ""
    error_text: str = ""

    def add_turn_span_id(self, key: str, span_id: str) -> None:
        """Register an active span id for the current turn (e.g. 'turn_span_id', 'llm_span_id')."""
        self.active_span_refs[key] = span_id

    def pop_turn_span(self, key: str) -> str | None:
        """Remove and return a span id by key. Returns None if the key doesn't exist."""
        return self.active_span_refs.pop(key, None)


@dataclass
class RuntimeSessionState:
    """Persistent session state. Per-request fields are reset by _reset_request_state."""
    session_id: str
    request: AiServiceGenerateRequest | None
    runtime: AgentRuntime
    context_manager: SessionContext | None = None  # 整个会话的上下文管理（由 on_session_start hook 初始化）
    session_manager: SessionManager | None = None  # TODO 转移到后台监控模块，负责落盘的
    task_manager: TaskManager | None = None  # 任务看板，跟随 session 生命周期


    # Monitoring (initialised by on_session_start hook)
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
    started_at: datetime | None = None
    last_activity_at: float = 0.0
    state: AgentState = AgentState.IDLE
    processing: bool = False
    closed: bool = False
    close_signal: asyncio.Event = field(default_factory=asyncio.Event)  # 用于 event 通知替代忙等轮询
    stop_signal: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str = ""
    worker_task: asyncio.Task | None = None  # 一个携程任务


    def touch(self) -> None:
        self.last_activity_at = time.time()

    @property
    def trace_id(self) -> str:
        return str(getattr(self.request, "trace_id", "") or "")

    @property
    def app_id(self) -> str:
        return str(getattr(self.request, "app_id", "main") or "main")

    @property
    def db_name(self) -> str | None:
        return getattr(self.request, "db_name", None)

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

    @property
    def safe_paths(self) -> list|None:
        if self.context_manager:
            return self.context_manager.get_safe_path()
        return None
