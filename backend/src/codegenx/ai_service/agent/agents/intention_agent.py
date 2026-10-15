"""
SupervisorAgent —— 多轮编排监管者

两种工作模式（每次 run() 根据 agent_message.sender 自动切换）：

1. 初始路由模式（sender == "user"）
   分析用户请求 → 三选一：DIRECT_ANSWER / CALL_AGENT
   设置 next_agent → 退出 run → 消息系统把请求发给下一跳

2. 审查编排模式（sender != "user"，来自 Planner 或子 agent）
   - Planner 回传 plan → 存 supervisor_ctx.plan → CALL_AGENT 指派第一个 step 的 agent
   - 子 agent 回传结果 → 追加到 history → CALL_AGENT 继续指派 / FINISH 总结

跨 run 的状态（plan / history）存在 session_state.supervisor_ctx 上，
避免类属性跨请求泄漏。
"""

import json as _json
from enum import Enum
from typing import Any

from pydantic import BaseModel

from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage, NextMessageModel
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from shared import log


class SupervisorActionType(str, Enum):
    """监管者决策（路由模式 + 审查模式统一四选一）。"""
    DIRECT_ANSWER = "direct_answer"   # 直接答复用户（打招呼 / 闲聊 / 违规 / 歧义需反问）
    CALL_AGENT = "call_agent"         # 调用某个子智能体（可以是 planner 或 data_explore 等）
    NEXT_STEP = "next_step"           # 审查模式：继续执行计划中下一个步骤
    FINISH = "finish"                 # 审查模式：所有步骤完成，总结给用户


class SupervisorDecision(BaseModel):
    """LLM 返回的决策（Action 解析目标）。"""
    action: SupervisorActionType
    agent: str = ""                    # call_agent / next_step 时填 agent name（可含 planner）
    task: str = ""                     # call_agent / next_step 时填任务描述
    answer: str = ""                   # direct_answer / finish 时填面向用户的答复


class SupervisorAction(Action[SupervisorDecision]):
    """监管者路由/审查动作：解析 LLM JSON 为 SupervisorDecision。"""

    async def act(self, ai_message: ActOutput, session_state=None, **kwargs) -> ActOutput:
        parsed = self.parse_ai_message(ai_message, SupervisorDecision)
        if parsed is None:
            return ActOutput(success=False, message="监管者决策解析失败（非合法 JSON）")

        action = parsed.action
        if action in (SupervisorActionType.CALL_AGENT, SupervisorActionType.NEXT_STEP):
            if not parsed.agent.strip():
                return ActOutput(success=False, message=f"{action.value} 分支缺失 agent 名称")
        elif action == SupervisorActionType.DIRECT_ANSWER:
            if not parsed.answer.strip():
                return ActOutput(success=False, message="DIRECT_ANSWER 分支缺失 answer 内容")

        return ActOutput(success=True, output=parsed.model_dump(mode="json"))


class SupervisorAgent(BaseReActAgent):
    """多轮编排监管者：路由 → 收结果 → 审查 → 再路由 / 总结。"""
    name = "supervisor"

    # ── 两种模式各自的 PromptConfig（基类只用一个 profile，这里覆盖 _prepare_agent 来切换） ──
    route_profile: PromptConfig = PromptConfig(
        role="supervisor",
        name="supervisor",
        goal="作为数据分析多智能体系统的监管者，请分析用户请求，选择最合适的处理方式。",
        desc="入口监管者，负责路由与多轮编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "可用 DIRECT_ANSWER / CALL_AGENT 两种方式。",
            "DIRECT_ANSWER：打招呼、闲聊、无关/违规问题、信息歧义需反问 → 直接回复用户。",
            "CALL_AGENT：需要调用某个子智能体（可以是 planner 负责复杂任务规划，也可以是 data_explore 等执行单一具体任务）→ agent 字段填具体智能体 name，task 字段填传给该智能体的任务描述。",
            "只输出指定 JSON，禁止额外文字解释，保证可被 python json.loads 正常解析。",
        ],
        resource_prompt="",
        expand_prompt="可用的子智能体如下：\n{{agents_list_desc}}",
        examples="",
        out_schema="""请严格按如下 JSON 格式输出：
{
    "action": "二选一：direct_answer / call_agent",
    "agent": "call_agent 时填子智能体 name（可含 planner）；direct_answer 填空字符串",
    "task": "call_agent 时填任务描述；direct_answer 填空字符串",
    "answer": "direct_answer 时填面向用户的答复；call_agent 填空字符串"
}""",
    )

    review_profile: PromptConfig = PromptConfig(
        role="supervisor",
        name="supervisor",
        goal="作为数据分析多智能体系统的监管者，正在审查子智能体执行结果并决定下一步。",
        desc="入口监管者，负责路由与多轮编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "可用 NEXT_STEP / CALL_AGENT / FINISH 三种方式。",
            "NEXT_STEP：继续执行计划中下一个未完成的步骤 → 从当前计划里选一个下一步要执行的 agent 和 task。",
            "CALL_AGENT：脱离当前计划，临时调用某个子智能体完成某个任务。",
            "FINISH：所有步骤已完成或无需继续 → answer 字段汇总结果给用户。",
            "只输出指定 JSON，禁止额外文字解释，保证可被 python json.loads 正常解析。",
        ],
        resource_prompt="",
        expand_prompt="可用的子智能体如下：\n{{agents_list_desc}}\n\n当前计划：\n{{plan_desc}}\n\n已执行历史：\n{{history_desc}}\n\n最新回传结果：\n{{latest_result}}",
        examples="",
        out_schema="""请严格按如下 JSON 格式输出：
{
    "action": "三选一：next_step / call_agent / finish",
    "agent": "next_step / call_agent 时填子智能体 name；finish 填空字符串",
    "task": "next_step / call_agent 时填任务描述；finish 填空字符串",
    "answer": "finish 时填面向用户的汇总答复；其余填空字符串"
}""",
    )

    # profile 保留 route_profile（默认路由模式），_prepare_agent 根据 supervisor_ctx.is_review 切换
    profile: PromptConfig = route_profile

    def __init__(self):
        super().__init__()
        self.actions = [SupervisorAction()]

    # ── run：只判断模式 + 消费跨 run 状态，不写 prompt ──

    async def run(self, agent_message: AgentMessage, session_state) -> AgentMessage:
        caller = agent_message.sender or "user"
        is_review = (caller != "user")
        self._session_state = session_state

        # 给 _prepare_prompt_params / _prepare_agent 传递模式标记
        session_state.supervisor_ctx["_is_review"] = is_review

        # ── 跨 run 的状态消费（在 run 开头做，确保 observation 看到最新） ──
        if caller == "planner":
            self._consume_planner_plan(agent_message, session_state)
        elif caller not in ("user", ""):
            self._consume_child_result(agent_message, caller, session_state)

        log.info("Supervisor run [{}模式] | from={} | request_id={}",
                 "审查" if is_review else "路由", caller, agent_message.request_id)

        return await super().run(agent_message, session_state)

    # ── prompt 构建：_prepare_prompt_params 传参，_prepare_agent 选模板 ──

    def _prepare_prompt_params(self) -> dict:
        """返回当前模式下需要的 prompt 变量。"""
        ctx = self._session_state.supervisor_ctx if self._session_state else {}
        is_review = ctx.get("_is_review", False)

        params = {
            "agents_list_desc": self._agents_list_desc(),
        }
        if is_review:
            params["plan_desc"] = self._format_plan(ctx.get("plan"))
            params["history_desc"] = self._format_history(ctx.get("history"))
            # latest_result 从最近一条 history 拿，或者从 _latest_result 暂存拿
            history = ctx.get("history") or []
            params["latest_result"] = str(history[-1]["result"])[:500] if history else "（暂无）"
        return params

    async def _prepare_agent(self, session_state):
        """基类已经通过 profile.build_default_system_prompt 构建了 system_prompt；
        这里如果是审查模式，用 review_profile 覆盖一次。"""
        await super()._prepare_agent(session_state)
        ctx = session_state.supervisor_ctx
        if ctx.get("_is_review"):
            # 审查模式：用 review_profile 重新 build
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

        elif action_str in (SupervisorActionType.CALL_AGENT.value, SupervisorActionType.NEXT_STEP.value):
            agent = (parsed.get("agent") or "").strip()
            task = (parsed.get("task") or "").strip()
            if not agent:
                msg.next = NextMessageModel(next_agent="user", message="监管者路由失败：未指定子智能体")
            else:
                if action_str == SupervisorActionType.NEXT_STEP:
                    self._mark_next_step(agent, self._session_state)
                # CALL_AGENT + agent=planner 等价于原来的 CALL_PLANNER：重置 plan + history
                if action_str == SupervisorActionType.CALL_AGENT and agent == "planner":
                    if self._session_state is not None:
                        self._session_state.supervisor_ctx["plan"] = None
                        self._session_state.supervisor_ctx["history"] = []
                msg.next = NextMessageModel(
                    next_agent=agent,
                    message=task or msg.message.get("task", ""),
                )

        else:
            msg.next = NextMessageModel(next_agent="user", message=f"未知决策动作: {action_str}")

        msg.success = True  # 一轮 run 内 Supervisor 只做一次决策
        return msg

    # ── run() 开头跨 run 状态消费 ──

    def _consume_planner_plan(self, msg: AgentMessage, session_state) -> None:
        """Planner 回传 plan → 存 supervisor_ctx.plan。"""
        task_raw = msg.message.get("task", "") or ""
        try:
            parsed = _json.loads(task_raw)
            if isinstance(parsed, dict) and parsed.get("steps"):
                session_state.supervisor_ctx["plan"] = parsed
                session_state.supervisor_ctx["history"] = []
                log.info("Supervisor: 已存 plan | goal={} | steps={}",
                         parsed.get("goal", ""), len(parsed.get("steps", [])))
                return
        except Exception:
            pass
        log.warning("Supervisor: Planner 回传无法解析为 plan，跳过")

    def _consume_child_result(self, msg: AgentMessage, caller: str, session_state) -> None:
        """子 agent 回传结果 → 追加到 history。"""
        task = msg.message.get("task", "") or ""
        metadata = msg.message.get("metadata") or {}
        summary = task[:300] if isinstance(task, str) else str(task)[:300]

        history: list = session_state.supervisor_ctx.setdefault("history", [])
        history.append({
            "agent": caller,
            "task": summary,
            "success": True,
            "result": metadata or summary,
        })
        log.info("Supervisor: 追加 history | from={} | total={}", caller, len(history))

    def _mark_next_step(self, agent: str, session_state) -> None:
        """决定 NEXT_STEP 时，标记当前 step（方便审查 prompt 显示 ✓）。"""
        plan = session_state.supervisor_ctx.get("plan") or {}
        steps = plan.get("steps", [])
        done_ids = set(plan.get("_done_ids") or [])
        for s in steps:
            if (s.get("agent") or "") == agent and s.get("step_id") not in done_ids:
                done_ids.add(s.get("step_id"))
                plan["_done_ids"] = list(done_ids)
                session_state.supervisor_ctx["plan"] = plan
                break

    # ── prompt 模板辅助 ──

    def _agents_list_desc(self) -> str:
        lines = []
        for spec in get_app().agents.all_specs():
            if spec.role in ("supervisor", "intent_recognition"):
                continue
            lines.append(f"{spec.name} - {spec.desc}")
        return "\n".join(lines) or "（暂无可用子智能体）"

    def _format_plan(self, plan: dict | None) -> str:
        if not plan:
            return "（暂无计划）"
        goal = plan.get("goal", "")
        steps = plan.get("steps", [])
        done_ids = set(plan.get("_done_ids") or [])
        lines = [f"goal: {goal}"]
        for s in sorted(steps, key=lambda x: x.get("step_id", 0)):
            sid = s.get("step_id", "?")
            status = "✓" if sid in done_ids else "pending"
            lines.append(f"  step{sid} [{status}] {s.get('agent')}: {s.get('task')}")
        return "\n".join(lines)

    def _format_history(self, history: list | None) -> str:
        if not history:
            return "（暂无执行历史）"
        return "\n".join(
            f"  [{h.get('agent')}] success={h.get('success')} | {str(h.get('result', ''))[:200]}"
            for h in history
        )
