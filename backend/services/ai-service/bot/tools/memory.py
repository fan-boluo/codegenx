"""
记忆工具
"""
import asyncio
from typing import Any

from bot.memory.memory_writer import SHORT_TERM_TYPES, get_memory_writer
from bot.memory.retriver import get_memory_retriever
from bot.memory.schema import MemorySearchResult
from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log


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
            "搜索 Qdrant 记忆库。必传 query；可选 score_threshold，默认 0.65；"
            "可选 top_k/topk，默认 10；user_id 和 app_id 由运行上下文自动注入。"
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
                "score_threshold": {
                    "type": "number",
                    "description": "Minimum score threshold (default: 0.65)"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10)"
                },
                "topk": {
                    "type": "integer",
                    "description": "Alias of top_k (default: 10)"
                },
            },
            "required": ["query"]
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        try:
            query = self._normalize_required_text(params.get("query"), "query")
            user_id = self._normalize_required_text(params.get("user_id"), "user_id")
            app_id = self._normalize_required_text(params.get("app_id", "main"), "app_id")
            score_threshold = float(params.get("score_threshold", 0.65) or 0.65)
            top_k = int(params.get("top_k", params.get("topk", 10)) or 10)

            results = await get_memory_retriever().retrieve(
                user_id=user_id,
                app_id=app_id,
                query=query,
                is_hybrid=True,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            formatted_results = self._format_results(results)

            return ToolResult(
                success=True,
                data=formatted_results,
                details={
                    "query": query,
                    "user_id": user_id,
                    "app_id": app_id,
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
            lines.append(
                f"{i}. [{result.type.value}] score={result.score:.2f} importance={float(result.importance or 0.0):.2f}"
            )
            if result.category:
                lines.append(f"   Category: {result.category}")
            if result.version is not None:
                lines.append(f"   Version: {result.version}")
            lines.append(f"   {result.snippet}")
            lines.append("")

        return "\n".join(lines)

    def _serialize_result(self, result: MemorySearchResult) -> dict[str, Any]:
        return {
            "id": result.id,
            "type": result.type.value,
            "score": result.score,
            "snippet": result.snippet,
            "text": result.text,
            "category": result.category,
            "importance": result.importance,
            "access_count": result.access_count,
            "version": result.version,
        }

    @staticmethod
    def _normalize_required_text(value: Any, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required")
        return normalized


class MemoryWriteShortTermTool(BaseTool):
    name = "write_short_term"
    label = "memory"
    description = "写入并保存前端代码生成场景下的短期临时记忆，沉淀用户前端开发相关偏好、技术选型、功能需求、页面交互约定、决策结论、待开发任务及客观约束条件；用于后续生成页面、组件、样式、接口联调、工程配置时精准复用记忆信息，同时按统一标准对本条记忆进行重要度打分，作为后续检索优先级、是否强制遵循的依据；"
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "需要存入短期记忆的前端开发相关具体内容"},
            "memory_type": {
                "type": "string",
                "description": "短期记忆分类类型，可选固定枚举值：preference开发偏好 / decision技术决策 / fact客观约束 / todo待开发任务，默认值为 fact"
            },
            "importance": {
                "type": "number",
                "description": "记忆重要度评分，取值范围0-1，前端代码生成打分标准：\n0.8~1.0 强约束必须严格遵守：技术栈指定、UI框架选型、全局样式规范、项目目录结构、强制编码规则；\n0.6~0.79 常规建议优先遵循：组件风格、页面布局偏好、常用工具库、交互习惯；\n0.3~0.59 次要参考信息：临时示例、可选扩展功能、非强制性细节；\n0.0~0.39 无关备注仅做留存：随口提及、临时闲聊、不影响代码生成的冗余信息；默认值0.5"
            }
        },
        "required": ["content"]
    }

    @staticmethod
    def _normalize_required_text(value: Any, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required")
        return normalized

    @staticmethod
    def _normalize_memory_type(value: Any) -> str:
        normalized = str(value or "fact").strip().lower()
        if normalized not in SHORT_TERM_TYPES:
            raise ValueError(f"Unsupported short-term memory type: {value}")
        return normalized



    async def execute(self, params: dict, signal=None) -> ToolResult:
        try:
            session_id = str(params.get("session_id", "default") or "default")
            content = self._normalize_required_text(params.get("content"), "content")
            app_id = self._normalize_required_text(params.get("app_id", "main"), "app_id")
            user_id = self._normalize_required_text(params.get("user_id") or session_id or "anonymous", "user_id")
            memory_type = self._normalize_memory_type(params.get("memory_type", "fact"))
            importance = float(params.get("importance", 0.5) or 0.5)

            memory_writer = get_memory_writer()
            point_id = await memory_writer.add_short_term_memory(
                user_id=user_id,
                app_id=app_id,
                content=content,
                memory_type=memory_type,
                importance=importance
            )
            return ToolResult(
                success=True,
                data="短期记忆已保存",
                details={
                    "point_id": point_id,
                    "app_id": app_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "turn_id": params.get("turn_id", ""),
                    "memory_type": memory_type
                }
            )
        except Exception as e:
            log.error(f"调用工具 write_short_term 失败 :{e}")
            return ToolResult(
                success=False,
                data=str(e)
            )

if __name__ == '__main__':
    import asyncio

    async def _main() -> None:
        infos = [
            {
                "user_id": "1",
                "app_id": "3",
                "content": "页面使用暖色系，能让人联想到好吃的食物",
                "memory_type": "fact",
                "importance": 0.8,
            },
            {
                "user_id": "1",
                "app_id": "3",
                "content": "项目名称使用纯html写，不用复杂架构",
                "memory_type": "preference",
                "importance": 0.82,
            },
            {
                "user_id": "1",
                "app_id": "3",
                "content": "项目预计开发时间为一周",
                "memory_type": "fact",
                "importance": 0.9,
            },


        ]

        tool = MemoryWriteShortTermTool()
        results = await asyncio.gather(*(tool.execute(info) for info in infos))
        point_ids = [
            str(result.details.get("point_id"))
            for result in results
            if result.success and result.details and result.details.get("point_id")
        ]
        # point_ids = ['d73e374e-cb14-47a8-b5d4-3929429456a8', 'f6b9a187-49d2-455f-a74c-e84e4d998f54']

        writer = get_memory_writer()
        await writer.consolidate_to_long_term(user_id="1", app_id="3", candidate_ids=point_ids)
        # await writer.consolidate_to_long_term(user_id="1", app_id="3", candidate_ids=point_ids)

    asyncio.run(_main())