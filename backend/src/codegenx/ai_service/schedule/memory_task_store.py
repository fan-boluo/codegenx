"""
记忆离线任务队列 + 消费水位 + 同步检查点 —— MySQL DAO（async SQLAlchemy）。

对应表（DDL 见 init/memory_schema.sql，由部署方手动执行，本模块不建表）：
  memory_task            离线任务队列
  memory_watermark       会话消息消费水位（chat_message.seq，会话内单调递增）
  memory_sync_checkpoint 双数据源同步检查点 (app_id, scope) → last_entry_id

状态机：pending → running → done
                └→ 失败：retry_count+1，按退避(1m/5m/30m)回 pending；超限 → dead
语义：at-least-once。任务失败重跑可能重复写记忆，由写入侧判重（writer 相似度门槛）兜底。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from sqlalchemy import text

from db.mysql.session import session_maker
from shared import log as logger

# 任务类型常量（与 memory_schema.sql 的注释一致）
TASK_WARM_EXTRACT = "warm_extract"
TASK_CONSOLIDATE = "consolidate"
TASK_DECAY_ARCHIVE = "decay_archive"
TASK_SYNC_CHECK = "sync_check"

# 失败退避序列（秒）：第 n 次失败后等待 backoffs[n-1] 再重试
RETRY_BACKOFFS = (60.0, 300.0, 1800.0)
MAX_RETRY = len(RETRY_BACKOFFS)


class MemoryTaskStore:
    """memory_task / memory_watermark / memory_sync_checkpoint 三表 DAO。"""

    # === 任务登记 ===

    async def enqueue(
        self,
        task_type: str,
        app_id: str,
        session_id: str = "",
        user_id: str = "",
        payload: dict | None = None,
        next_run_at: float = 0.0,
        dedup: bool = False,
    ) -> int | None:
        """登记一个离线任务，返回任务 id；dedup=True 时同类型同会话已有
        pending/running 任务则跳过（聊天在途高频触发靠它防任务爆炸）。"""
        async with session_maker() as session:
            if dedup:
                row = (
                    await session.execute(
                        text(
                            "SELECT id FROM memory_task "
                            "WHERE task_type = :t AND session_id = :s AND status IN ('pending','running') "
                            "LIMIT 1"
                        ),
                        {"t": task_type, "s": session_id},
                    )
                ).first()
                if row is not None:
                    return None
            cur = await session.execute(
                text(
                    "INSERT INTO memory_task "
                    "(task_type, app_id, user_id, session_id, status, payload, next_run_at) "
                    "VALUES (:t, :a, :u, :s, 'pending', :p, :n)"
                ),
                {
                    "t": task_type,
                    "a": str(app_id),
                    "u": str(user_id or ""),
                    "s": session_id,
                    "p": json.dumps(payload, ensure_ascii=False) if payload else None,
                    "n": int(next_run_at),
                },
            )
            await session.commit()
            return int(cur.lastrowid or 0) or None

    # === 任务领取与状态流转 ===

    async def claim_due(self, limit: int = 5, now: float | None = None) -> list[dict[str, Any]]:
        """原子领取到期 pending 任务并置 running。

        单事务内 SELECT ... FOR UPDATE SKIP LOCKED + UPDATE：
        多消费者也不会把同一任务发两次（当前为单消费者，双保险）。
        """
        now = now or time.time()
        async with session_maker() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM memory_task "
                            "WHERE status = 'pending' AND next_run_at <= :n "
                            "ORDER BY id LIMIT :l FOR UPDATE SKIP LOCKED"
                        ),
                        {"n": int(now), "l": int(limit)},
                    )
                )
                .mappings()
                .all()
            )
            tasks = [dict(r) for r in rows]
            if tasks:
                ids = [t["id"] for t in tasks]
                await session.execute(
                    text(
                        "UPDATE memory_task SET status = 'running' "
                        "WHERE id IN :ids"
                    ),
                    {"ids": tuple(ids)},
                )
                for t in tasks:
                    t["status"] = "running"
            await session.commit()
        for t in tasks:
            t["payload"] = self._parse_payload(t.get("payload"))
        return tasks

    async def mark_done(self, task_id: int) -> None:
        async with session_maker() as session:
            await session.execute(
                text("UPDATE memory_task SET status = 'done', last_error = NULL WHERE id = :i"),
                {"i": task_id},
            )
            await session.commit()

    async def mark_failed(self, task_id: int, error: str, backoffs: tuple = RETRY_BACKOFFS) -> str:
        """失败退避：回 pending 延迟重试；超限置 dead。返回处理后的状态。"""
        now = time.time()
        async with session_maker() as session:
            row = (
                await session.execute(
                    text("SELECT retry_count FROM memory_task WHERE id = :i"),
                    {"i": task_id},
                )
            ).first()
            retry = ((row[0] if row else 0) or 0) + 1
            if retry > len(backoffs):
                status, next_at = "dead", now
            else:
                status, next_at = "pending", now + backoffs[retry - 1]
            await session.execute(
                text(
                    "UPDATE memory_task SET status = :st, retry_count = :r, next_run_at = :n, "
                    "last_error = :e WHERE id = :i"
                ),
                {"st": status, "r": retry, "n": int(next_at), "e": error[:2000], "i": task_id},
            )
            await session.commit()
        return status

    async def recover_running(self) -> int:
        """启动/关闭时把 running 任务复位 pending（崩溃恢复，at-least-once）。"""
        async with session_maker() as session:
            cur = await session.execute(
                text("UPDATE memory_task SET status = 'pending' WHERE status = 'running'")
            )
            await session.commit()
            changed = int(cur.rowcount or 0)
        if changed:
            logger.info("记忆任务崩溃恢复: {} 个 running 任务已复位为 pending", changed)
        return changed

    # === 统计与调度辅助 ===

    async def stats(self) -> dict[str, int]:
        async with session_maker() as session:
            rows = (
                (
                    await session.execute(
                        text("SELECT status, COUNT(*) AS c FROM memory_task GROUP BY status")
                    )
                )
                .mappings()
                .all()
            )
        return {r["status"]: int(r["c"]) for r in rows}

    async def consolidate_scheduled_today(self) -> bool:
        """今天是否已登记过 consolidate（每日定时调度的判重依据）。"""
        return await self._scheduled_today("consolidate")

    async def decay_scheduled_today(self) -> bool:
        """今天是否已登记过 decay_archive。"""
        return await self._scheduled_today("decay_archive")

    async def sync_scheduled_today(self) -> bool:
        """今天是否已登记过 sync_check。"""
        return await self._scheduled_today("sync_check")

    async def _scheduled_today(self, task_type: str) -> bool:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        async with session_maker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id FROM memory_task WHERE task_type = :t "
                        "AND status IN ('pending','running','done') AND created_at >= :d LIMIT 1"
                    ),
                    {"t": task_type, "d": today_start},
                )
            ).first()
        return row is not None

    # === 水位（memory_watermark 表）===

    async def get_watermark(self, session_id: str) -> int:
        """读取会话消费水位（已消费的最大 chat_message.seq）；无记录返回 0。"""
        async with session_maker() as session:
            row = (
                await session.execute(
                    text("SELECT last_seq FROM memory_watermark WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).first()
        return int(row[0] or 0) if row else 0

    async def advance_watermark(
        self, session_id: str, app_id: str, user_id: str, last_seq: int
    ) -> None:
        """推进水位（upsert）。只在提取成功后调用，保证崩溃可续提。"""
        async with session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO memory_watermark "
                    "(session_id, app_id, user_id, last_seq) "
                    "VALUES (:s, :a, :u, :l) "
                    "ON DUPLICATE KEY UPDATE last_seq = :l"
                ),
                {
                    "s": session_id,
                    "a": str(app_id),
                    "u": str(user_id or ""),
                    "l": int(last_seq),
                },
            )
            await session.commit()

    # === 同步检查点（memory_sync_checkpoint 表）===

    async def get_checkpoint(self, app_id: str, scope: str = "warm", user_id: str = "") -> str:
        """读取已同步到 Qdrant 的最大记忆 id；无记录返回空串。"""
        async with session_maker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT last_entry_id FROM memory_sync_checkpoint "
                        "WHERE app_id = :a AND scope = :sc AND user_id = :u"
                    ),
                    {"a": str(app_id), "sc": scope, "u": str(user_id or "")},
                )
            ).first()
        return str(row[0] or "") if row else ""

    async def advance_checkpoint(self, app_id: str, scope: str, last_entry_id: str, user_id: str = "") -> None:
        """推进同步检查点（upsert）。"""
        async with session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO memory_sync_checkpoint (user_id, app_id, scope, last_entry_id) "
                    "VALUES (:u, :a, :sc, :e) "
                    "ON DUPLICATE KEY UPDATE last_entry_id = :e"
                ),
                {"u": str(user_id or ""), "a": str(app_id), "sc": scope, "e": last_entry_id},
            )
            await session.commit()

    # === 内部 ===

    @staticmethod
    def _parse_payload(raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (str, bytes)) and raw:
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}


# === 全局单例 ===

_global_memory_task_store: MemoryTaskStore | None = None


def get_memory_task_store() -> MemoryTaskStore:
    """全局任务存储单例（聊天在途高频登记 + 离线 worker 共用）。"""
    global _global_memory_task_store
    if _global_memory_task_store is None:
        _global_memory_task_store = MemoryTaskStore()
    return _global_memory_task_store
