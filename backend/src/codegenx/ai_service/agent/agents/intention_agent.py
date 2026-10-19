"""
SupervisorAgent —— 多轮编排监管者（编排入口，双模式，决策工具化，单一模板）

模式一：自主编排模式（mode="direct"，默认）
  supervisor 路由/审查 think 经 function-calling 决策：
  dispatch_agent 派发执行智能体（同轮多次调用 = 批量并行）
  → 收结果 → 再决策 → ... → 不调用工具直接输出汇总答复（终答）。

模式二：planner-executor 模式（mode="planner_executor"）
  路由决策 request_plan → planner 产出计划 → 计划落任务看板
  （TaskBoardService：step → TaskRecord，executor_agent=执行智能体，depends_on=blocked_by DAG）
  → 就绪任务确定性派发（免 think）→ 执行智能体回传 → 看板标记 completed
  （自动解锁下游）→ 派发下一个就绪任务 → 无可派发任务才 think 审查
  （dispatch_agent 优先派发看板待办 = 僵局逃生，原 NEXT_STEP 语义并入）。

决策协议（P2 工具化，替代原 JSON 文本协议）：
  - dispatch_agent(agent, task, direct_reply?)：本地登记看板 + 累积待派发跳；
  - request_plan(task)：登记计划请求（observation 路由 planner）；
  - 不调用工具且输出非空文本 = 终答（direct_answer / finish 合一）。
  决策工具为控制流工具，在 _execute_tool_calls 本地执行（不走通用 ToolExecutor），
  observation 按优先级消费：计划请求 > 待派发 > 终答。

P3 模板合一：route/review 双模板与 _is_review 模式标志已删除。路由与审查共用
单一 profile（SupervisorDecisionEngine.profile），区分由输入侧承载——
_extract_task_text 按 msg.sender 分支组装本轮输入（user=请求原文 /
batch=批审查框架 / 执行智能体=单任务审查框架），_build_history_messages
注入 chat_messages[:-1]（末条内容已由本轮输入承载）。

职责分层（自原单类实现拆分）：
- SupervisorAgent（本文件）：编排流程——run 三阶段（结果/计划消费短路 →
  think 决策 → observation 组装下一跳）+ 输入组装 + 会话历史注入
- SupervisorDecisionEngine（supervisor_decision.py）：统一模板与智能体清单
- TaskBoardCoordinator（supervisor_board.py)：看板/编排状态的全部确定性操作

跨 run 编排上下文 SessionContext.orchestration（turn 级，请求开始重置）：
  mode / plan / step_task_map(step_id→task_id) / turn_task_ids / last_task_id / execution_trace
（会话历史由 chat_messages 真相源承载；方案《上下文工程设计方案2》）
"""

from typing import Any

from codegenx.ai_service.agent.action import ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.agents.supervisor_board import TaskBoardCoordinator
from codegenx.ai_service.agent.agents.supervisor_decision import SupervisorDecisionEngine
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.task.task_manager import TaskStatus
from codegenx.ai_service.system_app import get_app
from shared import log

# supervisor 决策工具名（tools/supervisor.py 描述符；_execute_tool_calls 本地拦截执行）
DECISION_TOOL_DISPATCH = "dispatch_agent"
DECISION_TOOL_PLAN = "request_plan"


class SupervisorAgent(BaseReActAgent):
    """多轮编排监管者：自主编排（react） / planner-executor（计划确定性派发）双模式。"""
    name = "supervisor"
    # 类属性：register() 经 cls.profile 构建注册规格
    #（actions 为空：决策走 tool_calls 分支，JSON Action 链已随工具化退役；
    #  P3 模板合一：路由/审查共用单一 profile，区分由输入侧 sender 分支承载）
    profile: PromptConfig = SupervisorDecisionEngine.profile

    def __init__(self):
        super().__init__()
        # 协作组件：决策引擎（统一模板）+ 看板协调器（看板/编排状态确定性操作）
        self.decision = SupervisorDecisionEngine()
        self.board = TaskBoardCoordinator()
        # run 级状态（实例即建即弃，不落会话状态）
        self._session_state: RuntimeSessionState | None = None
        # 决策工具的 run 级累积状态：待派发跳（observation 组装派发/批信封）与计划请求
        self._pending_dispatches: list[AgentMessage] = []
        self._plan_request: str | None = None

    # ── run：三阶段编排（消费短路 → think 决策 → observation 组装下一跳） ──

    async def run(self, agent_message: AgentMessage, session_state: RuntimeSessionState) -> AgentMessage:
        caller = agent_message.sender or "user"
        # 供 observation/_prepare_prompt_params 等 run 内环节使用（实例即建即弃，不落会话状态）
        self._session_state = session_state
        # 决策工具累积状态重置（防实例复用时残留上一轮派发）
        self._pending_dispatches = []
        self._plan_request = None

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
        elif caller == "batch":
            # 批量派发回灌：子任务结果已由调度器逐个消费（看板闭环 + 入史），
            # 不再重复 consume，直接进入 think 审查（汇总 / 派发下一批）
            pass
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
        log.info("Supervisor run [{}] | from={} | mode={} | request_id={}",
                 "审查" if caller != "user" else "路由", caller,
                 orch.mode if orch is not None else "?", agent_message.request_id)

        # 阶段2+3：think 决策（单一模板，场景差异由本轮输入承载）→ observation 组装下一跳（基类 ReAct 循环）
        return await super().run(agent_message, session_state)

    # ── prompt 构建与输入组装（P3 模板合一：无模式切换） ──

    def _prepare_prompt_params(self) -> dict:
        """统一 prompt 变量：智能体清单（含 planner）+ 任务看板（路由时通常为空看板）。

        执行历史不走 system prompt，由 _build_history_messages 注入的对话历史消息承载。
        """
        return {
            "agents_list_desc": self.decision.agents_list_desc(),
            "board_desc": self.board.render_board(self._session_state),
        }

    def _extract_task_text(self, msg: AgentMessage | None) -> str:
        """按入参 sender 组装本轮输入文本（模板合一后唯一的输入分支点）。

        背景：审查模式下 base run 的 user 消息若为 msg.task（派发任务原文），
        而 chat_messages 末条（子 agent 回传）又被历史注入去除，最新回传结果对
        LLM 完全不可见（曾致其判定「result is missing」重复派发同任务）——
        因此回传结果必须以审查框架呈现为本轮输入（最高显著性）。

        - sender == user（或空，新请求）→ 用户请求原文（msg.task）；
        - sender == batch（并行批回灌）→ 批审查框架（result 承载全部子任务结果摘要）；
        - 其它（执行智能体/planner 回传）→ 单任务审查框架（result 承载最新回传，
          避免派发原文被误读为新请求）；result 为空时回落任务文本。

        配套 _build_history_messages 的 [:-1]：末条内容已由本轮输入承载，避免重复。
        """
        if msg is None:
            return ""
        sender = str(msg.sender or "")
        if sender == "batch":
            result = str(msg.result or "").strip()
            return (
                f"多个子智能体已并行执行完毕，各任务执行结果如下：\n{result}\n\n"
                "请审查这些结果并决定下一步（全部完成则汇总答复，仍有缺口则派发后续任务）。"
            )
        if sender and sender != "user":
            result = str(msg.result or "").strip()
            if not result:
                return msg.task or ""
            return (
                f"子智能体 {sender} 回传了任务「{msg.task}」的执行结果：\n{result}\n\n"
                "请审查该结果并决定下一步。"
            )
        return msg.task or ""

    def _build_history_messages(self, session_state) -> list[dict]:
        """Supervisor 注入会话历史（chat_messages 真相源，跨 turn 连续）。

        去除末条（内容已由 _extract_task_text 按 sender 分支承载进当前输入，避免重复）：
        - 路由（sender=user）：末条为本轮用户请求（add_user_message）= 当前输入原文；
        - 审查（sender=执行智能体/planner）：末条为最新回传（add_agent_result），
          已由当前输入以审查框架呈现；其余回传与历史轮保留在历史消息中；
        - 批回灌（sender=batch）：末条为批内最后一个子任务回传，其内容已含于
          批摘要（当前输入）中；
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

    # ── 决策工具本地执行（控制流工具，不走通用 ToolExecutor） ──

    async def _execute_tool_calls(
        self, tool_calls: list[dict[str, Any]], session_state: RuntimeSessionState,
    ) -> list[dict[str, Any]]:
        """supervisor 决策工具拦截：dispatch_agent / request_plan 本地执行
        （登记看板 + 累积待派发 / 记录计划请求），由 observation 消费组装下一跳；
        其余工具（配置混入时）仍走通用执行器。参数错误以 tool 消息回给 LLM 自纠。
        """
        messages: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []
        for tc in tool_calls or []:
            name = str(tc.get("name") or "")
            if name == DECISION_TOOL_DISPATCH:
                messages.append(self._exec_dispatch_agent(tc, session_state))
            elif name == DECISION_TOOL_PLAN:
                messages.append(self._exec_request_plan(tc, session_state))
            else:
                others.append(tc)
        if others:
            generic = await super()._execute_tool_calls(others, session_state)
            if generic is None:
                # 通用执行器未就绪：逐个回填错误说明（已执行的决策工具结果不丢弃）
                generic = [self._tool_result_msg(tc, "工具执行器未就绪") for tc in others]
            messages.extend(generic)
        return messages

    @staticmethod
    def _tool_result_msg(tc: dict[str, Any], content: str) -> dict[str, Any]:
        """构造回填对话的 tool 消息（与 tool_call 一一对应）。"""
        return {
            "role": "tool",
            "tool_call_id": str(tc.get("id") or ""),
            "name": str(tc.get("name") or ""),
            "content": content,
        }

    def _exec_dispatch_agent(self, tc: dict[str, Any], session_state) -> dict[str, Any]:
        """本地执行 dispatch_agent：校验目标 → 登记看板 → 累积待派发跳。

        - 校验失败返回错误说明（LLM 据此自纠：改参数或换工具）；
        - 计划模式优先派发该智能体的看板待办任务（僵局逃生，原 NEXT_STEP 语义并入）；
        - 自主编排登记新看板任务（in_progress）后累积；同轮多次调用由
          _build_dispatch_hop 组装为批信封并行执行。
        """
        args = tc.get("arguments") or {}
        agent = str(args.get("agent") or "").strip()
        task_text = str(args.get("task") or "").strip()
        direct_reply = bool(args.get("direct_reply", False))

        # 参数校验（错误消息引导 LLM 自纠）
        if self._plan_request is not None:
            return self._tool_result_msg(tc, "已登记计划请求（request_plan），不能再派发任务，请结束本轮决策")
        if not agent:
            return self._tool_result_msg(tc, "缺少 agent 参数：请填写执行智能体 name")
        if agent in ("planner", self.agent_name, "supervisor", "user"):
            return self._tool_result_msg(
                tc, f"不能派发给 {agent}：进计划模式请改用 request_plan，汇总由你直接输出")
        if get_app().agents.get(agent) is None:
            return self._tool_result_msg(tc, f"未注册的智能体：{agent}")
        if not task_text:
            return self._tool_result_msg(tc, "缺少 task 参数：任务描述必须完全自包含")

        # 计划模式僵局逃生：优先派发该智能体的看板待办任务（免登记；
        # pick 只取 pending 就绪任务，派发即标记 in_progress，状态机天然防重）
        orch = self.board.orchestration(session_state)
        if orch is not None and orch.mode == "planner_executor":
            picked = self.board.pick_task_for_agent(agent, session_state)
            if picked is not None:
                dispatch = self.board.dispatch_board_task(
                    session_state, str(getattr(session_state, "request_id", "") or ""), picked)
                self._pending_dispatches.append(dispatch)
                return self._tool_result_msg(
                    tc, f"已派发看板待办任务 #{picked.id} → {agent}：{picked.subject[:80]}")

        # 防重复守卫1（同 run）：本回复已登记过相同 (agent, task) → 拒绝重复登记
        # （qwen-flash 曾在登记确认后原样重复 tool_calls，导致同批 4 任务两两重复）
        for d in self._pending_dispatches:
            if d.recipient == agent and (d.task or "").strip() == task_text:
                return self._tool_result_msg(
                    tc, f"任务已在本回复中登记（→ {agent}：{task_text[:60]}），请勿重复调用；"
                        "全部任务登记完毕后，请直接输出简短说明文本结束本轮")
        # 防重复守卫2（同 request）：看板已有该智能体的相同任务（含已完成）→ 拒绝。
        # 已完成任务的执行结果已在对话历史中，重跑相同任务只会产生重复/矛盾结果
        dup = self.board.find_same_task(session_state, agent, task_text)
        if dup is not None:
            status_desc = "已完成" if dup.status is TaskStatus.COMPLETED else "已登记"
            return self._tool_result_msg(
                tc, f"该智能体本请求内已有{status_desc}的相同任务 #{dup.id}（→ {agent}：{dup.subject[:60]}），"
                    "其执行结果已在对话历史中；请基于已有结果直接汇总，或改写任务描述（新口径）后再派发")

        # 自主编排：登记看板任务（in_progress）并累积待派发跳
        record = self.board.create_dispatch_task(session_state, agent, task_text[:200])
        self._pending_dispatches.append(AgentMessage(
            sender=self.agent_name,
            recipient=agent,
            task=task_text,
            task_id=record.id if record is not None else None,
            direct_reply=direct_reply,
            request_id=str(getattr(session_state, "request_id", "") or ""),
            session_id=str(getattr(session_state, "session_id", "") or ""),
            success=True,
        ))
        # 登记确认必须带停止指引：告知累计数并明确「登记完毕即输出文本结束」，
        # 否则 qwen-flash 会把 tool 结果当作继续派发的信号（同 run 重复登记的根因之一）
        return self._tool_result_msg(
            tc, f"已登记任务 #{record.id if record is not None else '?'} → {agent}：{task_text[:80]}"
                f"（本回复累计 {len(self._pending_dispatches)} 个待派发任务，本轮决策结束后统一派发执行）。"
                "全部任务登记完毕后请直接输出简短说明文本结束本轮（例如：已派发 N 个任务，等待执行结果），"
                "不要再调用 dispatch_agent")

    def _exec_request_plan(self, tc: dict[str, Any], session_state) -> dict[str, Any]:
        """本地执行 request_plan：登记计划请求（observation 路由 planner）。"""
        orch = self.board.orchestration(session_state)
        if orch is not None and orch.mode == "planner_executor":
            return self._tool_result_msg(
                tc, "已处于计划模式，不能重复请求计划：请用 dispatch_agent 派发看板待办或直接输出汇总")
        if self._pending_dispatches:
            return self._tool_result_msg(
                tc, "已登记待派发任务，两者互斥：本轮将派发已登记任务，请勿请求计划")
        task = str((tc.get("arguments") or {}).get("task") or "").strip()
        self._plan_request = task
        return self._tool_result_msg(
            tc, "计划请求已登记：本轮决策结束后转交 planner 制定执行计划，请停止派发并结束本轮")

    # ── observation：决策结果分发 → 把入参消息改造为下一跳 ──

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """按决策工具累积状态组装下一跳（优先级：计划请求 > 待派发 > 终答）。

        - request_plan 已登记 → planner 路由跳（task=用户原始请求）；
        - 本轮有 dispatch_agent 决策 → 派发跳：单项=普通派发跳 / 多项=批信封（并行）；
        - 无工具决策 → 终答跳：LLM 最终文本即面向用户答复（直答/FINISH 合一）。
        """
        if self._plan_request is not None:
            return self._route_to_planner(msg, self._plan_request)
        if self._pending_dispatches:
            return self._build_dispatch_hop(msg)
        final_text = str((llm_response or {}).get("content") or "").strip()
        return self._final_answer(msg, final_text or "任务已完成。")

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

    def _route_to_planner(self, msg: AgentMessage, task_hint: str = "") -> AgentMessage:
        """进 planner-executor 模式：重置编排上下文，task 优先为用户原始请求
        （supervisor 不给编排建议，计划由 planner 自行制定并落任务看板）。"""
        session_state = self._session_state
        orch = self.board.orchestration(session_state) if session_state is not None else None
        if orch is not None:
            orch.mode = "planner_executor"
            orch.plan = None
            orch.step_task_map = {}
            orch.turn_task_ids = []
        request_msg = str(getattr(getattr(session_state, "request", None), "message", "") or "")
        task = request_msg or task_hint or msg.task
        msg.sender = self.agent_name
        msg.recipient = "planner"
        msg.task = task
        msg.result = ""
        msg.task_id = None
        msg.step_id = None
        msg.direct_reply = False
        msg.success = True
        return msg

    def _build_dispatch_hop(self, msg: AgentMessage) -> AgentMessage:
        """把累积的待派发跳组装为下一跳：单项=改造入参消息为普通派发跳；
        多项=批信封（recipient 哨兵 __batch__，调度器并行执行整批后合并审查摘要回灌）。

        - 单项：direct_reply 随决策下传（免汇总直答）；
        - 多项：批 >1 强制 direct_reply=False（跨任务汇总由终答完成）；
        - task_id 为登记时的新任务 id（防同 agent 再派发时旧值串号）。
        """
        dispatches = self._pending_dispatches
        # 单项：普通派发跳（与原行为一致）
        if len(dispatches) == 1:
            d = dispatches[0]
            msg.sender = self.agent_name
            msg.recipient = d.recipient
            msg.task = d.task
            msg.result = ""
            msg.task_id = d.task_id
            msg.step_id = d.step_id
            msg.direct_reply = bool(d.direct_reply)
            msg.success = True
            return msg

        # 多项：批派发信封
        for d in dispatches:
            if d.direct_reply:
                log.warning("Supervisor: 批量派发含 direct_reply 项，强制回传审查（跨任务汇总由终答完成）")
                d.direct_reply = False
        log.info("Supervisor: 批量派发 {} 个子任务（并行执行）| {}",
                 len(dispatches), "、".join(f"{d.recipient}#{d.task_id}" for d in dispatches))
        msg.sender = self.agent_name
        msg.recipient = "__batch__"
        msg.result = ""
        msg.task_id = None
        msg.step_id = None
        msg.direct_reply = False
        msg.success = True
        msg.dispatch_batch = dispatches
        return msg
