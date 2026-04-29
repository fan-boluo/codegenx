import asyncio
from typing import Any

from bot.skill.skill_loader import SkillLoader
from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log


class LoadSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def label(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "Load the full body of a named skill into the current context. Use after the skill catalog indicates a relevant skill."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name from the available skill catalog."}
            },
            "required": ["name"],
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        name = str(params.get("name", "")).strip()
        try:
            skill_text = SkillLoader().load_full_text(name)
            return ToolResult(
                success=True,
                data=skill_text,
                details={"name": name},
            )
        except Exception as exc:
            log.error(f"Failed to load skill {name}: {exc}")
            return ToolResult(success=False, data=f"Error loading skill: {exc}")