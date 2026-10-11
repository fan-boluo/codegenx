from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel

from codegenx.ai_service.compact.session_summary import SummaryState
from codegenx.ai_service.context.context_service import TurnPrompts


class Agent(ABC):
    """Agent Interface."""

    @abstractmethod
    async def run(self, session_state) -> "AgentMessage":
        """Generate a reply based on the received messages."""

    @abstractmethod
    async def receive(self, timeout: float | None = None) -> "AgentMessage | None":
        """Receive a message from another agent or the user.

        timeout=None 阻塞等待；timeout=0 非阻塞，无消息返回 None。
        """

    @abstractmethod
    async def send(
        self,
        recipient: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        **kwargs,
    ) -> bool:
        """Send a message to another agent (recipient 为对方 role，"*" 广播)."""

    @abstractmethod
    async def act(self):
        """Perform actions."""

    @abstractmethod
    async def thinking(self):
        """Think and reason about the current task goal."""

    @abstractmethod
    async def verify(self):
        """Verify whether the current execution results meet the target expectations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of the agent."""

    @property
    @abstractmethod
    def desc(self) -> Optional[str]:
        """Return the description of the agent."""


@dataclass
class AgentContext:
    session_id: str = ""
    app_id: str = ""
    user_id: str = ""
    db_name: str | None = None
    agent_name: str = ""
    system_prompt: str = ""
    # 聊天对话
    chat_messages: list[dict[str, Any]] = field(default_factory=list)
    # 每轮组装产物
    prompts: TurnPrompts = field(default_factory=TurnPrompts)
    # 会话摘要阈值状态
    summary_state: SummaryState = field(default_factory=SummaryState)


class AgentMessage(BaseModel):
    """Agent 间消息（控制面）：任务分配、结果返回、意图路由等。

    request_id 复用用户请求 ID，串联同一次任务的全链路消息，不新增 correlation_id。
    """

    sender: str = ""               # 发送方 name
    recipient: str = ""            # 接收方 name，"*" 表示广播
    message_type: str = ""         # task_assign / result_return / intent_result / plan_ready ...
    payload: dict[str, Any] = {}   # 业务数据
    request_id: str = ""           # 任务关联 ID（复用请求 ID）
    session_id: str = ""
    success: bool = True
    message: str = ""              # 文本摘要
    next_agent: str | None = None  # 路由建议（如 intent 识别后指定下一 agent name）
