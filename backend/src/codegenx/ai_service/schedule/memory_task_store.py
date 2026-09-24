"""
记忆离线任务队列 + 消费水位 + 同步检查点 —— MySQL DAO（async SQLAlchemy）。

对应表（DDL 见 init/memory_schema.sql，由部署方手动执行，本模块不建表）：
  memory_task            离线任务队列（v2.1 增 idempotency_key 幂等 + produced_count 审计）
  memory_watermark       会话消息消费水位（chat_message.seq，会话内单调递增）
  memory_sync_checkpoint 双数据源对账断点（v2.1 语义：仅断点续跑，不作正确性保证）

状态机：pending → running → done
                └→ 失败：retry_count+1，按退避(1m/5m/30m)回 pending；超限 → dead
语义：at-least-once。任务失败重跑可能重复写记忆：
  hot  由 uk_slot upsert 天然幂等（同槽位覆盖）
  warm 由 memory_task 幂等键/at-least-once + 后续 conflict_detect（P1）兜底
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
TASK_COMPLIANCE_DELETE = "compliance_delete"
TASK_CONFLICT_DETECT = "conflict_detect"  # P1-9：每日异步矛盾检测

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
        idempotency_key: str | None = None,
    ) -> int | None:
        """登记一个离线任务，返回任务 id。

        - dedup=True：同类型同会话已有 pending/running 任务则跳过
          （聊天在途高频触发靠它防任务爆炸）
        - idempotency_key：uk_idempotency 唯一约束兜底（合规删除等
          强幂等场景）；键冲突返回 None
        """
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
                    "(task_type, idempotency_key, app_id, user_id, session_id, status, payload, next_run_at) "
                    "VALUES (:t, :ik, :a, :u, :s, 'pending', :p, :n)"
                ),
                {
                    "t": task_type,
                    "ik": idempotency_key,
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

    async def mark_done(
        self, task_id: int, produced_count: int = 0, result_payload: dict | None = None
    ) -> None:
        """完成（produced_count 审计 + payload JSON_MERGE_PATCH 回填执行结果，§4.2 ⑥）。"""
        async with session_maker() as session:
            if result_payload:
                await session.execute(
                    text(
                        "UPDATE memory_task SET status = 'done', produced_count = :pc, "
                        "last_error = NULL, "
                        "payload = JSON_MERGE_PATCH(COALESCE(payload, '{}'), :rp) "
                        "WHERE id = :i"
                    ),
                    {
                        "i": task_id, "pc": int(produced_count),
                        "rp": json.dumps(result_payload, ensure_ascii=False, default=str),
                    },
                )
            else:
                await session.execute(
                    text(
                        "UPDATE memory_task SET status = 'done', produced_count = :pc, last_error = NULL "
                        "WHERE id = :i"
                    ),
                    {"i": task_id, "pc": int(produced_count)},
                )
            await session.commit()

    async def reschedule(self, task_id: int, delay_sec: float = 60.0) -> None:
        """无过错重排（如会话锁被占）：保持 pending，延后领取，不计失败次数。"""
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE memory_task SET status = 'pending', next_run_at = :n WHERE id = :i"
                ),
                {"i": task_id, "n": int(time.time() + delay_sec)},
            )
            await session.commit()

    async def touch_running(self, task_id: int) -> None:
        """长任务心跳（§7.4）：仅刷新 updated_at，防止被卡死回收巡检误杀。"""
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE memory_task SET updated_at = NOW() "
                    "WHERE id = :i AND status = 'running'"
                ),
                {"i": task_id},
            )
            await session.commit()

    async def reclaim_stuck(self, timeout_sec: int = 600) -> int:
        """卡死回收（§7.4）：running 且 updated_at 超时的任务复位 pending。

        Worker 崩溃会让任务永久停在 running；巡检每分钟跑一次。
        返回复位行数（P1-12 指标 memory.task.stuck_reclaimed）。
        """
        async with session_maker() as session:
            cur = await session.execute(
                text(
                    "UPDATE memory_task SET status = 'pending', next_run_at = :n "
                    "WHERE status = 'running' "
                    "AND updated_at < DATE_SUB(NOW(), INTERVAL :t SECOND)"
                ),
                {"n": int(time.time()), "t": int(timeout_sec)},
            )
            await session.commit()
            return int(cur.rowcount or 0)

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

    async def get_by_id(self, task_id: int) -> dict | None:
        """按 id 取任务完整行（P2-7 ③ 提炼批次排查）。payload 解析为 dict。"""
        async with session_maker() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM memory_task WHERE id = :i"),
                        {"i": int(task_id)},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        task = dict(row)
        task["payload"] = self._parse_payload(task.get("payload"))
        return task

    async def consolidate_scheduled_today(self) -> bool:
        """今天是否已登记过 consolidate（每日定时调度的判重依据）。"""
        return await self._scheduled_today("consolidate")

    async def decay_scheduled_today(self) -> bool:
        """今天是否已登记过 decay_archive。"""
        return await self._scheduled_today("decay_archive")

    async def sync_scheduled_today(self) -> bool:
        """今天是否已登记过 sync_check。"""
        return await self._scheduled_today("sync_check")

    async def conflict_scheduled_today(self) -> bool:
        """今天是否已登记过 conflict_detect（P1-9）。"""
        return await self._scheduled_today("conflict_detect")

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

    async def get_watermark_full(self, session_id: str) -> dict | None:
        """读取完整水位状态（漏斗触发判断用）。"""
        async with session_maker() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT last_seq, pending_signals, pending_tokens, last_extract_at "
                            "FROM memory_watermark WHERE session_id = :s"
                        ),
                        {"s": session_id},
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def bump_pending(
        self, session_id: str, app_id: str, user_id: str,
        signals: int, tokens: int,
    ) -> dict | None:
        """P1-2 第一级：累加轻量信号计数（upsert 原子自增），返回累加后状态。

        只动 pending_* 计数，不碰 last_seq（消费位点唯一写者是提炼 Worker，
        见 memory/trigger.py 的偏差说明）。
        """
        async with session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO memory_watermark "
                    "(session_id, app_id, user_id, last_seq, pending_signals, pending_tokens) "
                    "VALUES (:s, :a, :u, 0, :sg, :tk) "
                    "ON DUPLICATE KEY UPDATE "
                    "pending_signals = pending_signals + VALUES(pending_signals), "
                    "pending_tokens = pending_tokens + VALUES(pending_tokens)"
                ),
                {
                    "s": session_id, "a": str(app_id), "u": str(user_id or ""),
                    "sg": max(0, int(signals)), "tk": max(0, int(tokens)),
                },
            )
            await session.commit()
        return await self.get_watermark_full(session_id)

    async def scan_stale_pending(self, minutes: int, limit: int = 50) -> list[dict]:
        """P1-2 兜底扫描：有积压信号且超时未处理的会话（投递前崩溃补投，§4.1）。"""
        async with session_maker() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT session_id, app_id, user_id, pending_signals, pending_tokens "
                            "FROM memory_watermark "
                            "WHERE (pending_signals > 0 OR pending_tokens > 0) "
                            "AND updated_at < DATE_SUB(NOW(), INTERVAL :m MINUTE) "
                            "ORDER BY updated_at LIMIT :l"
                        ),
                        {"m": int(minutes), "l": int(limit)},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def advance_watermark(
        self, session_id: str, app_id: str, user_id: str, last_seq: int
    ) -> None:
        """推进水位并清零漏斗计数（§4.2 ⑥，仅在提取成功后调用）。

        pending_signals/pending_tokens 归零 + last_extract_at=NOW()；
        崩溃在推进前则计数保留，重试覆盖同一区间（幂等）。
        """
        async with session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO memory_watermark "
                    "(session_id, app_id, user_id, last_seq) "
                    "VALUES (:s, :a, :u, :l) "
                    "ON DUPLICATE KEY UPDATE last_seq = :l, "
                    "pending_signals = 0, pending_tokens = 0, last_extract_at = NOW()"
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

    async def record_reconcile(
        self,
        app_id: str,
        user_id: str,
        scope: str,
        last_entry_id: str = "",
        scanned_rows: int = 0,
        missing_found: int = 0,
        ghost_found: int = 0,
    ) -> None:
        """P1-5 对账进度回写（§7.3 ④）：last_reconcile_at=NOW() + 统计列。

        checkpoint 只做断点续跑与审计，不承担同步正确性（正确性由
        vector_synced_at 行级队列保证）。
        """
        async with session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO memory_sync_checkpoint "
                    "(user_id, app_id, scope, last_entry_id, last_reconcile_at, "
                    " scanned_rows, missing_found, ghost_found) "
                    "VALUES (:u, :a, :sc, :e, NOW(), :sr, :mf, :gf) "
                    "ON DUPLICATE KEY UPDATE last_entry_id = :e, last_reconcile_at = NOW(), "
                    "scanned_rows = :sr, missing_found = :mf, ghost_found = :gf"
                ),
                {
                    "u": str(user_id or ""), "a": str(app_id), "sc": scope,
                    "e": last_entry_id, "sr": int(scanned_rows),
                    "mf": int(missing_found), "gf": int(ghost_found),
                },
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
