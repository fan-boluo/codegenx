"""
记忆工具
"""
import asyncio
from typing import Any

from bot.memory.manager import MemoryManager
from bot.memory.schema import MemorySearchResult, MemoryType
from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log


def _get_memory_manager(app_id: str | None = None) -> MemoryManager:
    return MemoryManager(app_id or "main")


class MemorySearchTool(BaseTool):
    """
    搜索记忆向量库
    """

    @property
    def label(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Mandatory recall step: semantically search MEMORY.md + memory/*.md "
            "(and optional session transcripts) before answering questions about "
            "prior work, decisions, dates, people, preferences, or todos; "
            "returns top snippets with path + lines."
            )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of results (default: 10)"
                },
                "minScore": {
                    "type": "number",
                    "description": "Minimum score threshold (0-1, default: 0)"
                }
            },
            "required": ["query"]
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        query = params['query']
        limit = params.get("limit")
        min_score = float(params.get("minScore", 0) or 0)
        memory_manager = _get_memory_manager(params.get("app_id"))
        try:
            results = await memory_manager.search(query, limit=limit, use_hybrid=True)
            if min_score > 0:
                results = [result for result in results if result.score >= min_score]
            formatted_results = self._format_results(results)

            return ToolResult(
                success=True,
                data=formatted_results,
                details={
                    "results": [self._serialize_result(r) for r in results]
                }
            )

        except Exception as e:
            log.error(f"Memory search failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                data="",
                details={"error": str(e)}
            )

    def _format_results(self, results: list[MemorySearchResult]) -> str:
        """Format search results for display."""
        if not results:
            return "No results found."

        lines = []
        for i, result in enumerate(results, 1):
            citation = self._format_citation(result)
            lines.append(f"{i}. {citation}")
            lines.append(f"   Score: {result.score:.2f}")
            lines.append(f"   {result.snippet}")
            lines.append("")

        return "\n".join(lines)

    def _serialize_result(self, result: MemorySearchResult) -> dict[str, Any]:
        return {
            "id": result.id,
            "path": result.path,
            "source": result.source.value if hasattr(result.source, "value") else str(result.source),
            "score": result.score,
            "snippet": result.snippet,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "text": result.text,
        }

    def _format_citation(self, result: MemorySearchResult) -> str:
        """Format citation string (matches TS formatCitation)."""
        if result.start_line == result.end_line:
            line_range = f"#L{result.start_line}"
        else:
            line_range = f"#L{result.start_line}-L{result.end_line}"

        return f"{result.path}{line_range}"


class MemoryGetTool(BaseTool):
    """
    直接搜索记忆原文件
    """

    @property
    def label(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "memory_get"

    @property
    def description(self) -> str:
        return (
            "Safe snippet read from MEMORY.md or memory/*.md with optional from/lines; "
            "use after memory_search to pull only the needed lines and keep context small."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path (e.g., MEMORY.md or memory/project.md)"
                },
                "from": {
                    "type": "number",
                    "description": "Starting line number (1-indexed, optional)"
                },
                "lines": {
                    "type": "number",
                    "description": "Number of lines to read (optional)"
                }
            },
            "required": ["path"]
        }


    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        rel_path = params.get("path", "")
        from_line = params.get("from")
        lines = params.get("lines")
        memory_manager = _get_memory_manager(params.get("app_id"))
        try:

            result = await memory_manager.read_file(params)

            text = result.get("text", "")
            error = result.get("error")

            if error:
                return ToolResult(
                    success=False,
                    data="",
                    details={"error": error}
                )

            return ToolResult(
                success=True,
                data=text,
                details={
                    "path": rel_path,
                    "from": from_line,
                    "lines": lines
                }
            )

        except Exception as e:
            log.error(f"Memory read failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                data="",
                details={"error": str(e)}
            )


class MemoryWriteShortTermTool(BaseTool):
    label = "memory"
    name = "write_short_term"
    description = "写入短期记忆，用于记录当前对话、观察、临时事实，每天自动归档"
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记忆的内容"}
        },
        "required": ["content"]
    }


    async def execute(self, params: dict, signal=None) -> ToolResult:
        content = params["content"]
        session_id = params.get("session_id", "default")
        memory_manager = _get_memory_manager(params.get("app_id"))
        try:
            await memory_manager.write_memory(content, MemoryType.SHORT, session_id=session_id)
            return ToolResult(
                success=True,
                data="短期记忆已保存",
                details={"app_id": params.get("app_id", "main"), "session_id": session_id, "turn_id": params.get("turn_id", "")}
            )
        except Exception as e:
            log.error(f"调用工具 write_short_term 失败 :{e}")
            return ToolResult(
                success=False,
                data=str(e)
            )


class MemoryWriteLongTermTool(BaseTool):
    label = "memory"
    name = "write_long_term"
    description = "写入长期记忆，用于永久保存用户偏好、重要规则、关键知识、核心目标"
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要长期保存的内容"},
            "memory_type": {
                "type": "string",
                "description": "类型，值必须是如下几种：[user,long]"
                               "具体含义："
                               "1 user:"
                               "2 long:"
            }
        },
        "required": ["content", "memory_type"]
    }

    async def execute(self, params: dict, signal=None) -> Any:
        content = params["content"]
        memory_type = params["memory_type"]
        session_id = params.get("session_id", "default")
        memory_manager = _get_memory_manager(params.get("app_id"))

        try:
            await memory_manager.write_memory(content, memory_type, session_id=session_id)
            return ToolResult(
                success=True,
                data="长期记忆已保存",
                details={"app_id": params.get("app_id", "main"), "session_id": session_id, "turn_id": params.get("turn_id", ""), "memory_type": memory_type}
            )
        except Exception as e:
            log.error(f"调用工具 write_long_term 失败 :{e}")
            return ToolResult(
                success=False,
                data=str(e)
            )


class MemoryWriteTool(BaseTool):
    label = "memory"
    name = "write_identity_memory"
    description = "写入人格、身份设定、行为准则、语气风格"
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "身份/人格内容"},
            "memory_type": {
                "type": "string",
                "description": "类型，值必须是如下几种：[user,soul,identity]"
                               "具体含义："
                               "3 user:"
                               "4 soul:"
                               "5 identity:"
            }
        },
        "required": ["content", "memory_type"]
    }


    async def execute(self, params: dict, signal=None) -> Any:
        content = params["content"]
        memory_type = params["memory_type"]
        session_id = params.get("session_id", "default")
        memory_manager = _get_memory_manager(params.get("app_id"))

        try:
            await memory_manager.write_memory(content, memory_type, session_id=session_id)
            return ToolResult(
                success=True,
                data=f"{memory_type}记忆已保存",
                details={"app_id": params.get("app_id", "main"), "session_id": session_id, "turn_id": params.get("turn_id", ""), "memory_type": memory_type}
            )
        except Exception as e:
            log.error(f"调用工具 {self.name} 失败 :{e}")
            return ToolResult(
                success=False,
                data=str(e)
            )

