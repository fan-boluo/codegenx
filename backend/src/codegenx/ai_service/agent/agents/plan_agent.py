import json as _json

from pydantic import BaseModel
from typing import Any
from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.agents.supervisor_board import TaskBoardCoordinator
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.agent.agents.agent import AgentMessage
from shared import log


class PlanStep(BaseModel):
    """任务规划中的单个步骤（纯任务分解：不指派执行者，由 lead agent 逐任务决定）。"""
    step_id: int = 0                 # 步骤序号
    task: str = ""                   # 该步骤目标（需清晰完整、可独立完成）
    depends_on: list[int] = []       # 依赖的前置 step_id（无依赖为空）
    resource: list[str] = []


class PlanOutput(BaseModel):
    """任务规划输出（LLM 返回内容的解析结果）。"""
    goal: str = ""                   # 用户目标概述
    steps: list[PlanStep] = []       # 任务步骤列表


class PlanAction(Action[PlanOutput]):
    """任务规划动作：解析 LLM 返回的 JSON 为 PlanOutput，解析失败或步骤为空则中断动作链。"""

    async def act(self, ai_message: ActOutput, session_state=None, **kwargs) -> ActOutput:
        parsed = self.parse_ai_message(ai_message, PlanOutput)
        if parsed is None:
            return ActOutput(success=False, message="任务规划结果解析失败（非合法 JSON）")
        if not parsed.steps:
            return ActOutput(success=False, message="任务规划结果为空（steps 为空列表）")
        # 验证agent是否存在
        log.debug("【PlanAction】 结果：{}", parsed.model_dump())
        return ActOutput(success=True, output=parsed.model_dump(mode="json"))


class PlannerAgent(BaseReActAgent):
    """ 计划 """
    name = "planner"

    profile: PromptConfig = PromptConfig(
        role="planner",
        name="planner",
        goal="理解系统的数据处理能力，将用户问题拆解为一份**无需用户协助、可逐步执行完成的任务分解方案**。",
        desc="复杂任务制定计划，需要多步执行的任务编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "任务规划的每一步都应当服务于推进用户目标的解决。不要生成无意义的任务步骤；确保每一步目标清晰、内容完整。",
            "task 中禁止出现「步骤N」「上一步」「结合前序/步骤X的结果」「与步骤X口径一致」等引用表述；"
            "该步骤执行所需的全部信息（文件名、库名、表名、字段口径、筛选条件、统计口径）必须直接写进 task。",
            "**每一步只做一件事**：取数类步骤只负责产出其数据源内的统计结果；"
            "相互独立的子任务（如分别从不同数据源取数）拆分为相互独立的并行步骤，depends_on 保持为空数组。",
            "根据用户目标的实际需求，利用给定资源辅助生成规划步骤，不要使用不必要的资源。"
            "理想情况下，每一步仅使用一类资源完成一个子目标。如果当前目标可以拆分为多个同类型子任务，可创建相互独立的并行任务。"
            "数据资源可由对应的智能体直接加载使用，无需考虑数据加载链路相关问题。",
            "尽量合并存在顺序依赖的连续步骤。如果用户目标不需要拆分，可以创建单步任务，任务内容即为用户目标。",
            "仔细复核任务规划：逐一检查每个 task，确认它不引用其它步骤、不含汇总要求，仅凭 task 文本即可独立完成。",
        ],
        resource_prompt="",
        expand_prompt="",
        examples="",
        out_schema="""请严格按如下JSON格式输出任务规划方案，确保输出仅有JSON、无多余文字，可被python json.loads加载：
{
    "goal": "对用户目标的简要概述",
    "steps": [
        {
            "step_id": 1,
            "task": "该步骤的目标描述；必须完全自包含——执行智能体仅凭此文本即可独立完成，所需信息全部写明，禁止引用其它步骤或要求跨步骤汇总",
            "resource":"该步骤需要的资源，比如涉及的文件，数据库，表等",
            "depends_on": [依赖的前置step_id，无依赖则填空数组]
        }
    ]
}""",
    )

    def __init__(self):
        super().__init__()
        # 注意：这里是实例而非类，act 是实例方法
        self.actions = [PlanAction()]
        # 看板协调器：计划产出后由 planner 自行落板（不再回流 supervisor 侧消费）
        self.board = TaskBoardCoordinator()
        # run 级会话状态（_prepare_agent 存入，observation 落板时使用）
        self._session_state = None

    # def _prepare_prompt_params(self) -> dict:
    #     # 智能体清单仅作系统能力参考（拆解任务不超出能力边界）；
    #     # 计划不指派执行者——每个任务怎么执行由 supervisor 决定
    #     selected = []
    #     for spec in get_app().agents.all_specs():
    #         if spec.role in ["planner", "intent_recognition", "supervisor"]:
    #             continue
    #         else:
    #             selected.append(spec)
    #     return {
    #         "agents_list_desc": "\n".join([spec.name + "-" + spec.desc for spec in selected]),
    #     }

    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        await super()._prepare_agent(session_state)
        # 落板所需会话状态（observation 阶段 register_plan 使用）
        self._session_state = session_state
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")
        tools = self.agent_config.tools or []
        self.tools = await get_app().tool_registry.build_tools_with_name(tools)

        self.skill = None
        self.memory = None
        # 组合
        self.prompt = None

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """观察 PlanAction 结果，决定重试还是登记看板后返回调用方。

        Planner 是规划器 + 登记器（B/C 阶段：计划不再回流 lead agent 落板）：
          think → LLM 生成 PlanOutput JSON
          act   → PlanAction 解析 + 校验
          ── 失败 → messages 追加错误提示，继续 try（最多 max_steps 次）
          ── 成功 → board.register_plan 自行落任务看板（DAG 依赖一并写入）
                    → 回传登记摘要（goal/总数/就绪数），lead agent 审查看板
                      并自主派发就绪任务（无确定性派发状态机）。
        """
        caller = msg.sender or "user"

        if not act_output.success:
            # ── 规划失败：让 LLM 重试（最多 max_steps 次） ──
            log.warning("Planner observation: 规划失败，让 LLM 重试：{}", act_output.message)
            self._messages.append({
                "role": "user",
                "content": (
                    f"上一次生成的任务规划无效，请重新输出正确的 JSON。\n"
                    f"失败原因：{act_output.message}\n"
                    f"请确保 goal 和 steps 完整，steps 不能为空列表。"
                ),
            })
            msg.success = False
            return msg

        # ── 规划成功：planner 自行登记任务看板，回传登记摘要 ──
        plan_dict = act_output.output  # dict: {goal, steps}
        summary = self.board.register_plan(self._session_state, plan_dict)
        msg.sender = self.agent_name
        msg.recipient = caller
        msg.task_id = None
        msg.success = True
        if summary is None:
            # 落板失败（看板服务未就绪/上下文缺失）：回传计划原文供 lead agent 观测处置
            log.warning("Planner: 计划落板失败，回传计划原文 | goal={}", str(plan_dict.get("goal", ""))[:80])
            msg.result = (
                "计划已生成，但任务看板登记失败（计划原文如下，请基于对话继续处置）：\n"
                + _json.dumps(plan_dict, ensure_ascii=False)
            )
            return msg
        log.info("Planner: 计划已登记并回传 {} | goal={} | tasks={} | ready={}",
                 caller, summary["goal"][:80], summary["total"], summary["ready"])
        msg.result = (
            f"已制定任务分解计划并登记至任务看板：goal={summary['goal']}。"
            f"共 {summary['total']} 个任务，当前 {summary['ready']} 个已就绪可执行。"
            "请审查任务看板，逐任务决定由自己执行还是派发给子智能体。"
        )
        return msg