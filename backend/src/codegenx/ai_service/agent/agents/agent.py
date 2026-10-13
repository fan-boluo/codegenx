from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel

from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.compact.session_summary import SummaryState
from codegenx.ai_service.context.context_service import TurnPrompts
from codegenx.ai_service.prompt.prompt_manager import PromptConfig


class Agent(ABC):
    """Agent Interface.

    类属性 id/role/name/desc 由子类定义，resolve_agent_config() 类方法
    在系统初始化时读取配置并填充 agent_config 类属性；register() 类方法
    将自身规格注册到 AgentRegistry。
    """
    name: str = ""
    agent_config: Any = None  # 由 resolve_agent_config() 类方法填充
    profile: PromptConfig = None

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
    async def act(self,ai_message: dict[str, Any],session_state:RuntimeSessionState, **kwargs):
        """Perform actions."""

    @abstractmethod
    async def think(self,messages: list[dict[str, Any]]):
        """Think and reason about the current task goal."""

    @abstractmethod
    async def verify(self,final_text: str, messages: list[dict[str, Any]]):
        """Verify whether the current execution results meet the target expectations."""

    # ------------------------------------------------------------------ 类方法：配置解析与注册

    @classmethod
    def resolve_agent_config(cls) -> None:
        """按 cls.name 从 config.agents 匹配配置，存为类属性（系统初始化时调用）。

        子类（如 BaseReActAgent）可覆盖以读取更丰富的配置字段。
        """

    @classmethod
    def register(cls) -> None:
        """将自身规格注册到 AgentRegistry（系统初始化时调用）。

        从类属性 id/role/name/desc + cls 本身构建 AgentSpec 并注册。
        """
        


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
