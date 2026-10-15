from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, List
from codegenx.ai_service.agent.agent_schema import AgentState,AgentEventType
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState, raise_if_stop_signal
from codegenx.ai_service.llm.async_client import get_llm
from codegenx.ai_service.llm.llm_recovery import LLMRecovery
from codegenx.ai_service.llm.resilience import get_breaker, resilient_invoke
from codegenx.ai_service.utils.config import AgentConfig, config
from codegenx.ai_service.system_app import get_app
from shared import log
from codegenx.ai_service.agent.agents.agent import Agent, AgentContext, AgentMessage

from codegenx.ai_service.agent.action import Action, ActOutput
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
            role=cls.profile.role,
            name=cls.profile.name,
            desc=cls.profile.desc,
            cls=cls,
        )
        registry.register_spec(spec)


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

    async def receive(self, msg:AgentMessage,session_state: RuntimeSessionState) -> AgentMessage | None:
        """接收来自其他 agent 或外部的消息，执行 run() 并转发下一跳。

        注意：全局消息路由已收归 AutoPlanManager，此处仅为独立测试或手动调用保留。
        """
        log.info(
            "agent 消息接收: {} <- {} | request_id={}",
            self.agent_name, msg.sender, msg.request_id,
        )
        reply = await self.run(msg, session_state)
        if reply:
            if reply.recipient == "user":
                get_app().message_bus.publish_runtime_event(
                    session_state.publish_context,
                    event_type=AgentEventType.REQUEST_COMPLETED,
                    data={"content": reply.message},
                    state=AgentState.COMPLETED,
                )
            else:
                await self.send(reply, session_state)
        return msg

    async def send(
        self,
        msg:AgentMessage,
            session_state:RuntimeSessionState,
        **kwargs,
    ) -> bool:
        """向指定 name 的 agent 发送消息并触发其 receive → run 链路。

        从 AgentRegistry 取 AgentSpec.cls() 实例化目标 agent，再调 receive。
        """
        receiver = msg.recipient
        if not receiver:
            raise ValueError(f"未设置接收方：{msg}")

        spec = get_app().agents.get(receiver)
        if spec is None or spec.cls is None:
            raise ValueError(f"目标 agent 未注册或无 cls: {receiver}")

        target_agent = spec.cls()
        await target_agent.receive(msg, session_state, **kwargs)
        log.info(
            "agent 消息发送: {} -> {} | request_id={}",
            msg.sender, msg.recipient, msg.request_id,
        )
        return True

    # ------------------------------------------------------------------ ReAct lifecycle

    @staticmethod
    def _extract_task_text(msg: AgentMessage | None) -> str:
        """从接收消息提取任务文本。唯一来源为 receive()，无 session_state.request 兜底——
        agent 不再"双源"取数，未收到消息即为空任务（verify 阶段判失败）。
        """
        if msg is None:
            return ""
        text = msg.message.get("task","") or ""
        return text



    async def think(self, messages: list[dict[str, Any]]) -> dict[Any, Any]:
        """思考：调用 LLM 推理，返回 {content, tool_calls}（子类可覆盖定制提示词/工具策略）。"""
        llm_response = await resilient_invoke(
            messages=messages,
            chain = [self.model_name],
            label="agent",
            tools=self.tools or None,
            temperature=self.agent_config.temperature,
            max_tokens=self.agent_config.max_tokens,
        )


        return llm_response

    async def act(self, llm_response: dict[str, Any], session_state: RuntimeSessionState,
                  **kwargs) -> ActOutput:
        """行动：遍历 self.actions 链式执行，上一环的 ActOutput 作为下一环的输入。

        - 链路首环输入由 think() 原始返回封装（ActOutput(success=True, output=llm_response)），
          之后各环输入即上一环的返回，类型全程统一为 ActOutput。
        - 任一 Action 返回 success=False 时立即中断，后续 Action 不再执行，
          并整体返回该失败的 ActOutput（由 run 交给调用方判断）。
        """
        if not self.actions:
            log.warning(f"Agent {self.agent_name} 未设置action，返回原始llm response")
            return ActOutput(success=True, output=llm_response,message=f"Agent {self.agent_name} 未设置action，返回原始llm response")

        last_result = ActOutput(success=True, output=llm_response)
        for action in self.actions:
            raise_if_stop_signal(session_state)
            last_result = await action.act(
                ai_message=last_result, session_state=session_state, **kwargs
            )
            log.info("agent act: {} action={} success={}", self.agent_name, type(action).__name__, last_result.success)
            if not last_result.success:
                log.warning("agent act: {} action={} 执行失败，中断动作链: {}",
                            self.agent_name, type(action).__name__, last_result.message)
                
                break
        return last_result

    async def observation(self, msg: AgentMessage, act_output: ActOutput,llm_response:dict[Any, Any]) -> AgentMessage:
        pass


    async def verify(self, observation:AgentMessage) -> tuple[bool, str]:
        """验证：检查最终结果是否可用（子类可覆盖定制校验规则）。返回 (ok, reason)。"""
        if (observation.next is None
                or observation.next.next_agent is None
                or observation.next.metadata is None):
            return False ,f"{self.agent_name} 执行失败没有定义next/next_agent/next_message"
        return True, ""

    async def run(self, agent_message:AgentMessage,session_state: RuntimeSessionState) -> AgentMessage:
        """ReAct 方式执行：think → act 循环 → verify。

        核心流程仅保留消息收发、LLM 推理与工具执行，全部过程记录到日志；
        监控、中间消息 publish、hook、记忆、存储、上下文管理等后续接入。

        参数:
            session_state: 运行时会话状态，携带 request/session/user 等标识。
        """
        # 1. 准备 agent 上下文（inbox 已在 __init__ 气注，此处仅组装 prompt/工具）
        await self._prepare_agent(session_state)
        log.debug("──────────【{}】 STATR───────────── ",self.agent_name)
        log.debug("接收到消息:{}", agent_message.model_dump())
        # 2.
        request_id = (agent_message.request_id if agent_message else "") or session_state.request_id
        task_text = self._extract_task_text(agent_message)
        log.info("agent run 开始: {} | request_id={} | task_len={}", self.agent_name, request_id, len(task_text))

        # 3. 组装本地对话消息（仅内存，不落库；提升为实例属性，便于 observation 追加 act 结果）
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task_text},
        ]

        # 4. ReAct 循环：think → act，直到无工具调用或达到 max_steps
        step = 0
        final_text = ""
        act_output: ActOutput | None = None
        observation: AgentMessage = AgentMessage()
        while step < self.max_steps:
            raise_if_stop_signal(session_state)
            step += 1
            log.info("agent run: {} step {}/{} think 开始", self.agent_name, step, self.max_steps)
            response = await self.think(self._messages)
            if not response:
                self._messages.append({"role": "assistant", "content": self.profile.retry_goal})
                continue
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
            self._messages.append(assistant_message)

            raise_if_stop_signal(session_state)
            # act：链式执行 actions，失败（success=False）则中断本轮并结束运行
            act_output = await self.act(response, session_state=session_state)

            observation:AgentMessage = await self.observation(agent_message, act_output,response)

            if observation.success:
                # observation 判定可结束：退出 ReAct 循环
                log.info("agent run: {} observation 判定完成，退出循环", self.agent_name)
                break
            # observation.success=False 表示需要继续 think：
            #   - act 成功但还要进一步推理（如 SQL 执行完要总结给用户）
            #   - act 失败需要修正重试（如 SQL 语法错、字段不存在）
            log.info("agent run: {} observation 要求继续 think", self.agent_name)

        else:
            log.warning("agent run: {} 达到 max_steps={}，强制结束", self.agent_name, self.max_steps)

        # 5. verify：observation的结果
        ok, reason = await self.verify(observation)
        log.info("agent run 结束: {} | request_id={} | steps={} | ok={} | reason={}",
                 self.agent_name, request_id, step, ok, reason)

        # 6. 返回结果 ,下一个AgentMessage
        if not ok:
            next_agent = "user"
            next_message = "执行失败，请稍后重试"
        else:
            next_agent = observation.next.next_agent
            next_message = observation.next.message
        log.debug("──────────【{}】 END ───────────── ", self.agent_name)
        return AgentMessage(
            sender=self.agent_name,
            recipient=next_agent,
            is_user_message=False,
            message={
                "task": next_message,
                "metadata": observation.next.metadata if observation.next else {}
            },
            request_id=request_id,
            session_id=session_state.session_id,
            summary=next_message[:200],
        )

