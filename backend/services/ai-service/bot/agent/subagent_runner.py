from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.agent.runtime import AgentRuntime, AgentState, TurnContext
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import ToolsHandler
from bot.utils.log_utils import log
from shared.constants import get_bot_code_dir


DEFAULT_CHILD_EXCLUDED_TOOLS = {"task", "compact"}


@dataclass
class SubagentContext:
    prompt: str
    app_id: str = "main"
    description: str = ""
    max_turns: int = 15
    allowed_tools: list[str] | None = None
    plan_summary: str = ""
    trace_id: str = ""
    parent_session_id: str = ""
    parent_turn_id: str = ""

    def get_tools(self, tools_handler: ToolsHandler | None = None) -> list[Any]:
        handler = tools_handler or ToolsHandler()
        child_tools = [tool for tool in handler.tools if tool.name not in DEFAULT_CHILD_EXCLUDED_TOOLS]
        if not self.allowed_tools:
            return child_tools

        allowed = {str(name).strip() for name in self.allowed_tools if str(name).strip()}
        return [tool for tool in child_tools if tool.name in allowed]


class SubagentRunner:
    async def run(self, subagent_context: SubagentContext) -> dict[str, Any]:
        tools_handler = ToolsHandler()
        tools_handler.tools = subagent_context.get_tools(tools_handler)

        app_code_dir = get_bot_code_dir(subagent_context.app_id)
        app_code_dir.mkdir(parents=True, exist_ok=True)

        runtime = AgentRuntime(
            app_id=subagent_context.app_id,
            tool_executor=ToolExecutor(tools_handler, safe_paths=[str(app_code_dir)]),
        )
        runtime.max_tool_iterations = max(1, int(subagent_context.max_turns or 15))

        child_context = TurnContext(
            app_id=subagent_context.app_id,
            session_id=subagent_context.parent_session_id,
            turn_id=subagent_context.parent_turn_id,
            user_input=subagent_context.prompt,
            plan_state=subagent_context.plan_summary,
            history=[],
            metadata={
                "trace_id": subagent_context.trace_id,
                "request_id": subagent_context.parent_turn_id,
                "plan_state_locked": bool(subagent_context.plan_summary),
                "subagent_description": subagent_context.description,
                "subagent_parent_session_id": subagent_context.parent_session_id,
                "subagent_parent_turn_id": subagent_context.parent_turn_id,
                "is_subagent": True,
            },
        )

        streamed_chunks: list[str] = []
        last_error = ""

        async for event in runtime.run_turn(child_context):
            if event.event_type == "LLM_Response_Chunk" and event.data:
                streamed_chunks.append(str(event.data))
                continue
            if event.event_type == "Error":
                last_error = str(event.data or "subagent execution failed")

        summary = self._extract_summary(child_context, streamed_chunks, last_error)
        return {
            "success": child_context.state != AgentState.FAILED,
            "data": summary,
            "details": {
                "state": child_context.state.value,
                "session_id": child_context.session_id,
                "tool_names": [tool.name for tool in tools_handler.tools],
            },
        }

    @staticmethod
    def _extract_summary(context: TurnContext, streamed_chunks: list[str], last_error: str) -> str:
        for message in reversed(context.history):
            if message.get("role") != "assistant":
                continue
            content = str(message.get("content", "") or "").strip()
            if content:
                return content

        if streamed_chunks:
            return "".join(streamed_chunks)
        if last_error:
            return f"Subagent failed: {last_error}"

        log.warning("Subagent completed without a textual summary")
        return "(subagent completed without a textual summary)"