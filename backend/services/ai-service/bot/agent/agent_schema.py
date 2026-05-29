from enum import Enum
from typing import Any
from pydantic import BaseModel

class AgentState(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentEventType(str, Enum):
    ON_TURN_START = "OnTurnStart"
    LLM_THINKING_START = "LLM_Thinking_Start"
    LLM_RESPONSE_CHUNK = "LLM_Response_Chunk"
    TOOL_EXECUTION_START = "ToolExecutionStart"
    TOOL_EXECUTION_END = "ToolExecutionEnd"
    COMPACT_EVENT = "CompactEvent"
    REQUEST_COMPLETED = "RequestCompleted"
    REQUEST_STOPPED = "RequestStopped"
    ERROR = "Error"


class AgentEvent(BaseModel):
    event_type: AgentEventType
    data: Any = None
    state: AgentState