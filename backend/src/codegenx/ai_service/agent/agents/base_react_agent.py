from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, List

from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.llm.async_client import get_llm
from codegenx.ai_service.llm.llm_recovery import LLMRecovery
from codegenx.ai_service.llm.resilience import get_breaker
from codegenx.ai_service.utils.config import AgentConfig, config
from codegenx.ai_service.system_app import get_app
from shared import log
from codegenx.ai_service.agent.agents.agent import Agent, AgentContext, AgentMessage

from codegenx.ai_service.agent.action import Action
from codegenx.ai_service.prompt.prompt_manager import PromptConfig


class BaseReActAgent(Agent):
    """
    一个基础思考，行动，验证agent
    """


    def __init__(
            self,
            system_app=None,
    ):
        Agent.__init__(self)
        # agent_config 已由 resolve_agent_config() 类方法解析为类属性；
        # 若尚未解析（如未走系统初始化），此处兜底调用。
        if self.agent_config is None:
            self.resolve_agent_config()

        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = self.agent_config.max_same_tool_calls or 3
        self.max_steps = self.agent_config.max_steps

        # 初始化即解析默认智能体模型链
        self.model_name = self.agent_config.resolved_model_name
        # 准备llm,breaker
        with suppress(Exception):
            get_llm(self.model_name)
            get_breaker(self.model_name)
        self._llm_recovery = LLMRecovery()
        # agent 间寻址标识与收件箱（agent_name 全局唯一）
        self.agent_name = self.agent_config.name
        self._inbox: asyncio.Queue | None = None
        self.actions: List[Action] = []   # act要执行的动作
        # 保留子类定义的 profile；仅未设置时兜底为空 PromptConfig
        if not getattr(self, "profile", None):
            self.profile: PromptConfig = PromptConfig()
        log.info(f"初始化agent: {self.agent_name} 完成")


    # -----类方法：配置解析与注册---------------------

    @classmethod
    def resolve_agent_config(cls) -> None:
        """按 cls.name 匹配 config.agents，将配置存为类属性（系统初始化时调用）。

        匹配后同时填充 cls.id（来自配置）和 cls.agent_config。
        """
        matched = AgentConfig()
        for agent in config.agents or []:
            if (agent.name or "").strip() and agent.name.strip() == cls.name.strip():
                matched = agent
                break
        cls.agent_config = matched
        # 从配置同步 id 到类属性（配置优先，缺省回落 cls.name）
        cls.id = str(getattr(matched, "id", "") or cls.name)

    @classmethod
    def register(cls) -> None:
        """将自身规格注册到 AgentRegistry（系统初始化时调用）。

        从类属性 id/role/name/desc + cls 本身构建 AgentSpec 并注册。
        """
        from codegenx.ai_service.system_app import get_app
        from codegenx.ai_service.agent.agent_registry import AgentSpec

        registry = get_app().agents
        spec = AgentSpec(
            id=cls.agent_config.id or cls.agent_config.name,
            role=cls.agent_config.role,
            name=cls.agent_config.name,
            desc=cls.agent_config.desc,
            cls=cls,
        )
        registry.register_spec(spec)

    def _ensure_inbox(self) -> None:
        """注册 agent 收件箱（幂等）。"""
        if self._inbox is None:
            self._inbox = get_app().message_bus.register_agent(self.agent_name)

    async def _prepare_agent(
        self,
        session_state: RuntimeSessionState,
    ) -> None:
        """准备 agent 所需的上下文与收件箱（子类可覆盖扩展，需先调 super()）。"""
        # self._ensure_inbox()
        self.tools: list[dict] = getattr(self, "tools", None) or []
        self.agent_context = AgentContext(
            session_id=session_state.session_id,
            app_id=str(session_state.app_id or ""),
            user_id=session_state.user_id,
            agent_name=self.agent_name,
        )
        params = self._prepare_prompt_params()
        self._system_prompt = self.profile.build_default_system_prompt(**params)

    def _prepare_prompt_params(self) -> dict:
        """ 在build prompt之前，子类各自准备 """
        return {}
    # ------------------------------------------------------------------ message I/O

    async def receive(self, timeout: float | None = None) -> AgentMessage | None:
        """从 agent 收件箱接收消息（timeout=None 阻塞，0 非阻塞）。"""
        self._ensure_inbox()
        if timeout == 0:
            if self._inbox.empty():
                return None
            msg = await self._inbox.get()
        else:
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        log.info(
            "agent 消息接收: {} <- {} | type={} request_id={}",
            self.agent_name, msg.sender, msg.message_type, msg.request_id,
        )
        return msg

    async def send(
        self,
        recipient: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        **kwargs,
    ) -> bool:
        """向指定 name 的 agent 发送消息，"*" 表示广播。"""
        self._ensure_inbox()
        msg = AgentMessage(
            sender=self.agent_name,
            recipient=recipient,
            message_type=message_type,
            payload=payload or {},
            request_id=kwargs.get("request_id", "") or self.agent_context.session_id,
            session_id=self.agent_context.session_id,
            success=kwargs.get("success", True),
            message=kwargs.get("message", ""),
            next_agent=kwargs.get("next_agent"),
        )
        delivered = await get_app().message_bus.send_to_agent(msg)
        log.info(
            "agent 消息发送: {} -> {} | type={} request_id={} delivered={}",
            msg.sender, msg.recipient, msg.message_type, msg.request_id, delivered,
        )
        return delivered

    # ------------------------------------------------------------------ ReAct lifecycle

    @staticmethod
    def _extract_task_text(message: AgentMessage | None, session_state: RuntimeSessionState) -> str:
        """从接收消息或用户请求中提取任务文本。"""
        if message is not None:
            text = (message.message or "").strip()
            if not text:
                payload = message.payload or {}
                text = str(payload.get("task") or payload.get("goal") or "").strip()
            if text:
                return text
        request = session_state.request
        return str(getattr(request, "message", "") or "").strip() if request else ""



    async def think(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """思考：调用 LLM 推理，返回 {content, tool_calls}（子类可覆盖定制提示词/工具策略）。"""
        llm = get_llm(self.model_name)
        completion = await llm.client.chat.completions.create(
            model=llm.model_name,
            messages=messages,
            tools=self.tools or None,
            temperature=self.agent_config.temperature,
            max_tokens=self.agent_config.max_tokens,
        )
        if not completion.choices:
            log.warning("agent think: {} LLM 返回空 choices", self.agent_name)
            return {"content": "", "tool_calls": []}
        message = completion.choices[0].message
        tool_calls: list[dict[str, Any]] = []
        for tc in (message.tool_calls or []):
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
                if not isinstance(arguments, dict):
                    arguments = {"_raw": arguments}
            except (json.JSONDecodeError, TypeError):
                log.warning("agent think: {} 工具参数解析失败: {}", self.agent_name, tc.function.arguments)
                arguments = {"_raw": tc.function.arguments}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": arguments})
        return {"content": message.content or "", "tool_calls": tool_calls}

    async def act(self, ai_message: Any = None, **kwargs) -> Any:
        """行动：遍历 self.actions 链式执行，上一个结果传给下一个，返回最后一个结果。

        ai_message 首个 Action 收到 think() 的返回 {"content","tool_calls"}；
        后续 Action 收到上一个 Action 的返回值。
        """
        last_result = ai_message
        for action in self.actions:
            last_result = await action.act(ai_message=last_result, **kwargs)
            log.info("agent act: {} action={} 完成", self.agent_name, type(action).__name__)
        return last_result

    async def verify(self, final_text: str, messages: list[dict[str, Any]]) -> tuple[bool, str]:
        """验证：检查最终结果是否可用（子类可覆盖定制校验规则）。返回 (ok, reason)。"""
        if not (final_text or "").strip():
            return False, "最终结果为空"
        return True, ""

    async def run(self, session_state: RuntimeSessionState) -> AgentMessage:
        """ReAct 方式执行：think → act 循环 → verify。

        核心流程仅保留消息收发、LLM 推理与工具执行，全部过程记录到日志；
        监控、中间消息 publish、hook、记忆、存储、上下文管理等后续接入。

        参数:
            session_state: 运行时会话状态，携带 request/session/user 等标识。
        """
        # 1. 准备 agent 上下文与收件箱
        await self._prepare_agent(session_state)

        # 2. 获取接收的消息（用户消息经 runtime 投递，或上游 agent 发送）
        message = await self.receive(timeout=0)
        request_id = (message.request_id if message else "") or session_state.request_id
        task_text = self._extract_task_text(message, session_state)
        log.info("agent run 开始: {} | request_id={} | task={}", self.agent_name, request_id, task_text)

        # 3. 组装本地对话消息（仅内存，不落库）
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task_text},
        ]

        # 4. ReAct 循环：think → act，直到无工具调用或达到 max_steps
        step = 0
        final_text = ""
        while step < self.max_steps:
            step += 1
            log.info("agent run: {} step {}/{} think 开始", self.agent_name, step, self.max_steps)
            response = await self.think(messages)
            final_text = str(response.get("content") or "")
            tool_calls = response.get("tool_calls") or []
            log.info("agent run: {} step {}/{} think 结束 | content_len={} tool_calls={}",
                     self.agent_name, step, self.max_steps, len(final_text), len(tool_calls))
            # assistant 消息回写本地对话
            assistant_message: dict[str, Any] = {"role": "assistant", "content": final_text}
            if tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_message)
            if not tool_calls:
                break
            # act：遍历 actions 链式执行，结果回写本地对话
            act_result = await self.act(response, session_state=session_state)
            if isinstance(act_result, list):
                messages.extend(act_result)
        else:
            log.warning("agent run: {} 达到 max_steps={}，强制结束", self.agent_name, self.max_steps)

        # 5. verify：校验最终结果
        ok, reason = await self.verify(final_text, messages)
        log.info("agent run 结束: {} | request_id={} | steps={} | ok={} | reason={}",
                 self.agent_name, request_id, step, ok, reason)

        # 6. 返回结果（返回值直接交回调用方 runtime / 上游 agent）
        return AgentMessage(
            sender=self.agent_name,
            recipient="",
            message_type="result_return",
            payload={"result": final_text},
            request_id=request_id,
            session_id=session_state.session_id,
            success=ok,
            message=reason or final_text[:200],
        )
