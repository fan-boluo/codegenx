from enum import Enum
from typing import Any
from pydantic import BaseModel

class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentEvent(BaseModel):
    event_type: str
    data: Any = None
    state: AgentState