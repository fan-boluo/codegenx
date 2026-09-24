"""
记忆门面 —— 每轮组装 hot + warm 两层注入（供 SessionContext.build_system_prompt 调用）。

分层（设计方案 v2.1 §1.1/§5.5）：
  hot  hot_store.format_hot_prompt  核心约束，每轮全量注入（MySQL，≤hot_token_budget）
  warm retriever.search_warm        情景记忆，按 query 召回 + 应用层重排（≤warm_token_budget）
  冲突消解规则注入尾部（P0-12）：hot > warm > 用户历史，用户当前指令最高

写入不在这里：条件触发的提取走 schedule/ 离线任务，本类只读。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory import metrics
from codegenx.ai_service.memory.hot_store import format_hot_prompt
from codegenx.ai_service.memory.retriever import search_warm, format_warm_entries_prompt

# 冲突消解规则（P0-12，§5.5）：跨层 hot 优先，不按时间推翻硬约束
MEMORY_USAGE_RULES = """\
# 记忆冲突处理规则
- 核心约束（hot）与历史经验（warm）冲突时，一律以核心约束为准
- 同一层级内出现冲突时，以时间更新的记忆为准
- 若发现记忆与用户当前明确指令冲突，以用户当前指令为准\
"""


@dataclass
class MemoryManager:
    """会话级记忆门面（随 SessionContext 生命周期）。"""

    session_id: str = ""
    app_id: str = ""
    user_id: str = ""

    async def load(self, query: str = "") -> str:
        """组装当前轮的记忆注入：hot（常驻）+ warm（按 query 召回）+ 冲突规则。

        Args:
            query: 用户本轮输入，空则跳过 warm 召回。
        """
        if not config.memory.search.enabled:
            return ""

        parts: list[str] = []
        search_cfg = config.memory.search
        hot_timeout = max(0.05, (search_cfg.hot_load_timeout_ms or 200) / 1000)
        warm_timeout = max(0.05, (search_cfg.warm_load_timeout_ms or 500) / 1000)

        # ── hot 层：核心约束，每轮注入（超时/异常均降级为空，§9 原则 1）──────────
        try:
            hot_prompt = await asyncio.wait_for(
                format_hot_prompt(self.app_id, self.user_id), timeout=hot_timeout
            )
        except asyncio.TimeoutError:
            metrics.inc_degrade("timeout")
            log.warning("hot 层加载超时(>{}ms)，本轮降级为无 hot 约束", int(hot_timeout * 1000))
            hot_prompt = ""
        except Exception as exc:  # noqa: BLE001 — 记忆加载不得阻塞对话
            metrics.inc_degrade("mysql")
            log.error("hot 层注入异常:{}", exc)
            hot_prompt = ""
        if hot_prompt:
            parts.append(hot_prompt)

        # ── warm 层：按 query 混合召回（超时/异常降级，§9 原则 1）────────────────
        warm_entries: list = []
        if (query or "").strip():
            try:
                warm_entries = await asyncio.wait_for(
                    search_warm(self.app_id, self.user_id, query), timeout=warm_timeout
                )
            except asyncio.TimeoutError:
                metrics.inc_degrade("timeout")
                log.warning("warm 召回超时(>{}ms)，本轮降级为无 warm 记忆", int(warm_timeout * 1000))
                warm_entries = []
            except Exception as exc:  # noqa: BLE001 — 记忆检索失败不阻断对话
                metrics.inc_degrade("mysql")
                log.error("warm 记忆检索异常:{}", exc)
                warm_entries = []
            if warm_entries:
                parts.append(format_warm_entries_prompt(warm_entries))

        # ── 冲突消解规则（有记忆注入才附带）────────────────────────────────────
        if parts:
            parts.append(MEMORY_USAGE_RULES)

        # 监控埋点：本轮记忆命中条数
        if warm_entries:
            try:
                from codegenx.ai_service.monitor.monitor_pipeline import get_monitor_pipeline
                get_monitor_pipeline().on_memory_recall(self.session_id, len(warm_entries))
            except Exception:  # noqa: BLE001 — 埋点失败静默
                pass

        return "\n\n".join(parts)
