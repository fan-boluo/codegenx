from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from codegenx.ai_service.agent.agent_schema import AgentState
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
    async def run(self, msg: AgentMessage, session_state: RuntimeSessionState) -> AgentMessage:
        """Generate a reply based on the received messages."""

    @abstractmethod
    async def receive(self, msg: AgentMessage, session_state: RuntimeSessionState) -> AgentMessage | None:
        """Receive a message from another agent or the user.

        timeout=None 阻塞等待；timeout=0 非阻塞，无消息返回 None。
        """

    @abstractmethod
    async def send(
        self,
        msg: AgentMessage,
        session_state: RuntimeSessionState,
        **kwargs,
    ) -> bool:
        """Send a message to another agent (recipient 为对方 role，"*" 广播)."""

    @abstractmethod
    async def act(self, llm_response: dict[str, Any], session_state: RuntimeSessionState,
                  **kwargs) -> ActOutput:
        """Perform actions."""

    @abstractmethod
    async def think(self, messages: list[dict[str, Any]]) -> dict[Any, Any]:
        """Think and reason about the current task goal."""

    @abstractmethod
    async def verify(self, observation: AgentMessage) -> tuple[bool, str]:
        """Verify whether the current execution results meet the target expectations."""

    @abstractmethod
    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """  action之后的操作，根据action的结果决定下一步操作"""
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
    """Agent 间消息：路由循环中的一跳（AutoPlanManager 内存接力）。

    语义契约（按跳向，一跳通常只填其一）：
    - 派发跳（supervisor→executor / planner）：task=任务描述
    - 回传跳（executor/planner→supervisor）：result=执行结果；task 保留入参原值（追溯）
    - 终答跳（*→user）：result=面向用户的答复

    request_id 复用用户请求 ID，串联同一次任务的全链路消息，不新增 correlation_id。
    """

    # ── 信封：路由与追踪（调度器 AutoPlanManager 消费） ──
    sender: str = ""               # 发送方 agent_name；"user" 表示用户
    recipient: str = ""            # 接收方 agent_name；"user" 表示面向用户的终答
    request_id: str = ""           # 任务关联 ID（复用请求 ID）
    session_id: str = ""

    # ── 载荷：接收方消费 ──
    task: str = ""                 # 要求接收方完成的任务文本
    result: str = ""               # 发送方执行结果 / 面向用户的答复
    task_id: int | None = None     # 看板任务 ID：派发下传、回传原样带回（闭环握手）、直答清除
    step_id: int | None = None     # 计划步骤号（planner 模式派发携带）
    direct_reply: bool = False     # 派发标记：执行 agent 完成后直答用户不回传 supervisor

    # ── 状态与低频扩展 ──
    success: bool | None = None    # 发送方执行成败
    metadata: dict[str, Any] = {}  # 低频扩展；禁止承载上述一等字段语义
