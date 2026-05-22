from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from bot.agent.runtime import AgentRuntime, AgentState
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import ToolRegistry
from bot.utils.log_utils import log
from shared.constants import get_code_dir
from shared.schema.ai_service import AiServiceGenerateRequest


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

    def get_tools(self, tools_handler: ToolRegistry | None = None) -> list[Any]:
        handler = tools_handler or ToolRegistry()
        child_tools = [tool for tool in handler.tools if tool.name not in DEFAULT_CHILD_EXCLUDED_TOOLS]
        if not self.allowed_tools:
            return child_tools

        allowed = {str(name).strip() for name in self.allowed_tools if str(name).strip()}
        return [tool for tool in child_tools if tool.name in allowed]


class SubagentRunner:
    async def run(self, subagent_context: SubagentContext) -> dict[str, Any]:
        tools_handler = ToolRegistry()
        tools_handler.tools = subagent_context.get_tools(tools_handler)

        app_code_dir = get_code_dir(subagent_context.app_id)
        app_code_dir.mkdir(parents=True, exist_ok=True)

        runtime = AgentRuntime(
            tool_executor=ToolExecutor(tools_handler, safe_paths=[str(app_code_dir)]),
        )
        runtime.max_tool_iterations = max(1, int(subagent_context.max_turns or 15))

        request = AiServiceGenerateRequest(
            appId=int(subagent_context.app_id) if str(subagent_context.app_id).isdigit() else 0,
            userId="",
            sessionId=subagent_context.parent_session_id or f"subagent-session-{uuid4().hex[:8]}",
            traceId=subagent_context.trace_id or uuid4().hex,
            requestId=subagent_context.parent_turn_id or f"subagent-request-{uuid4().hex[:8]}",
            message=subagent_context.prompt,
            clientVersion="subagent",
            metadata={
                "plan_state_locked": bool(subagent_context.plan_summary),
                "plan_summary": subagent_context.plan_summary,
                "subagent_description": subagent_context.description,
                "subagent_parent_session_id": subagent_context.parent_session_id,
                "subagent_parent_turn_id": subagent_context.parent_turn_id,
                "is_subagent": True,
            },
        )

        streamed_chunks: list[str] = []
        last_error = ""
        final_state = AgentState.COMPLETED

        async for event in runtime.submit_request(request):
            final_state = event.state
            if event.event_type == "LLM_Response_Chunk" and event.data:
                streamed_chunks.append(str(event.data))
                continue
            if event.event_type == "Error":
                last_error = str(event.data or "subagent execution failed")

        summary = self._extract_summary(streamed_chunks, last_error)
        return {
            "success": final_state != AgentState.FAILED,
            "data": summary,
            "details": {
                "state": final_state.value,
                "session_id": request.session_id,
                "tool_names": [tool.name for tool in tools_handler.tools],
            },
        }

    @staticmethod
    def _extract_summary(streamed_chunks: list[str], last_error: str) -> str:
        if streamed_chunks:
            return "".join(streamed_chunks)
        if last_error:
            return f"Subagent failed: {last_error}"

        log.warning("Subagent completed without a textual summary")
        return "(subagent completed without a textual summary)"