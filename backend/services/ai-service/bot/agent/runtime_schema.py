import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.runtime import AgentRuntime
from monitor import TurnTelemetry, SessionTelemetry
from session.manager import SessionManager
from shared.schema.ai_service import AiServiceGenerateRequest


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class TurnContext(BaseModel):

    plan_summary: str = ""
    tool: list[Any] = Field(default_factory=list)
    skill: list[Any] = Field(default_factory=list)
    memory: list[Any] = Field(default_factory=list)
    workspace_metadata:dict = {}
    system_prompt: str = ""
    chat_message: list[dict[str, Any]] = Field(default_factory=list)


class AgentEvent(BaseModel):
    event_type: str
    data: Any = None
    state: AgentState


class TurnStoppedError(Exception):
    pass


@dataclass
class RuntimeRequestState:
    request: AiServiceGenerateRequest
    request_id: str
    context: TurnContext
    code_dir: str = ""
    safe_paths: list[str] = field(default_factory=list)
    workspace_metadata: dict[str, Any] = field(default_factory=dict)
    knowledge_cache: dict[str, Any] = field(default_factory=dict)
    prompt_template: str = ""
    plan_summary: str = ""
    state: AgentState = AgentState.IDLE
    error_text: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    turn_counter: int = 0
    active_turn_id: str = ""
    tool_iterations: int = 0
    last_tool_signature: str | None = None
    consecutive_same_tool_calls: int = 0


@dataclass
class RuntimeTurnState:
    request: AiServiceGenerateRequest
    request_state: RuntimeRequestState
    turn_id: str
    turn_number: int
    context: TurnContext
    code_dir: str = ""
    safe_paths: list[str] = field(default_factory=list)
    workspace_metadata: dict[str, Any] = field(default_factory=dict)
    knowledge_cache: dict[str, Any] = field(default_factory=dict)
    prompt_template: str = ""
    plan_summary: str = ""
    snapshot_path: str = ""
    telemetry: TurnTelemetry | None = None
    state: AgentState = AgentState.IDLE
    transition_reason: str = ""
    error_text: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    active_span_refs: dict[str, Any] = field(default_factory=dict)
    requires_followup: bool = False


@dataclass
class RuntimeSessionState:
    session_id: str
    request: AiServiceGenerateRequest | None
    runtime: AgentRuntime
    session_manager: SessionManager | None = None
    workspace_metadata: dict[str, Any] = field(default_factory=dict)
    skill:list[Any] = field(default_factory=list)
    tool:list[Any] = field(default_factory=list)

    telemetry: SessionTelemetry | None = None
    turn_counter: int = 0
    started_at: float = 0.0
    last_activity_at: float = 0.0
    state: AgentState = AgentState.IDLE
    session_span: Any = None
    session_span_started_at: datetime | None = None
    # active_requests: dict[str, RuntimeRequestState] = field(default_factory=dict)
    # active_turns: dict[str, RuntimeTurnState] = field(default_factory=dict)
    active_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    worker_task: asyncio.Task | None = None
    queue: asyncio.Queue[AiServiceGenerateRequest] = field(default_factory=asyncio.Queue)
    stop_signal: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str = ""
    closed: bool = False

    def touch(self) -> None:
        self.last_activity_at = time.time()

    @property
    def client_version(self) -> str:
        return str(getattr(self.request, "client_version", "ai-service") or "ai-service")
