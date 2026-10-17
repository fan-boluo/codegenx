from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, List
from codegenx.ai_service.agent.agent_schema import AgentState,AgentEventType
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState, raise_if_stop_signal
from codegenx.ai_service.llm.async_client import get_llm
from codegenx.ai_service.llm.resilience import get_breaker, resilient_invoke
from codegenx.ai_service.utils.config import AgentConfig, config
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.agent.tool_handler import get_tool_registry
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
        # agent 间寻址标识与收件箱（agent_name 全局唯一）
        self.agent_name = self.agent_config.name
        self._inbox: asyncio.Queue | None = None
        # 保留子类定义的 actions（类属性，如 SupervisorAgent.actions = [SupervisorAction()]）；
        # 仅未设置时兜底为空链——无条件赋值会遮蔽类属性，导致 Action 链静默失效
        if not getattr(self, "actions", None):
            self.actions: List[Action] = []
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


    def _resolve_tool_catalog(self) -> list[dict]:
        """按 config.json agents[].tools 白名单解析 function-calling 工具目录。

        白名单为空/未配置时不启用工具（supervisor/planner 等纯文本决策链路保持不变）；
        解析失败（如独立调试场景无 SystemApp）时降级为无工具，不阻断 agent 构建。
        """
        allowlist = self.agent_config.tools or []
        if not allowlist:
            return []
        try:
            return get_tool_registry().build_agent_tools(allowlist)
        except Exception as exc:
            log.warning("{} 工具目录解析失败，降级为无工具: {}", self.agent_name, exc)
            return []

    def _resolve_final_target(self, msg: AgentMessage) -> str:
        """最终答复目标（直答/回传路由，工具化执行型 agent 通用判据）。

        - 派发消息 direct_reply=True（supervisor 单任务免汇总派发）→ 直答用户；
        - 但本轮调用过工具且全部失败 → 降级回传调用方（supervisor 审查后重派或如实说明）；
        - 其余情况回传调用方。
        """
        caller = msg.sender or "user"
        if not msg.direct_reply:
            return caller
        if self._tool_seen and not self._tool_any_success:
            log.warning("{}: 派发标记直答但本轮工具全部失败，降级回传 {}", self.agent_name, caller)
            return caller
        return "user"

    def _reply(self, msg: AgentMessage, target: str, result_text: str) -> AgentMessage:
        """把入参消息改造为回传/直答跳（执行型 agent observation 的统一出口）。

        - task 保留入参原值（追溯），result 承载结果文本；
        - 回传调用方时原样保留 task_id（看板闭环握手）；直答用户时清除握手字段；
        - 清除 direct_reply 派发标记（标记仅派发跳有效）。
        """
        msg.sender = self.agent_name
        msg.recipient = target
        msg.result = result_text
        if target == "user":
            msg.task_id = None
            msg.step_id = None
        msg.direct_reply = False
        msg.success = True
        return msg

    async def _prepare_agent(
        self,
        session_state: RuntimeSessionState,
    ) -> None:
        """准备 agent 所需的上下文与收件箱（子类可覆盖扩展，需先调 super()）。"""
        # self._ensure_inbox()
        # 工具目录与执行器：config.json agents[].tools 白名单非空才启用 function calling
        self.tools = self._resolve_tool_catalog()
        self.tool_executor = None
        if self.tools:
            # 配置了工具的 agent 必须拿到执行器，失败直接暴露（fail fast，不静默吞掉）
            self.tool_executor = get_app().tools_executor
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
                    data={"content": reply.result},
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
        return msg.task or ""



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
        log.debug("{},llm_response:{}", self.agent_name, self._trim_response_for_log(llm_response))

        return llm_response

    @staticmethod
    def _trim_response_for_log(llm_response: dict[Any, Any]) -> Any:
        """日志瘦身：reasoning_content 动辄数千 token（曾出现 6k+ token 的审查思考），
        截断展示避免刷屏；content/tool_calls 全量保留。"""
        if not isinstance(llm_response, dict):
            return llm_response
        trimmed = dict(llm_response)
        rc = trimmed.get("reasoning_content")
        if isinstance(rc, str) and len(rc) > 1500:
            trimmed["reasoning_content"] = f"{rc[:1500]}...(truncated, total {len(rc)} chars)"
        return trimmed

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
            log.info("【{} ACT】:   action={} output={}", self.agent_name, type(action).__name__, last_result.model_dump())
            if not last_result.success:
                log.warning("【{} ACT】: action={} 执行失败，中断动作链: {}",
                            self.agent_name, type(action).__name__, last_result.message)

                break
        return last_result

    async def observation(self, msg: AgentMessage, act_output: ActOutput,llm_response:dict[Any, Any]) -> AgentMessage:
        pass


    async def verify(self, observation:AgentMessage) -> tuple[bool, str]:
        """验证：observation 是否产出有效下一跳（子类可覆盖定制校验规则）。返回 (ok, reason)。"""
        if observation.success is not True:
            return False, f"{self.agent_name} 未产出成功的 observation（max_steps 耗尽或持续失败）"
        if not observation.recipient:
            return False, f"{self.agent_name} observation 未指定下一跳 recipient"
        return True, ""

    def _build_history_messages(self, session_state: RuntimeSessionState) -> list[dict[str, Any]]:
        """注入会话历史消息（默认为空，子类按需覆盖）。

        执行型 agent（data_explore/csv_explore）任务自包含，无需历史；
        编排型 agent（Supervisor）覆盖此方法注入 context_manager.chat_messages
        真相源，获得跨轮对话与子 agent 执行历史，用于意图理解与结果审查。
        """
        return []

    @staticmethod
    def _normalize_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
        """归一化 tool_call 为扁平结构 {id, name, arguments(dict)}。

        兼容两种来源：非流式 message.to_dict() 的 OpenAI 原生嵌套格式
        （{id, type, function: {name, arguments(字符串)}}）与流式 _safe_build_tool_calls
        的扁平格式（{id, name, arguments(dict)}）。
        """
        fn = tc.get("function") or {}
        name = str(fn.get("name") or tc.get("name") or "")
        raw_args = fn.get("arguments", tc.get("arguments"))
        # arguments 可能是 JSON 字符串（OpenAI 原生）或 dict（流式），统一解析为 dict
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw_arguments": raw_args}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        return {"id": str(tc.get("id") or ""), "name": name, "arguments": args}

    async def run(self, agent_message:AgentMessage,session_state: RuntimeSessionState) -> AgentMessage:
        """ReAct 方式执行：think → act 循环 → verify。

        核心流程仅保留消息收发、LLM 推理与工具执行，全部过程记录到日志；
        监控、中间消息 publish、hook、记忆、存储、上下文管理等后续接入。

        参数:
            session_state: 运行时会话状态，携带 request/session/user 等标识。
        """
        # 1. 准备 agent 上下文（inbox 已在 __init__ 气注，此处仅组装 prompt/工具）
        await self._prepare_agent(session_state)
        log.debug("+++++++++++++++++++【{}】 STATR +++++++++++++++++++ ",self.agent_name)
        log.debug("接收到消息:{}", agent_message.model_dump())
        # 2.
        request_id = (agent_message.request_id if agent_message else "") or session_state.request_id
        task_text = self._extract_task_text(agent_message)
        log.info("agent run 开始: {} | request_id={} | task_len={}", self.agent_name, request_id, len(task_text))

        # 3. 组装本地对话消息（仅内存，不落库；提升为实例属性，便于 observation 追加 act 结果）
        # 历史消息由 _build_history_messages 钩子注入（默认空；Supervisor 注入会话历史）
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            *self._build_history_messages(session_state),
            {"role": "user", "content": task_text},
        ]

        # 4. ReAct 循环：think →（有 tool_calls 则执行工具回填后继续 think）→
        #    无工具调用且内容非空时走 Action 链（兼容 supervisor 路由解析）+ observation 定跳
        step = 0
        final_text = ""
        act_output: ActOutput | None = None
        # 下一跳消息：observation 成功时为改造后的入参消息；未成功时保持空信封（verify 兜底终答）
        observation: AgentMessage = AgentMessage(
            sender=self.agent_name, recipient="",
            request_id=request_id, session_id=session_state.session_id,
        )
        # 直答降级记账：本轮是否有工具调用、是否有任一成功（observation 直答判据用）
        self._tool_seen = False
        self._tool_any_success = False
        while step < self.max_steps:
            raise_if_stop_signal(session_state)
            step += 1
            log.info("【{} Think】:  step {}/{} START", self.agent_name, step, self.max_steps)
            log.debug("{} 传入llm的chat_message,system_prompt除外：\n{}",self.agent_name, self._messages[1:])
            response = await self.think(self._messages)
            if not response:  # TODO reasoning_content 的解析
                self._messages.append({"role": "assistant", "content": self.profile.retry_goal})
                continue
            final_text = str(response.get("content") or "")
            # tool_calls 归一化为扁平结构（兼容非流式嵌套格式与流式扁平格式）
            tool_calls = [self._normalize_tool_call(tc) for tc in response.get("tool_calls") or []]
            log.info("【{} Think】:  step {}/{} END | content_len={} tool_calls={}",
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

            if tool_calls:
                # 工具化分支：执行 tool_calls 并回填 tool 消息，继续 think（LLM 自主决定下一步）
                if self.tool_executor is None:
                    log.error("【{} THINK】返回了 tool_calls 但工具执行器未就绪，强制结束", self.agent_name)
                    break
                tool_messages = await self.tool_executor.dispatch_tool_calls(tool_calls, session_state)
                # _success 为内部记账字段（直答降级判据），回填对话前剔除，不进 LLM API
                self._tool_seen = True
                for m in tool_messages:
                    if m.pop("_success", True):
                        self._tool_any_success = True
                self._messages.extend(tool_messages)
                log.info("【{} ACT】: 执行 {} 个工具并回填 tool 消息", self.agent_name, len(tool_messages))
                continue

            if not final_text.strip():
                # 空回复兜底
                self._messages.append({
                    "role": "user",
                    "content": "回复为空，请基于任务描述与现有信息给出答复。",
                })
                continue

            raise_if_stop_signal(session_state)
            # 无工具调用 → Action 链兼容层（supervisor 路由解析），随后 observation 定跳
            act_output = ActOutput(success=True, output=response)
            if self.actions:
                act_output = await self.act(response, session_state=session_state)
            observation:AgentMessage = await self.observation(agent_message, act_output,response)

            if observation.success:
                # observation 判定可结束：退出 ReAct 循环
                log.info("【{} OBSERVATION】: 判定完成，退出循环", self.agent_name)
                break
            # observation.success=False 表示需要继续 think
            #（如 supervisor 的 Action 解析失败，或 observation 判定还需进一步处理）
            log.info("【{} OBSERVATION】: 未完成，继续 think", self.agent_name)

        else:
            log.warning("【{} 】: 达到 max_steps={}，强制结束", self.agent_name, self.max_steps)

        # 5. verify：observation 是否产出有效下一跳
        ok, reason = await self.verify(observation)
        # 6. 返回下一跳消息：observation 已把入参消息改造为下一跳，直接返回；
        #    verify 失败（max_steps 耗尽/无效跳）时兜底改写为终答跳
        if not ok:
            observation.sender = self.agent_name
            observation.recipient = "user"
            observation.result = "执行失败，请稍后重试"
            observation.task_id = None
            observation.step_id = None
            observation.direct_reply = False
        log.info("【{} 】结束: {} | steps={} | ok={} | reason={}", self.agent_name, request_id, step, ok, reason)
        log.debug("+++++++++++++++++++【{}】 END +++++++++++++++++++ ", self.agent_name)

        return observation

