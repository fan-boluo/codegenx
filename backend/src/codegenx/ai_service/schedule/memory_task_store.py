"""
记忆离线任务队列 + 提取水位 —— SQLite 持久化 DAO（技术方案 v3 5.3.4 / 5.3.5 节）。

职责：
1. memory_task 表：离线任务登记 / 原子领取 / 状态流转 / 退避重试 / 崩溃恢复
2. memory_watermark 表：会话消息消费水位（rowid），崩溃后从水位处增量续提

状态机：
    pending → running → done
                    └→ 失败：retry_count+1，按退避(1m/5m/30m)回 pending；超限 → dead

语义：at-least-once。任务失败重跑会重复写 md（append-only 容忍重复），
由 consolidate 整理阶段去重。

注意：建表 DDL 在仓库根 init/patients_schema.sql，由部署方手动执行；本模块不建表。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

# from db.sqlite_store import SqliteStore
from shared import log as logger

# 任务类型常量（与 patients_schema.sql 的 CHECK 约束一致）
TASK_TOPIC_EXTRACT = "topic_extract"
TASK_SESSION_SUMMARIZE = "session_summarize"
TASK_SESSION_FLUSH = "session_flush"
TASK_CONSOLIDATE = "consolidate"

# 失败退避序列（秒）：第 n 次失败后等待 backoffs[n-1] 再重试
RETRY_BACKOFFS = (60.0, 300.0, 1800.0)


class MemoryTaskStore:
    """memory_task / memory_watermark 两表的数据访问对象。

    用法:
        store = get_memory_task_store()
        store.enqueue(TASK_TOPIC_EXTRACT, patient_id, session_id, dedup=True)
        tasks = store.claim_due(limit=5)
        store.mark_done(task["id"])
    """

    def __init__(self, store: SqliteStore | None = None) -> None:
        self._store = store or SqliteStore()

    # === 任务登记 ===

    def enqueue(
        self,
        task_type: str,
        patient_id: str,
        session_id: str = "",
        payload: dict | None = None,
        next_run_at: float = 0.0,
        dedup: bool = False,
    ) -> int | None:
        """登记一个离线任务，返回任务 id。

        参数:
            dedup: 为 True 时，同 (task_type, session_id) 已有 pending/running
                   任务则跳过登记（返回 None）。聊天在途高频调用靠它防任务爆炸；
                   检查与插入同为同步操作（单事件循环内无 await），无并发窗口。
            next_run_at: 最早可领取时间（unix 秒）。topic_extract 用它延迟几秒，
                         等当前 turn 的 assistant 消息落库后再消费。
        """
        if dedup and self._has_active(task_type, session_id):
            return None
        now = time.time()
        with self._store.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO memory_task
                    (task_type, patient_id, session_id, status, payload, next_run_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (task_type, patient_id, session_id, SqliteStore.to_json(payload), next_run_at),
            )
            return int(cur.lastrowid)

    def _has_active(self, task_type: str, session_id: str) -> bool:
        """是否存在同类型同会话的 pending/running 任务。"""
        row = self._store.query_one(
            """
            SELECT id FROM memory_task
            WHERE task_type = ? AND session_id = ? AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (task_type, session_id),
        )
        return row is not None

    # === 任务领取与状态流转 ===

    def claim_due(self, limit: int = 5, now: float | None = None) -> list[dict[str, Any]]:
        """原子领取到期的 pending 任务并置为 running。

        SELECT 与 UPDATE 在同一连接内完成（SQLite 单写者锁保证原子性），
        不会把同一条任务发给两个消费者。
        """
        now = now or time.time()
        with self._store.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_task
                WHERE status = 'pending' AND next_run_at <= ?
                ORDER BY id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            tasks = [dict(r) for r in rows]
            if tasks:
                ids = [t["id"] for t in tasks]
                marks = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE memory_task SET status = 'running', updated_at = ? WHERE id IN ({marks})",
                    (now, *ids),
                )
                # 返回值反映领取后的状态（SELECT 在 UPDATE 之前执行）
                for t in tasks:
                    t["status"] = "running"
        return tasks

    def mark_done(self, task_id: int) -> None:
        self._store.execute(
            "UPDATE memory_task SET status = 'done', last_error = NULL, updated_at = ? WHERE id = ?",
            (time.time(), task_id),
        )

    def mark_failed(self, task_id: int, error: str, backoffs: tuple = RETRY_BACKOFFS) -> str:
        """任务失败：退避后回 pending 重试；重试超限置 dead。

        返回处理后的状态（'pending' 或 'dead'）。
        """
        now = time.time()
        row = self._store.query_one(
            "SELECT retry_count FROM memory_task WHERE id = ?", (task_id,)
        )
        retry = (row["retry_count"] if row else 0) + 1
        if retry > len(backoffs):
            status, next_at = "dead", now
        else:
            status, next_at = "pending", now + backoffs[retry - 1]
        self._store.execute(
            """
            UPDATE memory_task
            SET status = ?, retry_count = ?, next_run_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, retry, next_at, error[:2000], now, task_id),
        )
        return status

    def recover_running(self) -> int:
        """把所有 running 任务复位为 pending（应用启动/关闭时调用）。

        场景：进程崩溃/被杀导致任务停在 running —— 复位后下个 worker 续跑。
        返回复位条数。
        """
        changed = self._store.execute(
            "UPDATE memory_task SET status = 'pending', updated_at = ? WHERE status = 'running'",
            (time.time(),),
        )
        if changed:
            logger.info(f"记忆任务崩溃恢复: {changed} 个 running 任务已复位为 pending")
        return changed

    # === 统计与调度辅助 ===

    def stats(self) -> dict[str, int]:
        """按状态统计任务数（监控用）。"""
        rows = self._store.query_all(
            "SELECT status, COUNT(*) AS c FROM memory_task GROUP BY status"
        )
        return {row["status"]: row["c"] for row in rows}

    def consolidate_scheduled_today(self) -> bool:
        """今天是否已登记/执行过 consolidate 任务（worker 每日定时调度的判重依据）。"""
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self._store.query_one(
            """
            SELECT id FROM memory_task
            WHERE task_type = 'consolidate'
              AND status IN ('pending', 'running', 'done')
              AND created_at >= ?
            LIMIT 1
            """,
            (today_start,),
        )
        return row is not None

    # === 水位（memory_watermark 表）===

    def get_watermark(self, session_id: str) -> int:
        """读取会话消费水位（已提取到的最大消息 rowid），无记录返回 0。"""
        row = self._store.query_one(
            "SELECT last_msg_rowid FROM memory_watermark WHERE session_id = ?",
            (session_id,),
        )
        return int(row["last_msg_rowid"]) if row else 0

    def advance_watermark(self, session_id: str, patient_id: str, rowid: int) -> None:
        """推进水位（upsert）。只在 LLM 提取成功写盘后调用，保证崩溃可续提。"""
        self._store.execute(
            """
            INSERT INTO memory_watermark (session_id, patient_id, last_msg_rowid, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id)
            DO UPDATE SET last_msg_rowid = ?, updated_at = ?
            """,
            (session_id, patient_id, rowid, time.time(), rowid, time.time()),
        )


# === 全局单例 ===

_global_memory_task_store: MemoryTaskStore | None = None


def get_memory_task_store() -> MemoryTaskStore:
    """获取全局记忆任务存储单例（聊天在途高频登记，共用一个实例）。"""
    global _global_memory_task_store
    if _global_memory_task_store is None:
        _global_memory_task_store = MemoryTaskStore()
    return _global_memory_task_store
