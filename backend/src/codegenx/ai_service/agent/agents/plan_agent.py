import json as _json

from pydantic import BaseModel
from typing import Any
from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.agent.agents.agent import AgentMessage, NextMessageModel
from shared import log


class PlanStep(BaseModel):
    """任务规划中的单个步骤。"""
    step_id: int = 0                 # 步骤序号
    agent: str = ""                  # 执行该步骤的 Agent 名称（必须来自可用 Agent 列表）
    task: str = ""                   # 该步骤目标（需清晰完整、可独立完成）
    resources: list[str] = []        # 该步骤所需资源地址 / 名称
    depends_on: list[int] = []       # 依赖的前置 step_id（无依赖为空）


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
        goal="理解下面每一个智能体及其能力，依托提供的资源，通过协调各个智能体来解决用户问题。"
             "请利用大模型自身的知识与理解能力，领会用户问题的意图与目标，生成一份**无需用户协助、可依靠多个智能体协作完成**的任务方案。",
        desc="复杂任务制定计划，需要多次调用agent的任务编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "任务规划的每一步都应当服务于推进用户目标的解决。不要生成无意义的任务步骤；确保每一步目标清晰、内容完整。",
            "请注意任务规划中各步骤的依赖关系与逻辑。对于存在依赖的步骤，需要考量其依赖的数据，以及该数据能否基于当前目标获取。若无法获取，则需在目标中注明需要先生成该依赖数据。",
            "每一步都必须是可独立完成的目标，保证逻辑与信息完整。避免目标模糊的步骤，例如 “分析检索到的问题数据”—— 这类描述没有说明需要分析哪些具体内容。",
            "仅可使用上文提到的智能体，并且只调用智能体能力中必要的部分。严格按照各智能体描述的能力范围与限制，将其分配到合适的步骤中。智能体可以重复调用。",
            "根据用户目标的实际需求，利用给定资源辅助生成规划步骤，不要使用不必要的资源。"
            "理想情况下，每一步仅使用一类资源完成一个子目标。如果当前目标可以拆分为多个同类型子任务，可创建相互独立的并行任务。"
            "数据资源可由对应的智能体直接加载使用，无需考虑数据加载链路相关问题。",
            "尽量合并存在顺序依赖的连续步骤。如果用户目标不需要拆分，可以创建单步任务，任务内容即为用户目标。",
            "仔细复核任务规划，确保完整覆盖用户问题涉及的全部信息，并且最终能够达成目标。确认每一步都附带必要的资源信息，例如资源地址、资源名称等。"
                    ],
        resource_prompt="",
        expand_prompt="可用的Agent如下：\n {{agents_list_desc}}",
        examples="",
        out_schema="""请严格按如下JSON格式输出任务规划方案，确保输出仅有JSON、无多余文字，可被python json.loads加载：
{
    "goal": "对用户目标的简要概述",
    "steps": [
        {
            "step_id": 1,
            "agent": "执行该步骤的Agent名称（必须来自上方可用Agent列表）",
            "task": "该步骤的目标描述，需清晰完整、可独立完成",
            "resources": ["该步骤所需的资源地址或名称"],
            "depends_on": [依赖的前置step_id，无依赖则填空数组]
        }
    ]
}""",
    )

    def __init__(self):
        super().__init__()
        # 注意：这里是实例而非类，act 是实例方法
        self.actions = [PlanAction()]

    def _prepare_prompt_params(self) -> dict:
        selected = []
        for spec in get_app().agents.all_specs():
            if spec.role in ["planner", "intent_recognition"]:
                continue
            else:
                selected.append(spec)
        return {
            "agents_list_desc": "\n".join([spec.name + "-" + spec.desc for spec in selected]),
        }

    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        await super()._prepare_agent(session_state)
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")
        tools = self.agent_config.tools or []
        self.tools = await get_app().tool_registry.build_tools_with_name(tools)

        self.skill = None
        self.memory = None
        # 组合
        self.prompt = None

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """观察 PlanAction 结果，决定重试还是返回给调用方。

        Planner 是纯规划器：think → PlanAction 产出 plan → 序列化 JSON 返回调用方。
        ReAct 流程（通常 1 步）：
          step 1  think → LLM 生成 PlanOutput JSON
                 act   → PlanAction 解析 + 校验
                 ── 成功 → next_agent=msg.sender（supervisor），message.task=plan JSON
                 ── 失败 → messages 追加错误提示，继续 try（最多 max_steps 次）
        """
        caller = msg.sender or "user"
        msg.output = act_output.model_dump(mode="json")

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

        # ── 规划成功：序列化 JSON 返回给调用方 ──
        plan_dict = act_output.output  # dict: {goal, steps}
        try:
            plan_json = _json.dumps(plan_dict, ensure_ascii=False)
        except Exception:
            plan_json = str(plan_dict)

        log.info("Planner observation: 规划成功，返回给 {} | goal={} | steps={}",
                 caller, plan_dict.get("goal", ""), len(plan_dict.get("steps", [])))
        msg.next = NextMessageModel(
            next_agent=caller,
            message=plan_json,  # Supervisor._consume_planner_plan 从 msg.message["task"] 解析
        )
        msg.success = True
        return msg