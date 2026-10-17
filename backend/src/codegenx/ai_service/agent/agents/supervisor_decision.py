"""Supervisor 决策引擎 —— 路由/审查双模式模板与决策协议（从 intention_agent.py 拆分）。

- SupervisorActionType / SupervisorDecision / SupervisorAction 为 LLM 决策协议：
  think 输出 JSON → SupervisorAction 解析为 SupervisorDecision（含字段校验）。
- SupervisorDecisionEngine 持有 route/review 双模式 PromptConfig 与智能体清单组装，
  不持有运行时状态（每次 run 即取即用）。
"""

from enum import Enum

from pydantic import BaseModel

from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app


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
    direct_reply: bool = False         # call_executor 可选：单任务免汇总派发，执行 agent 完成即直答用户


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


class SupervisorDecisionEngine:
    """决策引擎：route/review 双模式 profile 与智能体清单（无运行时状态）。

    - route_profile：sender == user 时的路由决策（DIRECT_ANSWER / CALL_EXECUTOR / CALL_PLANNER）
    - review_profile：子 agent 回传后的审查决策（CALL_EXECUTOR / NEXT_STEP / FINISH）
    """

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
            "direct_reply 判据（仅 call_executor 生效）：恰好只需 1 个子任务、且其执行结果无需任何跨任务对比/汇总/换算即可作为最终答复时设 true（执行智能体完成即直接答复用户，省去审查环节）；"
            "需要 2 个及以上子任务、或需对结果做二次加工（对比、差值、汇总、多源合并）时必须 false（执行完回传由你汇总）。",
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
    "answer": "direct_answer 时填面向用户的答复；其余填空字符串",
    "direct_reply": "仅 call_executor 时填写：true=单任务且无需汇总，执行智能体完成即直接答复用户；false=执行完回传给你审查汇总。direct_answer / call_planner 时填 false"
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
            "若本轮输入中最新回传结果已包含完成任务所需的数据或答案，必须 FINISH 并在 answer 中基于该结果汇总答复，禁止以格式不符等理由对同一智能体重复派发相同或相似任务。",
            "计划中 executor_agent 为 supervisor 的汇总类步骤由你自己完成，在 FINISH 的 answer 中直接给出结果，禁止把 supervisor 作为派发目标。",
            "只输出指定 JSON，禁止额外文字解释，保证可被 python json.loads 正常解析。",
        ],
        resource_prompt="",
        expand_prompt="可用的执行智能体如下：\n{{agents_list_desc}}\n\n任务看板：\n{{board_desc}}\n\n（子 agent 历史回传结果见对话历史消息，最新回传结果见本轮输入）",
        examples="",
        out_schema="""请严格按如下 JSON 格式输出：
{
    "action": "三选一：call_executor / next_step / finish",
    "agent": "call_executor / next_step 时填智能体 name；finish 填空字符串",
    "task": "call_executor / next_step 时填任务描述；finish 填空字符串",
    "answer": "finish 时填面向用户的汇总答复；其余填空字符串"
}""",
    )

    @staticmethod
    def agents_list_desc(include_planner: bool = True) -> str:
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
