from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

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
    telemetry: TurnTelemetry

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
class RuntimeSessionState:
    """Persistent session state. Per-request fields are reset by _reset_request_state."""
    session_id: str
    request: AiServiceGenerateRequest | None
    runtime: AgentRuntime

    session_manager: SessionManager | None = None
    workspace_metadata: dict[str, Any] = field(default_factory=dict)
    skill: list[Any] = field(default_factory=list)
    tool: list[Any] = field(default_factory=list)

    # Monitoring (initialised by on_session_start hook)
    telemetry: SessionTelemetry | None = None
    session_record: SpanRecord | None = None
    # span_collector: Any = None
    root_span_id: str = ""
    root_span_started_at: datetime | None = None

    # Session lifecycle
    turn_counter: int = 0
    started_at: datetime = ""
    last_activity_at: float = 0.0
    state: AgentState = AgentState.IDLE
    active_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    active_turns: dict[str, RuntimeTurnState] = field(default_factory=dict)
    worker_task: asyncio.Task | None = None
    queue: asyncio.Queue[AiServiceGenerateRequest] = field(default_factory=asyncio.Queue)
    stop_signal: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str = ""
    closed: bool = False

    # Task board (s12) — per-app_id, initialised on session start
    task_manager: Any = None  # bot.agent.task.task_manager.TaskManager

    # Per-request fields (reset per request)

    # request_id: str = ""
    # context: TurnContext | None = None
    # code_dir: str = ""
    # safe_paths: list[str] = field(default_factory=list)
    # knowledge_cache: dict[str, Any] = field(default_factory=dict)
    # prompt_template: str = ""
    # plan_summary: str = ""
    tool_iterations: int = 0
    last_tool_signature: str | None = None
    consecutive_same_tool_calls: int = 0
    active_turn_id: str = ""

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
