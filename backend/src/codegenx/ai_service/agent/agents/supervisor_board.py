"""TaskBoardCoordinator —— Supervisor 看板协调器（从 intention_agent.py 拆分）。

集中全部看板/编排状态的确定性操作（无 LLM 参与）：
- turn 生命周期：reset_turn（编排上下文重置 + 看板视图刷新）
- 计划模式：consume_planner_plan（plan 落看板）/ dispatch_next_ready（就绪任务免 think 派发）
- 结果消费：consume_child_result（执行轨迹 + 看板闭环，task_id 精确握手）
- 任务登记与选取：create_dispatch_task / pick_task_for_agent / dispatch_board_task
- prompt 渲染：render_board

看板复用全局 TaskBoardService（.data/{user}/{app}/session/{sid}/.tasks/{request_id}.json，
按 request 单文件隔离，task_id 各自从 1 计数）；编排上下文复用 SessionContext.orchestration（turn 级）。
"""

import json as _json

from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.context.session_context import OrchestrationState
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.task.task_manager import TaskRecord, TaskStatus
from shared import log


class TaskBoardCoordinator:
    """看板协调器：任务看板读写 + 编排上下文维护（supervisor 编排的确定性动作层）。"""

    sender = "supervisor"

    # ── 服务与上下文访问 ──

    @staticmethod
    def _service():
        return get_app().tasks

    @staticmethod
    def _ids(session_state) -> dict:
        # request_id 一起下传：任务看板按 request 单文件隔离，task_id 为 request 内局部 id
        return {
            "user_id": session_state.user_id,
            "app_id": str(session_state.app_id or ""),
            "session_id": session_state.session_id,
            "request_id": str(getattr(session_state, "request_id", "") or ""),
        }

    @staticmethod
    def orchestration(session_state) -> OrchestrationState | None:
        """取编排上下文（SessionContext.orchestration；context 未初始化时返回 None）。"""
        cm = getattr(session_state, "context_manager", None)
        return cm.orchestration if cm is not None else None

    # ── turn 生命周期 ──

    def reset_turn(self, session_state) -> None:
        """新请求开始：重置编排上下文（turn 级）+ 刷新看板视图，回到自主编排模式。"""
        cm = getattr(session_state, "context_manager", None)
        if cm is not None:
            cm.orchestration.reset()
            cm.refresh_task_prompt(str(getattr(session_state, "request_id", "") or ""))

    # ── 计划模式：计划落板 + 确定性派发 ──

    def consume_planner_plan(self, msg: AgentMessage, session_state) -> None:
        """Planner 回传 plan（msg.result 为 plan JSON）→ 逐 step 落任务看板
        （executor_agent=执行智能体，depends_on=DAG 前置）。"""
        try:
            parsed = _json.loads(msg.result or "")
        except Exception:
            parsed = None
        if not (isinstance(parsed, dict) and parsed.get("steps")):
            log.warning("Supervisor: Planner 回传无法解析为 plan，跳过")
            return

        orch = self.orchestration(session_state)
        tasks = self._service()
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

    def dispatch_next_ready(self, session_state, src_msg: AgentMessage) -> AgentMessage | None:
        """按就绪规则（pending 且 blocked_by 空）取本轮看板任务确定性派发（免 think）。

        同波多个就绪任务批量并行派发（dispatch_batch 信封，调度器并行执行整批）；
        executor_agent 为 supervisor 的汇总类步骤不派发，直接标记 completed 以解锁下游。
        """
        tasks = self._service()
        if tasks is None:
            return None
        orch = self.orchestration(session_state)
        turn_ids = set(orch.turn_task_ids) if orch is not None else set()
        if not turn_ids:
            return None

        ready = [
            t for t in tasks.list_all(**self._ids(session_state))
            if t.id in turn_ids and tasks.is_ready(t)
        ]
        wave: list[TaskRecord] = []
        for task in sorted(ready, key=lambda t: t.id):
            if task.executor_agent in (self.sender, "supervisor"):
                # 汇总类步骤由审查 think 自行完成，此处仅落状态解锁下游
                tasks.complete(task.id, **self._ids(session_state))
                continue
            wave.append(task)
        if not wave:
            return None
        if len(wave) == 1:
            return self.dispatch_board_task(session_state, src_msg.request_id, wave[0])
        # 同波多任务：批量信封（调度器并行执行整批）
        dispatches = [self.dispatch_board_task(session_state, src_msg.request_id, t) for t in wave]
        log.info("Supervisor: 计划模式同波并行派发 {} 个就绪任务 | {}",
                 len(dispatches), "、".join(f"{d.recipient}#{d.task_id}" for d in dispatches))
        return AgentMessage(
            sender=self.sender,
            recipient="__batch__",
            request_id=src_msg.request_id,
            session_id=session_state.session_id,
            success=True,
            dispatch_batch=dispatches,
        )

    def dispatch_board_task(self, session_state, request_id: str, task: TaskRecord) -> AgentMessage:
        """派发指定看板任务：标记 in_progress 并构造派发跳（不经过 LLM think）。"""
        tasks = self._service()
        if tasks is not None:
            tasks.update(task.id, status="in_progress", **self._ids(session_state))
        log.info("Supervisor: 派发看板任务 #{} → {}（免 think）| {}",
                 task.id, task.executor_agent, task.subject[:60])
        return AgentMessage(
            sender=self.sender,
            recipient=task.executor_agent,
            task=task.subject,
            task_id=task.id,
            step_id=task.step_id,
            request_id=request_id,
            session_id=session_state.session_id,
            success=True,
        )

    # ── 任务登记与选取 ──

    def pick_task_for_agent(self, agent: str, session_state) -> TaskRecord | None:
        """从看板找该 agent 本轮的待办任务（pending 且依赖已满足）。"""
        tasks = self._service()
        if tasks is None:
            return None
        orch = self.orchestration(session_state)
        turn_ids = set(orch.turn_task_ids) if orch is not None else set()
        for t in tasks.list_all(**self._ids(session_state)):
            if t.id not in turn_ids:
                continue
            if t.executor_agent == agent and tasks.is_ready(t):
                return t
        return None

    @staticmethod
    def _norm_subject(text: str) -> str:
        """任务描述归一化：去除全部空白字符后比较（仅空白差异视为相同任务）。"""
        return "".join(str(text or "").split())

    def find_same_task(self, session_state, agent: str, subject: str) -> TaskRecord | None:
        """本 request 内查找该 agent 的相同任务（归一化 subject 完全一致，含已完成）。

        供 dispatch_agent 防重复派发守卫使用：已完成任务的执行结果已在对话历史中，
        重跑相同任务只会产生重复/矛盾结果。
        """
        tasks = self._service()
        if tasks is None:
            return None
        target = self._norm_subject(subject)
        if not target:
            return None
        for t in tasks.list_all(**self._ids(session_state)):
            if t.executor_agent == agent and self._norm_subject(t.subject) == target:
                return t
        return None

    def create_dispatch_task(self, session_state, agent: str, subject: str) -> TaskRecord | None:
        """派发执行智能体前在看板登记任务（批内互不依赖，供前端展示与状态跟踪）。

        自主编排的任务间真实依赖由 supervisor 串行派发次序保证（依赖前置结果的
        任务等回传后再派发），看板不再强制线性依赖以支持批量并行派发。
        """
        if session_state is None or not subject:
            return None
        tasks = self._service()
        orch = self.orchestration(session_state)
        if tasks is None or orch is None:
            return None
        record = tasks.create(
            subject=subject,
            description="自主编排模式派发",
            depends_on=[],
            executor_agent=agent,
            source="direct",
            **self._ids(session_state),
        )
        tasks.update(record.id, status="in_progress", **self._ids(session_state))
        orch.turn_task_ids.append(record.id)
        log.info("Supervisor: 登记看板任务 #{} → {}（in_progress）", record.id, agent)
        return record

    # ── 结果消费 ──

    def consume_child_result(self, msg: AgentMessage, session_state) -> None:
        """执行智能体回传结果 → 追加执行轨迹 + 看板任务标记 completed（自动解锁下游）。

        任务闭环优先按回传 task_id（一等字段）精确匹配派发时的看板任务
        （同 agent 多任务/残留 in_progress 时不会误标）；仅缺失 task_id 的
        异常消息才按 executor_agent 兜底匹配本轮 in_progress 任务。
        """
        caller = msg.sender
        task_id = msg.task_id
        summary = (msg.task or "")[:300]
        orch = self.orchestration(session_state)

        # 执行轨迹（turn 级可观测视图，非真相源）
        if orch is not None:
            orch.execution_trace.append({
                "seq": len(orch.execution_trace) + 1,
                "agent": caller,
                "task_id": task_id,
                "task_summary": summary[:120],
                "success": True,
                "result_digest": (msg.result or summary)[:200],
            })
            log.info("Supervisor: 追加执行轨迹 | from={} | task_id={} | total={}",
                     caller, task_id, len(orch.execution_trace))

        # 任务状态管理：按 task_id 精确闭环
        tasks = self._service()
        if tasks is None:
            return
        ids = self._ids(session_state)
        if task_id is not None:
            t = tasks.get(task_id, **ids)
            if t is None or t.status != TaskStatus.IN_PROGRESS:
                log.warning("Supervisor: 回传 task_id={} 无对应 in_progress 看板任务，跳过闭环 | agent={}",
                            task_id, caller)
                return
            tasks.complete(t.id, **ids)
            log.info("Supervisor: 看板任务 #{} 已完成（task_id 精确闭环）| agent={}", t.id, caller)
            return

        # 兜底：缺失 task_id（异常路径）→ 按 executor_agent 匹配本轮 in_progress 任务
        turn_ids = set(orch.turn_task_ids) if orch is not None else set()
        for t in tasks.list_all(status="in_progress", **ids):
            if t.id in turn_ids and t.executor_agent == caller:
                tasks.complete(t.id, **ids)
                log.info("Supervisor: 看板任务 #{} 已完成（executor_agent 兜底）| agent={}", t.id, caller)
                break

    # ── prompt 渲染 ──

    def render_board(self, session_state) -> str:
        """渲染任务看板文本：读 TurnPrompts.task_prompt（单一来源），计划模式附 goal 行。

        审查前先 refresh：turn 过程中任务在完成、依赖在解锁，需最新就绪集。
        """
        cm = getattr(session_state, "context_manager", None)
        if cm is None:
            return "（暂无任务看板）"
        cm.refresh_task_prompt(str(getattr(session_state, "request_id", "") or ""))
        board = cm.prompts.task_prompt or "（暂无任务看板）"
        plan = cm.orchestration.plan or {}
        if plan.get("goal"):
            return f"goal: {plan.get('goal')}\n{board}"
        return board
