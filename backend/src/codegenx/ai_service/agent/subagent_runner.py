from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from codegenx.ai_service.agent.runtime import AgentRuntime, AgentState
from codegenx.ai_service.agent.tool_executor import ToolExecutor
from codegenx.ai_service.agent.tool_handler import ToolRegistry
from shared import log
from shared.constants import get_code_dir
from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest


DEFAULT_CHILD_EXCLUDED_TOOLS = {"subagent", "compact"}


@dataclass
class SubagentContext:
    prompt: str
    app_id: str = "main"
    user_id: str = ""
    description: str = ""
    agent_name: str = ""                   # P4 §10.6：目标智能体（空=默认）
    max_turns: int = 15
    allowed_tools: list[str] | None = None
    plan_summary: str = ""
    trace_id: str = ""
    parent_session_id: str = ""
    parent_turn_id: str = ""


class SubagentRunner:
    async def run(self, subagent_context: SubagentContext) -> dict[str, Any]:
        # P3 复用（docs/SystemApp架构设计.md §6）：全局注册表产出过滤子视图，
        # 不再 new ToolRegistry() 触发目录重扫；
        # P4 §10.6：从注册表取 spec → 工具 allowlist / 限额 / persona（经 metadata 下发）
        from codegenx.ai_service.system_app import get_app

        app = get_app()
        spec = app.agents.get(subagent_context.agent_name) if app.agents is not None else None

        tools_handler = app.tools.child_view(
            excluded=DEFAULT_CHILD_EXCLUDED_TOOLS,
            allowed=subagent_context.allowed_tools or (spec.tools if spec is not None else None),
        )

        app_code_dir = get_code_dir(subagent_context.user_id or "main", subagent_context.app_id)
        app_code_dir.mkdir(parents=True, exist_ok=True)

        runtime = AgentRuntime(
            tool_executor=ToolExecutor(tools_handler),
        )
        runtime.max_tool_iterations = max(1, int(subagent_context.max_turns or 15))
        # P4 §10.3：spec.limits 覆盖限额（max_steps / temperature 沿用 AgentConfig 字段）
        if spec is not None and spec.limits is not None:
            if getattr(spec.limits, "max_steps", None):
                runtime.max_steps = int(spec.limits.max_steps)

        app_code_dir = get_code_dir(subagent_context.user_id or "main", subagent_context.app_id)
        app_code_dir.mkdir(parents=True, exist_ok=True)

        runtime = AgentRuntime(
            tool_executor=ToolExecutor(tools_handler),
        )
        runtime.max_tool_iterations = max(1, int(subagent_context.max_turns or 15))

        request = AiServiceGenerateRequest(
            appId=int(subagent_context.app_id) if str(subagent_context.app_id).isdigit() else 0,
            userId=str(subagent_context.user_id or ""),
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
                # P4 §10.6：目标智能体随请求下发，init_session_objects 据此设置 session.agent_name
                "agent_name": subagent_context.agent_name,
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