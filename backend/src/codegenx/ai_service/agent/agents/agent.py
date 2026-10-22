from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from codegenx.ai_service.agent.action import ActOutput
from pydantic import BaseModel
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
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
    async def think(self, messages: list[dict[str, Any]],is_stream:bool,session_state:RuntimeSessionState) -> dict[Any, Any]:
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


class AgentMessage(BaseModel):
    """Agent 间消息协议。

    agent-as-tool 同步派发后，跨 agent 消息跳只剩两条：
    - 入口跳（user→lead）：task=用户请求原文
    - 终答跳（lead→user）：result=面向用户的答复
    派发（lead→子智能体）为同步内联调用，构造 dispatch 消息传给子智能体 run()，
    其回传（result=执行结果）由 lead 的派发执行器消费，不再经调度循环路由。

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
    task_id: int | None = None     # 看板任务 ID：派发下传、回传原样带回（计划链路闭环握手）
    step_id: int | None = None     # 计划步骤号（计划任务派发携带）

    # ── 状态与低频扩展 ──
    success: bool | None = None    # 发送方执行成败
    metadata: dict[str, Any] = {}  # 低频扩展；禁止承载上述一等字段语义

    def show(self):
        return f"{self.sender} ->{self.recipient}\n" \
                f"task: {self.task_id}: {self.task}\n" \
                f"metadata: {self.metadata}\n"
