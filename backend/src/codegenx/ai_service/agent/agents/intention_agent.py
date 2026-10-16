"""
SupervisorAgent —— 多轮编排监管者（双模式）

模式一：自主编排模式（mode="direct"，默认）
  supervisor 自己 react：路由/审查 think → CALL_EXECUTOR 派发执行智能体
  → 收结果 → 再 think 决定下一个执行智能体 → ... → FINISH 总结。
  每次派发前在看板登记任务（线性依赖上一个任务）。

模式二：planner-executor 模式（mode="planner_executor"）
  路由 think 决定 CALL_PLANNER → planner 产出计划 → 计划落任务看板
  （TaskBoardService：step → TaskRecord，executor_agent=执行智能体，depends_on=blocked_by DAG）
  → 就绪任务确定性派发（免 think）→ 执行智能体回传 → 看板标记 completed
  （自动解锁下游）→ 派发下一个就绪任务 → 无可派发任务才 think 审查（汇总/僵局决策）。

任务看板：复用全局 TaskBoardService（.data/{user}/{app}/session/{sid}/.tasks），
每个任务一个 TaskRecord JSON（状态 pending/in_progress/completed，executor_agent=执行智能体），
后续供前端展示。就绪规则复用 is_ready（pending 且 blocked_by 为空）。

跨 run 编排上下文 SessionContext.orchestration（turn 级，请求开始重置）：
  mode / plan / step_task_map(step_id→task_id) / turn_task_ids / last_task_id / execution_trace
（会话历史由 chat_messages 真相源承载，history_desc 由其派生；方案《上下文工程设计方案2》）
"""

import json as _json
from enum import Enum
from typing import Any

from pydantic import BaseModel

from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage, NextMessageModel
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.context.session_context import OrchestrationState
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.task.task_manager import TaskRecord
from shared import log


class SupervisorActionType(str, Enum):
    """监管者决策动作（路由模式 + 审查模式统一五选一）。"""
    DIRECT_ANSWER = "direct_answer"          # 直接答复用户（打招呼 / 闲聊 / 违规 / 歧义需反问）
    CALL_EXECUTE_AGENT = "call_executor"     # 调用执行智能体（自主编排：单任务直派 / 审查时派下一步）
    CALL_PLANNER = "call_planner"            # 调用 planner，进入 planner-executor 计划编排模式
    NEXT_STEP = "next_step"                  # 计划模式僵局逃生：强制指定计划中的下一步
    FINISH = "finish"                        # 所有任务完成，汇总结果给用户


class SupervisorDecision(BaseModel):
    """LLM 返回的决策（Action 解析目标）。"""
    action: SupervisorActionType
    agent: str = ""                    # call_executor  / next_step 时填 agent name
    task: str = ""                     # call_executor  / next_step 时填任务描述
    answer: str = ""                   # direct_answer / finish 时填面向用户的答复


class SupervisorAction(Action[SupervisorDecision]):
    """监管者路由/审查动作：解析 LLM JSON 为 SupervisorDecision。"""

    async def act(self, ai_message: ActOutput, session_state=None, **kwargs) -> ActOutput:
        parsed = self.parse_ai_message(ai_message, SupervisorDecision)
        if parsed is None:
            return ActOutput(success=False, message="监管者决策解析失败（非合法 JSON）")

        action = parsed.action
        if action in (SupervisorActionType.CALL_EXECUTE_AGENT,
                      SupervisorActionType.CALL_PLANNER,
                      SupervisorActionType.NEXT_STEP):
            if not parsed.agent.strip():
                return ActOutput(success=False, message=f"{action.value} 分支缺失 agent 名称")
        elif action in (SupervisorActionType.DIRECT_ANSWER, SupervisorActionType.FINISH):
            if not parsed.answer.strip():
                return ActOutput(success=False, message=f"{action.value} 分支缺失 answer 内容")

        return ActOutput(success=True, output=parsed.model_dump(mode="json"))


class SupervisorAgent(BaseReActAgent):
    """多轮编排监管者：自主编排（react） / planner-executor（计划确定性派发）双模式。"""
    name = "supervisor"

    # ── 路由模式 profile（sender == user 时使用） ──
    route_profile: PromptConfig = PromptConfig(
        role="supervisor",
        name="supervisor",
        goal="作为数据分析多智能体系统的监管者，请分析用户请求，选择最合适的处理方式。",
        desc="入口监管者，负责路由与多轮编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "可用 DIRECT_ANSWER / CALL_EXECUTOR / CALL_PLANNER 三种方式。",
            "DIRECT_ANSWER：打招呼、闲聊、无关/违规问题、信息歧义需反问、可以根据提供的信息回答用户问题了 → 直接回复用户。",
            "CALL_EXECUTOR：默认选择，适用于子任务数量少（通常 1~3 个）且相互独立的任务——各子任务分别取数或各自可独立完成，相互之间没有运行时数据依赖 → 逐个派发执行智能体（每次一个），最终由你在 finish 中汇总。"
            "派发后续任务时可基于已回传的结果构造 task 描述。",
            "CALL_PLANNER：仅当步骤较多（通常 ≥4 个）且存在复杂依赖链（后续步骤要做什么取决于前面步骤的运行时结果，需要按计划确定性调度）时使用。"
            "task 直接填用户的原始请求，禁止附加任何编排建议或执行方案（计划由 planner 自行制定）。",
            "对比、汇总、差值计算、结论生成是你的职责：禁止为了汇总多个子任务的结果而调用 planner，也禁止把跨数据源的汇总要求写进派发给执行智能体的 task 里。",
            "只输出指定 JSON，禁止额外文字解释，保证可被 python json.loads 正常解析。",
        ],
        resource_prompt="",
        expand_prompt="可用的智能体如下：\n{{agents_list_desc}}",
        examples="",
        out_schema="""请严格按如下 JSON 格式输出：
{
    "action": "三选一：direct_answer / call_executor / call_planner",
    "agent": "call_executor / call_planner 时填智能体 name；direct_answer 填空字符串",
    "task": "call_executor 时填任务描述； call_planner /direct_answer 填空字符串",
    "answer": "direct_answer 时填面向用户的答复；其余填空字符串"
}""",
    )

    # ── 审查模式 profile（自主编排的逐步审查 + 计划模式的收尾审查共用） ──
    review_profile: PromptConfig = PromptConfig(
        role="supervisor",
        name="supervisor",
        goal="作为数据分析多智能体系统的监管者，正在审查执行结果并决定下一步。",
        desc="入口监管者，负责路由与多轮编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "可用 CALL_EXECUTOR / NEXT_STEP / FINISH 三种方式。",
            "CALL_EXECUTOR：自主编排模式下派发下一个执行智能体 → agent 填执行智能体 name，task 填任务描述（以该智能体自身的能力与输出规范为准，禁止指定特定输出格式）。"
            "task 必须完全自包含：执行智能体只看得到 task 文本本身，看不到对话历史与其它任务，执行所需的全部信息（文件名、数值、统计口径）必须直接写进 task，禁止出现「上一步」「之前的结果」等引用。"
            "task 只要求执行智能体产出其数据源内的原始统计结果，差值、增长率、对比结论等跨数据汇总由你在 finish 中计算，不要写进 task。",
            "NEXT_STEP：计划模式下出现僵局（看板无就绪任务但计划未完成）时，从当前计划强制指定下一个要执行的 agent 和 task。",
            "FINISH：所有任务完成或无需继续 → answer 字段汇总执行历史中的结果给用户。",
            "若对话历史中最新回传结果已包含完成任务所需的数据或答案，必须 FINISH 并在 answer 中基于该结果汇总答复，禁止以格式不符等理由对同一智能体重复派发相同或相似任务。",
            "计划中 executor_agent 为 supervisor 的汇总类步骤由你自己完成，在 FINISH 的 answer 中直接给出结果，禁止把 supervisor 作为派发目标。",
            "只输出指定 JSON，禁止额外文字解释，保证可被 python json.loads 正常解析。",
        ],
        resource_prompt="",
        expand_prompt="可用的执行智能体如下：\n{{agents_list_desc}}\n\n任务看板：\n{{board_desc}}\n\n（子 agent 执行历史与最新回传结果见对话历史消息）",
        examples="",
        out_schema="""请严格按如下 JSON 格式输出：
{
    "action": "三选一：call_executor / next_step / finish",
    "agent": "call_executor / next_step 时填智能体 name；finish 填空字符串",
    "task": "call_executor / next_step 时填任务描述；finish 填空字符串",
    "answer": "finish 时填面向用户的汇总答复；其余填空字符串"
}""",
    )

    # profile 保留 route_profile（默认路由模式），_prepare_agent 根据 _is_review 切换
    profile: PromptConfig = route_profile

    def __init__(self):
        super().__init__()
        self.actions = [SupervisorAction()]
        # run 级状态（实例即建即弃，不落会话状态）
        self._session_state: RuntimeSessionState | None = None
        self._is_review: bool = False

    # ── run：模式判断 + 跨 run 状态消费 + 计划确定性派发 ──

    @staticmethod
    def _orchestration(session_state) -> OrchestrationState | None:
        """取编排上下文（SessionContext.orchestration；context 未初始化时返回 None）。"""
        cm = getattr(session_state, "context_manager", None)
        return cm.orchestration if cm is not None else None

    async def run(self, agent_message: AgentMessage, session_state:RuntimeSessionState) -> AgentMessage:
        caller = agent_message.sender or "user"
        # 供 observation/_prepare_prompt_params 等 run 内环节使用（基类不持有）
        self._session_state = session_state
        # _is_review 参数化：run 内由 caller 判断（实例即建即弃，不落会话状态）
        self._is_review = (caller != "user")
        orch = self._orchestration(session_state)

        if caller == "user":
            # 新请求：重置编排上下文（turn 级）+ 刷新看板视图，默认自主编排模式
            cm = getattr(session_state, "context_manager", None)
            if cm is not None:
                cm.orchestration.reset()
                cm.refresh_task_prompt()
        elif caller == "planner":
            # planner-executor 模式：计划落看板后，就绪任务确定性派发（免 think）
            self._consume_planner_plan(agent_message, session_state)
            dispatched = self._dispatch_next_ready(session_state, agent_message)
            if dispatched is not None:
                return dispatched
            # 计划为空 / 全部自执行 / 解析失败 → 进入 think 审查兜底
        else:
            # 执行智能体回传：追加执行轨迹 + 看板任务标记 completed（自动解锁下游）
            self._consume_child_result(agent_message, caller, session_state)
            if orch is not None and orch.mode == "planner_executor":
                # 有计划：就绪任务直接派发（免 think）；无可派发任务才 think 审查（汇总/僵局决策）
                dispatched = self._dispatch_next_ready(session_state, agent_message)
                if dispatched is not None:
                    return dispatched
            # 自主编排模式：think 审查（react 决定下一个执行智能体或总结）

        log.info("Supervisor run [{}模式] | from={} | mode={} | request_id={}",
                 "审查" if self._is_review else "路由", caller,
                 orch.mode if orch is not None else "?", agent_message.request_id)

        return await super().run(agent_message, session_state)

    # ── 任务看板访问（复用全局 TaskBoardService，ids 走参数） ──

    @staticmethod
    def _board_service():
        return get_app().tasks

    @staticmethod
    def _ids(session_state) -> dict:
        return {
            "user_id": session_state.user_id,
            "app_id": str(session_state.app_id or ""),
            "session_id": session_state.session_id,
        }

    def _pick_task_for_agent(self, agent: str) -> TaskRecord | None:
        """从看板找该 agent 本轮的待办任务（pending 且依赖已满足）。"""
        if self._session_state is None:
            return None
        tasks = self._board_service()
        if tasks is None:
            return None
        orch = self._orchestration(self._session_state)
        turn_ids = set(orch.turn_task_ids) if orch is not None else set()
        for t in tasks.list_all(**self._ids(self._session_state)):
            if t.id not in turn_ids:
                continue
            if t.executor_agent == agent and tasks.is_ready(t):
                return t
        return None

    # ── planner-executor 模式：计划落看板 + 确定性派发 ──

    def _consume_planner_plan(self, msg: AgentMessage, session_state) -> None:
        """Planner 回传 plan → 逐 step 落任务看板（executor_agent=执行智能体，depends_on=DAG 前置）。"""
        task_raw = msg.message.get("task", "") or ""
        try:
            parsed = _json.loads(task_raw)
        except Exception:
            parsed = None
        if not (isinstance(parsed, dict) and parsed.get("steps")):
            log.warning("Supervisor: Planner 回传无法解析为 plan，跳过")
            return

        orch = self._orchestration(session_state)
        tasks = self._board_service()
        if orch is None or tasks is None:
            return
        orch.mode = "planner_executor"
        orch.plan = parsed
        turn_ids: list = []
        ids = self._ids(session_state)
        step_task_map: dict = {}
        for s in sorted(parsed.get("steps", []), key=lambda x: x.get("step_id", 0)):
            dep_ids = [step_task_map[d] for d in (s.get("depends_on") or []) if d in step_task_map]
            # 计划落板：step_id / executor_agent / source=planner 一次性写入 TaskRecord
            record = tasks.create(
                subject=str(s.get("task", "") or ""),
                description="; ".join(str(r) for r in (s.get("resources") or [])),
                depends_on=dep_ids,
                executor_agent=str(s.get("agent", "") or ""),
                step_id=int(s.get("step_id", 0) or 0) or None,
                source="planner",
                **ids,
            )
            step_task_map[s.get("step_id")] = record.id
            turn_ids.append(record.id)
        # task→step 方向由 TaskRecord.step_id 派生，不再单独存映射
        orch.step_task_map = step_task_map
        orch.turn_task_ids = turn_ids
        log.info("Supervisor: 计划已落任务看板 | goal={} | steps={} | mode=planner_executor",
                 parsed.get("goal", ""), len(parsed.get("steps", [])))

    def _dispatch_next_ready(self, session_state, src_msg: AgentMessage) -> AgentMessage | None:
        """按就绪规则（pending 且 blocked_by 空）取本轮看板任务确定性派发（免 think）。

        executor_agent 为 supervisor 的汇总类步骤不派发，直接标记 completed 以解锁下游。
        """
        tasks = self._board_service()
        if tasks is None:
            return None
        orch = self._orchestration(session_state)
        turn_ids = set(orch.turn_task_ids) if orch is not None else set()
        if not turn_ids:
            return None

        ready = [
            t for t in tasks.list_all(**self._ids(session_state))
            if t.id in turn_ids and tasks.is_ready(t)
        ]
        for task in sorted(ready, key=lambda t: t.id):
            if task.executor_agent in (self.agent_name, "supervisor"):
                # 汇总类步骤由审查 think 自行完成，此处仅落状态解锁下游
                tasks.complete(task.id, **self._ids(session_state))
                continue
            return self._build_dispatch_msg(session_state, src_msg, task)
        return None

    def _build_dispatch_msg(self, session_state, src_msg: AgentMessage, task: TaskRecord) -> AgentMessage:
        """派发看板任务：标记 in_progress 并构造下一跳消息（不经过 LLM think）。"""
        tasks = self._board_service()
        if tasks is not None:
            tasks.update(task.id, status="in_progress", **self._ids(session_state))
        task_text = task.subject
        step_id = task.step_id  # task→step 方向由 TaskRecord.step_id 直接携带（不再存映射表）
        log.info("Supervisor: 派发看板任务 #{} → {}（免 think）| {}",
                 task.id, task.executor_agent, task_text[:60])
        return AgentMessage(
            sender=self.agent_name,
            recipient=task.executor_agent,
            is_user_message=False,
            message={"task": task_text, "metadata": {"task_id": task.id, "step_id": step_id}},
            request_id=src_msg.request_id,
            session_id=session_state.session_id,
            summary=task_text[:200],
        )

    # ── 自主编排模式：派发前登记看板任务 ──

    def _create_dispatch_task(self, session_state, agent: str, subject: str) -> TaskRecord | None:
        """派发执行智能体前在看板登记任务（线性依赖上一任务，供前端展示与状态跟踪）。"""
        if session_state is None or not subject:
            return None
        tasks = self._board_service()
        orch = self._orchestration(session_state)
        if tasks is None or orch is None:
            return None
        last_id = orch.last_task_id
        record = tasks.create(
            subject=subject,
            description="自主编排模式派发",
            depends_on=[last_id] if last_id else [],
            executor_agent=agent,
            source="direct",
            **self._ids(session_state),
        )
        tasks.update(record.id, status="in_progress", **self._ids(session_state))
        orch.turn_task_ids.append(record.id)
        orch.last_task_id = record.id
        log.info("Supervisor: 登记看板任务 #{} → {}（in_progress）", record.id, agent)
        return record

    # ── 执行结果消费 ──

    def _consume_child_result(self, msg: AgentMessage, caller: str, session_state) -> None:
        """执行智能体回传结果 → 追加执行轨迹 + 看板任务标记 completed（自动解锁下游）。

        history 不再单独维护：会话历史由 chat_messages 真相源承载
        （AutoPlanManager add_agent_result 写入），history_desc 由其派生。
        """
        task = msg.message.get("task", "") or ""
        metadata = msg.message.get("metadata") or {}
        summary = task[:300] if isinstance(task, str) else str(task)[:300]
        orch = self._orchestration(session_state)

        # 执行轨迹（turn 级可观测视图，非真相源）
        if orch is not None:
            orch.execution_trace.append({
                "seq": len(orch.execution_trace) + 1,
                "agent": caller,
                "task_summary": summary[:120],
                "success": True,
                "result_digest": str(metadata or summary)[:200],
            })
            log.info("Supervisor: 追加执行轨迹 | from={} | total={}", caller, len(orch.execution_trace))

        # 任务状态管理：该执行智能体本轮的 in_progress 看板任务 → completed
        tasks = self._board_service()
        if tasks is None:
            return
        turn_ids = set(orch.turn_task_ids) if orch is not None else set()
        for t in tasks.list_all(status="in_progress", **self._ids(session_state)):
            if t.id in turn_ids and t.executor_agent == caller:
                tasks.complete(t.id, **self._ids(session_state))
                log.info("Supervisor: 看板任务 #{} 已完成 | agent={}", t.id, caller)
                break

    # ── prompt 构建：_prepare_prompt_params 传参，_prepare_agent 选模板 ──

    def _prepare_prompt_params(self) -> dict:
        """返回当前模式下需要的 prompt 变量。"""
        is_review = getattr(self, "_is_review", False)

        params = {
            "agents_list_desc": self._agents_list_desc(include_planner=not is_review),
        }
        if is_review:
            # 执行历史不再走 system prompt（history_desc/latest_result 已移除），
            # 由 _build_history_messages 注入的对话历史消息承载
            params["board_desc"] = self._board_desc()
        return params

    def _build_history_messages(self, session_state) -> list[dict]:
        """Supervisor 注入会话历史（chat_messages 真相源，跨 turn 连续）。

        - route 模式：末条为本轮用户请求（add_user_message），与当前 task_text
          重复，去除后注入跨 turn 对话历史，支持追问场景；
        - 审查模式：末条为子 agent 回传（add_agent_result），其内容与当前
          task_text 重复，去除后注入（本轮用户请求 + 之前执行结果 + 历史轮）；
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
            self._system_prompt = self.review_profile.build_default_system_prompt(**params)

    # ── observation ──

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[str, Any]) -> AgentMessage:
        """根据 SupervisorAction 决策，组装下一跳。"""
        parsed = act_output.output or {}
        action_str = parsed.get("action", "")

        msg.output = act_output.model_dump(mode="json")

        if not act_output.success or not action_str:
            log.warning("Supervisor observation: 决策失败，兜底返回用户")
            msg.next = NextMessageModel(next_agent="user", message=f"监管者决策失败：{act_output.message}")
            msg.success = True
            return msg

        if action_str in (SupervisorActionType.DIRECT_ANSWER.value, SupervisorActionType.FINISH.value):
            answer = parsed.get("answer") or "任务已完成。"
            msg.next = NextMessageModel(next_agent="user", message=answer)

        elif action_str in (
            SupervisorActionType.CALL_EXECUTE_AGENT.value,
            SupervisorActionType.CALL_PLANNER.value,
            SupervisorActionType.NEXT_STEP.value,
        ):
            agent = (parsed.get("agent") or "").strip()
            task = (parsed.get("task") or "").strip()
            # 容错：executor 分支误填 planner 时按进入计划模式处理
            if action_str == SupervisorActionType.CALL_EXECUTE_AGENT.value and agent == "planner":
                action_str = SupervisorActionType.CALL_PLANNER.value

            if not agent:
                msg.next = NextMessageModel(next_agent="user", message="监管者路由失败：未指定子智能体")
            elif agent in (self.agent_name, "supervisor"):
                # 禁止派发自身（汇总类任务由审查环节自行完成）：兜底转 FINISH
                log.warning("Supervisor observation: 决策试图派发自身，按 FINISH 兜底")
                msg.next = NextMessageModel(next_agent="user", message=parsed.get("answer") or "任务已完成。")
            elif action_str == SupervisorActionType.CALL_PLANNER.value:
                # 进入 planner-executor 模式：重置编排上下文，task 强制为用户原始请求
                #（supervisor 不给编排建议，计划由 planner 自行制定并落任务看板）
                session_state = self._session_state
                orch = self._orchestration(session_state) if session_state is not None else None
                if orch is not None:
                    orch.mode = "planner_executor"
                    orch.plan = None
                    orch.step_task_map = {}
                    orch.turn_task_ids = []
                    task = str(getattr(session_state.request, "message", "") or task)
                msg.next = NextMessageModel(next_agent="planner", message=task or msg.message.get("task", ""))
            elif action_str == SupervisorActionType.NEXT_STEP.value:
                # 计划僵局逃生：按 agent 从看板强制取一个待办任务派发
                picked = self._pick_task_for_agent(agent)
                if picked is not None:
                    tasks = self._board_service()
                    if tasks is not None:
                        tasks.update(picked.id, status="in_progress", **self._ids(self._session_state))
                    msg.next = NextMessageModel(next_agent=agent, message=picked.subject or task)
                else:
                    # 看板无该 agent 的待办任务 → 按自主派发处理（登记新看板任务）
                    self._create_dispatch_task(self._session_state, agent, task or str(msg.message.get("task", ""))[:200])
                    msg.next = NextMessageModel(next_agent=agent, message=task or msg.message.get("task", ""))
            else:
                # CALL_EXECUTOR（自主编排派发）：先登记看板任务（前端可见），再派发
                self._create_dispatch_task(self._session_state, agent, task or str(msg.message.get("task", ""))[:200])
                msg.next = NextMessageModel(next_agent=agent, message=task or msg.message.get("task", ""))

        else:
            msg.next = NextMessageModel(next_agent="user", message=f"未知决策动作: {action_str}")

        msg.success = True  # 一轮 run 内 Supervisor 只做一次决策
        return msg

    # ── prompt 模板辅助 ──

    @staticmethod
    def _agents_list_desc(include_planner: bool = True) -> str:
        """可用智能体清单：路由模式含 planner（可进计划模式），审查模式仅执行智能体。"""
        skip_roles = {"supervisor", "intent_recognition"}
        if not include_planner:
            skip_roles.add("planner")
        lines = []
        for spec in get_app().agents.all_specs():
            if spec.role in skip_roles:
                continue
            lines.append(f"{spec.name} - {spec.desc}")
        return "\n".join(lines) or "（暂无可用子智能体）"

    def _board_desc(self) -> str:
        """渲染任务看板文本：读 TurnPrompts.task_prompt（单一来源），计划模式附 goal 行。

        审查前先 refresh：turn 过程中任务在完成、依赖在解锁，需最新就绪集（方案 §4.2）。
        """
        cm = getattr(self._session_state, "context_manager", None) if self._session_state else None
        if cm is None:
            return "（暂无任务看板）"
        cm.refresh_task_prompt()
        board = cm.prompts.task_prompt or "（暂无任务看板）"
        plan = cm.orchestration.plan or {}
        if plan.get("goal"):
            return f"goal: {plan.get('goal')}\n{board}"
        return board
