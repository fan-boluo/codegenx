import asyncio
from typing import Any

from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log

class CompactTool(BaseTool):
    @property
    def name(self) -> str:
        return "compact"

    @property
    def label(self) -> str:
        return "context"

    @property
    def description(self) -> str:
        return "Summarize earlier conversation so work can continue in a smaller context."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"focus": {"type": "string"}},
        }

    async def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
    ) -> ToolResult:
        focus = params.get("focus")
        context = params.get("context")
        context_compactor = params.get("context_compactor")
        log.info(f"Compact tool invoked with focus: {focus}")

        if context is None or context_compactor is None:
            return ToolResult(
                success=False,
                data="Compact tool requires an active runtime context.",
            )

        await context_compactor.compact_history(
            context,
            focus=focus,
            reason="tool-requested",
        )

        compaction_state = context.metadata.get("context_compaction", {})
        transcript_path = str(compaction_state.get("last_transcript_path", "") or "")
        return ToolResult(
            success=True,
            data="Context compacted for continued work.",
            details={
                "focus": focus,
                "summary": compaction_state.get("last_summary", ""),
                "transcript_path": transcript_path,
            },
        )
