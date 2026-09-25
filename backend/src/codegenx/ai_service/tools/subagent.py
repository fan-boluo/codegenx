import asyncio
from typing import Any

from codegenx.ai_service.agent.subagent_runner import SubagentContext, SubagentRunner
from codegenx.ai_service.tools.base import BaseTool, ToolResult


class SubagentTaskTool(BaseTool):
    @property
    def name(self) -> str:
        return "subagent"

    @property
    def label(self) -> str:
        return "subagent"

    @property
    def description(self) -> str:
        return "Spawn a subagent with fresh context. It shares the filesystem but not conversation history."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "Short description of the task",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Optional target agent name from the agent registry "
                                   "(e.g. data_explore). Omit for the default agent.",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Maximum tool-use turns allowed for the subagent (default: 15).",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of tool names to restrict the subagent to.",
                },
            },
            "required": ["prompt"],
        }

    async def execute(
        self,
        params: dict,
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        allowed_tools = params.get("allowed_tools")
        subagent_context = SubagentContext(
            prompt=str(params.get("prompt", "") or "").strip(),
            app_id=str(params.get("app_id", "main") or "main"),
            user_id=str(params.get("user_id", "") or ""),
            description=str(params.get("description", "") or ""),
            # P4 §10.6：指定目标智能体（空=默认智能体）
            agent_name=str(params.get("agent_name", "") or ""),
            max_turns=int(params.get("max_turns", 15) or 15),
            allowed_tools=list(allowed_tools) if isinstance(allowed_tools, list) else None,
            plan_summary=str(params.get("plan_summary", "") or ""),
            trace_id=str(params.get("trace_id", "") or ""),
            parent_session_id=str(params.get("parent_session_id", "") or ""),
            parent_turn_id=str(params.get("parent_turn_id", "") or ""),
        )

        runner = SubagentRunner()
        result = await runner.run(subagent_context)
        return ToolResult(success=result["success"],
                          data=result["data"]+result.get("details"),
                          message=str(result),
                          render=f"子代理: {subagent_context.description or subagent_context.prompt[:30]}")