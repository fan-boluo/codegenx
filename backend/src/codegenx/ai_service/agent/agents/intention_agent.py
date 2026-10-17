"""
SupervisorAgent —— 多轮编排监管者（编排入口，双模式）

模式一：自主编排模式（mode="direct"，默认）
  supervisor 自己 react：路由/审查 think → CALL_EXECUTOR 派发执行智能体
  → 收结果 → 再 think 决定下一个执行智能体 → ... → FINISH 总结。
  每次派发前在看板登记任务（线性依赖上一个任务）。

模式二：planner-executor 模式（mode="planner_executor"）
  路由 think 决定 CALL_PLANNER → planner 产出计划 → 计划落任务看板
  （TaskBoardService：step → TaskRecord，executor_agent=执行智能体，depends_on=blocked_by DAG）
  → 就绪任务确定性派发（免 think）→ 执行智能体回传 → 看板标记 completed
  （自动解锁下游）→ 派发下一个就绪任务 → 无可派发任务才 think 审查（汇总/僵局决策）。

职责分层（自原单类实现拆分）：
- SupervisorAgent（本文件）：编排流程——run 三阶段（结果/计划消费短路 →
  think 决策 → observation 组装下一跳）+ 会话历史注入 + route/review 模板切换
- SupervisorDecisionEngine（supervisor_decision.py）：决策协议（动作模型/解析）与双模式模板
- TaskBoardCoordinator（supervisor_board.py）：看板/编排状态的全部确定性操作

跨 run 编排上下文 SessionContext.orchestration（turn 级，请求开始重置）：
  mode / plan / step_task_map(step_id→task_id) / turn_task_ids / last_task_id / execution_trace
（会话历史由 chat_messages 真相源承载；方案《上下文工程设计方案2》）
"""

from typing import Any

from codegenx.ai_service.agent.action import ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.agents.supervisor_board import TaskBoardCoordinator
from codegenx.ai_service.agent.agents.supervisor_decision import (
    SupervisorAction,
    SupervisorActionType,
    SupervisorDecisionEngine,
)
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from shared import log


class SupervisorAgent(BaseReActAgent):
    """多轮编排监管者：自主编排（react） / planner-executor（计划确定性派发）双模式。"""
    name = "supervisor"
    # 类属性：register() 经 cls.profile 构建注册规格
    actions = [SupervisorAction()]
    profile: PromptConfig = SupervisorDecisionEngine.route_profile

    def __init__(self):
        super().__init__()
        # 协作组件：决策引擎（双模式模板）+ 看板协调器（看板/编排状态确定性操作）
        self.decision = SupervisorDecisionEngine()
        self.board = TaskBoardCoordinator()
        # run 级状态（实例即建即弃，不落会话状态）
        self._session_state: RuntimeSessionState | None = None
        self._is_review: bool = False

    # ── run：三阶段编排（消费短路 → think 决策 → observation 组装下一跳） ──

    async def run(self, agent_message: AgentMessage, session_state: RuntimeSessionState) -> AgentMessage:
        caller = agent_message.sender or "user"
        # 供 observation/_prepare_prompt_params 等 run 内环节使用（实例即建即弃，不落会话状态）
        self._session_state = session_state
        # _is_review 参数化：run 内由 caller 判断（实例即建即弃，不落会话状态）
        self._is_review = (caller != "user")

        # 阶段1：结果/计划消费短路（免 think，委托看板协调器）
        if caller == "user":
            # 新请求：重置编排上下文（turn 级）+ 刷新看板视图，默认自主编排模式
            self.board.reset_turn(session_state)
        elif caller == "planner":
            # planner-executor 模式：计划落看板后，就绪任务确定性派发（免 think）
            self.board.consume_planner_plan(agent_message, session_state)
            dispatched = self.board.dispatch_next_ready(session_state, agent_message)
            if dispatched is not None:
                return dispatched
            # 计划为空 / 全部自执行 / 解析失败 → 进入 think 审查兜底
        else:
            # 执行智能体回传：执行轨迹 + 看板任务标记 completed（自动解锁下游）
            self.board.consume_child_result(agent_message, session_state)
            orch = self.board.orchestration(session_state)
            if orch is not None and orch.mode == "planner_executor":
                # 有计划：就绪任务直接派发（免 think）；无可派发任务才 think 审查（汇总/僵局决策）
                dispatched = self.board.dispatch_next_ready(session_state, agent_message)
                if dispatched is not None:
                    return dispatched
            # 自主编排模式：think 审查（react 决定下一个执行智能体或总结）

        orch = self.board.orchestration(session_state)
        log.info("Supervisor run [{}模式] | from={} | mode={} | request_id={}",
                 "审查" if self._is_review else "路由", caller,
                 orch.mode if orch is not None else "?", agent_message.request_id)

        # 阶段2+3：think 决策（route/review 模板）→ observation 组装下一跳（基类 ReAct 循环）
        return await super().run(agent_message, session_state)

    # ── prompt 构建：route/review 双模板切换 ──

    def _prepare_prompt_params(self) -> dict:
        """返回当前模式下需要的 prompt 变量（智能体清单 / 任务看板）。"""
        is_review = getattr(self, "_is_review", False)
        params = {
            "agents_list_desc": self.decision.agents_list_desc(include_planner=not is_review),
        }
        if is_review:
            # 执行历史不走 system prompt，由 _build_history_messages 注入的对话历史消息承载
            params["board_desc"] = self.board.render_board(self._session_state)
        return params

    def _extract_task_text(self, msg: AgentMessage | None) -> str:
        """审查模式把当前输入改写为「审查最新回传结果」框架。

        修复：审查模式下 base run 的 user 消息原为 msg.task（派发任务原文），
        而 chat_messages 末条（子 agent 回传）又被历史注入去除，导致最新回传
        结果对 LLM 完全不可见（llm 思考判定「result is missing」重复派发同任务）。

        - 路由模式：维持原始用户请求（msg.task）；
        - 审查模式：msg.result 承载最新回传（执行 agent=结果答复 / planner=计划
          JSON），以审查框架呈现（最高显著性，避免派发原文被误读为新请求）；
          result 为空时回落任务文本。
        """
        if msg is None:
            return ""
        if not getattr(self, "_is_review", False):
            return msg.task or ""
        result = str(msg.result or "").strip()
        if not result:
            return msg.task or ""
        return (
            f"子智能体 {msg.sender} 回传了任务「{msg.task}」的执行结果：\n{result}\n\n"
            "请审查该结果并决定下一步。"
        )

    def _build_history_messages(self, session_state) -> list[dict]:
        """Supervisor 注入会话历史（chat_messages 真相源，跨 turn 连续）。

        两种模式均去除末条（内容已由 _extract_task_text 改写进当前输入，避免重复）：
        - route 模式：末条为本轮用户请求（add_user_message）= 当前 task_text 原文；
        - 审查模式：末条为最新子 agent 回传（add_agent_result），已由 _extract_task_text
          以审查框架呈现；其余回传与历史轮保留在历史消息中；
        - 只保留 role/content 供 LLM 对话使用，附带字段（agent/task/success）丢弃。
        """
        cm = getattr(session_state, "context_manager", None)
        if cm is None or not cm.chat_messages:
            return []
        return [
            {"role": m["role"], "content": m["content"]}
            for m in cm.chat_messages[:-1]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

    async def _prepare_agent(self, session_state):
        """基类已经通过 profile.build_default_system_prompt 构建了 system_prompt；
        这里如果是审查模式，用 review_profile 覆盖一次。"""
        await super()._prepare_agent(session_state)
        if getattr(self, "_is_review", False):
            params = self._prepare_prompt_params()
            self._system_prompt = self.decision.review_profile.build_default_system_prompt(**params)
        log.debug(self._system_prompt)

    # ── observation：决策分发 → 把入参消息改造为下一跳 ──

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """根据 SupervisorAction 决策，把入参消息改造为下一跳。

        决策失败兜底终答；DIRECT_ANSWER/FINISH 终答；CALL_PLANNER 进计划模式；
        CALL_EXECUTOR/NEXT_STEP 统一走派发。
        """
        parsed = act_output.output or {}
        action = parsed.get("action", "")

        if not act_output.success or not action:
            log.warning("Supervisor observation: 决策失败，兜底返回用户")
            return self._final_answer(msg, f"监管者决策失败：{act_output.message}")
        if action in (SupervisorActionType.DIRECT_ANSWER.value, SupervisorActionType.FINISH.value):
            return self._final_answer(msg, parsed.get("answer") or "任务已完成。")
        if action == SupervisorActionType.CALL_PLANNER.value:
            return self._route_to_planner(msg, parsed)
        # CALL_EXECUTOR / NEXT_STEP 统一派发入口
        return self._dispatch_executor(msg, parsed)

    def _final_answer(self, msg: AgentMessage, answer: str) -> AgentMessage:
        """终答跳：面向用户的答复，不携带看板握手字段。"""
        msg.sender = self.agent_name
        msg.recipient = "user"
        msg.result = answer
        msg.task_id = None
        msg.step_id = None
        msg.direct_reply = False
        msg.success = True
        return msg

    def _route_to_planner(self, msg: AgentMessage, parsed: dict) -> AgentMessage:
        """进 planner-executor 模式：重置编排上下文，task 强制为用户原始请求
        （supervisor 不给编排建议，计划由 planner 自行制定并落任务看板）。"""
        session_state = self._session_state
        orch = self.board.orchestration(session_state) if session_state is not None else None
        if orch is not None:
            orch.mode = "planner_executor"
            orch.plan = None
            orch.step_task_map = {}
            orch.turn_task_ids = []
        request_msg = str(getattr(getattr(session_state, "request", None), "message", "") or "")
        task = request_msg or (parsed.get("task") or "").strip() or msg.task
        msg.sender = self.agent_name
        msg.recipient = "planner"
        msg.task = task
        msg.result = ""
        msg.task_id = None
        msg.step_id = None
        msg.direct_reply = False
        msg.success = True
        return msg

    def _dispatch_executor(self, msg: AgentMessage, parsed: dict) -> AgentMessage:
        """派发跳：CALL_EXECUTOR（自主编排）/ NEXT_STEP（计划僵局逃生）统一入口。

        - NEXT_STEP 先从看板取该 agent 的待办任务，取到则免登记直接派发看板任务；
          未取到按自主派发处理（登记新看板任务）；
        - CALL_EXECUTOR 登记新看板任务后派发（direct_reply 随决策下传）；
        - task_id 覆盖为新登记任务的 id（防同 agent 再派发时旧值串号）。
        """
        action = parsed.get("action", "")
        agent = (parsed.get("agent") or "").strip()
        task = (parsed.get("task") or "").strip()
        # 容错：executor 分支误填 planner 时按进入计划模式处理
        if action == SupervisorActionType.CALL_EXECUTE_AGENT.value and agent == "planner":
            return self._route_to_planner(msg, parsed)
        if not agent:
            return self._final_answer(msg, "监管者路由失败：未指定子智能体")
        if agent in (self.agent_name, "supervisor"):
            # 禁止派发自身（汇总类任务由审查环节自行完成）：兜底转 FINISH
            log.warning("Supervisor observation: 决策试图派发自身，按 FINISH 兜底")
            return self._final_answer(msg, parsed.get("answer") or "任务已完成。")

        session_state = self._session_state
        if action == SupervisorActionType.NEXT_STEP.value:
            # 计划僵局逃生：按 agent 从看板强制取待办任务，取到则免登记直接派发
            picked = self.board.pick_task_for_agent(agent, session_state)
            if picked is not None:
                return self.board.dispatch_board_task(session_state, msg, picked)
            # 看板无该 agent 的待办任务 → 按自主派发处理（登记新看板任务）

        # CALL_EXECUTOR / NEXT_STEP 兜底：登记新看板任务（前端可见）后派发
        task_text = task or msg.task or ""
        record = self.board.create_dispatch_task(session_state, agent, task_text[:200])
        msg.sender = self.agent_name
        msg.recipient = agent
        msg.task = task_text
        msg.result = ""
        msg.task_id = record.id if record is not None else None
        msg.step_id = None
        msg.direct_reply = (
            bool(parsed.get("direct_reply", False))
            if action == SupervisorActionType.CALL_EXECUTE_AGENT.value else False
        )
        msg.success = True
        return msg
