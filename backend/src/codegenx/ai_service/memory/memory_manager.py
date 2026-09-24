"""
记忆门面 —— 每轮组装 hot + warm 两层注入（供 SessionContext.build_system_prompt 调用）。

分层（设计文档 §3）：
  hot  hot_store.format_hot_prompt  核心约束，始终注入（≤2K token）
  warm retriever.search_warm        话题记忆，按当前 query 混合召回 + 重排（≤8K token）

写入不在这里：条件触发的提取走 schedule/ 任务（阶段3），本类只读。
"""
from __future__ import annotations

from dataclasses import dataclass

from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory.hot_store import format_hot_prompt
from codegenx.ai_service.memory.retriever import search_warm, format_warm_entries_prompt


@dataclass
class MemoryManager:
    """会话级记忆门面（随 SessionContext 生命周期）。"""

    session_id: str = ""
    app_id: str = ""

    async def load(self, query: str = "") -> str:
        """组装当前轮的记忆注入：hot（常驻）+ warm（按 query 召回）。

        Args:
            query: 用户本轮输入，空则跳过 warm 召回。
        """
        if not config.memory.search.enabled:
            return ""

        parts: list[str] = []

        # ── hot 层：核心约束，每轮注入 ─────────────────────────────────────────
        hot_prompt = format_hot_prompt(self.app_id)
        if hot_prompt:
            parts.append(hot_prompt)

        # ── warm 层：按 query 混合召回 ─────────────────────────────────────────
        warm_entries: list = []
        if (query or "").strip():
            try:
                warm_entries = await search_warm(self.app_id, query)
            except Exception as exc:  # noqa: BLE001 — 记忆检索失败不阻断对话
                log.error("warm 记忆检索异常:{}", exc)
                warm_entries = []
            if warm_entries:
                parts.append(format_warm_entries_prompt(warm_entries))

        # 监控埋点：本轮记忆命中条数
        if warm_entries:
            try:
                from codegenx.ai_service.monitor.monitor_pipeline import get_monitor_pipeline
                get_monitor_pipeline().on_memory_recall(self.session_id, len(warm_entries))
            except Exception:  # noqa: BLE001 — 埋点失败静默
                pass

        return "\n\n".join(parts)
