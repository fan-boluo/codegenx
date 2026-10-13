from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from typing import Optional

from shared import log


class PlannerAgent(BaseReActAgent):
    """ 计划 """
    name = "planner"

    profile: PromptConfig = PromptConfig(
        role="planner",
        name="planner",
        goal="理解下面每一个智能体及其能力，依托提供的资源，通过协调各个智能体来解决用户问题。"
             "请利用大模型自身的知识与理解能力，领会用户问题的意图与目标，生成一份**无需用户协助、可依靠多个智能体协作完成**的任务方案。",
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
        out_schema="",
    )

    def _prepare_prompt_params(self) -> dict:
        selected = []
        for spec in get_app().agents._all_specs():
            if spec.role in ["planner","intent_recognition"] :
                continue
            else:
                selected.append(spec)
        return {
            "agents_list_desc":"/n".join([spec.name+"-"+spec.desc for spec in selected ]),
        }

    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        super()._prepare_agent(session_state)
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")
        tools = self.agent_config.tools or []
        self.tools = await get_app().tool_registry.build_tools_with_name(tools)

        self.skill = None
        self.memory = None
        # 组合
        self.prompt = None


