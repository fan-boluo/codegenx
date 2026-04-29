import asyncio

from bot.tools.base import BaseTool, ToolResult


class WebSearchTool(BaseTool):
    label = "search"
    name = "web_search"
    description = "搜索互联网上的页面"
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "要搜索的关键字"
            },
            "url": {
                "type": "string",
                "description": "要搜索的网址"
            }
        },

        "required": ["keywords","url"]
    }

    def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
            # on_update: Callable[[AgentToolResult[TDetails]], None] | None = None,
    ) -> ToolResult:
        pass