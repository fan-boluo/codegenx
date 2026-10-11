from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.system_app import get_app
from typing import Optional

class PlannerAgent(BaseReActAgent):
    """ 计划 """

    @property
    def name(self) -> str:
        return "planner"

    @property
    def desc(self) -> Optional[str]:
        return "计划agent"

    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        super()._prepare_agent(session_state)

        tools = self.agent_config.tool_registry
        self.tools = await get_app().tool_registry.build_tools_with_name(tools)

        self.skill = None
        self.memory = None
        # 组合
        self.prompt = None


