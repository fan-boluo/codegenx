import asyncio
from typing import Any

from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log


class TodoTool(BaseTool):
    @property
    def name(self) -> str:
        return "todo"

    @property
    def label(self) -> str:
        return "planner"

    @property
    def description(self) -> str:
        return "Rewrite the current session plan for multi-step work. Keep exactly one item in_progress when there are multiple steps."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Optional present-progress label for the in-progress step.",
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["items"],
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        planner = params.get("planner")
        items = params.get("items", [])
        if planner is None:
            return ToolResult(success=False, data="Planner is not available for todo updates.")

        try:
            plan_state = planner.update_plan(items)
            return ToolResult(
                success=True,
                data=plan_state,

            )
        except Exception as exc:
            log.error(f"Todo update failed: {exc}")
            return ToolResult(success=False, message=f"Error: {exc}")