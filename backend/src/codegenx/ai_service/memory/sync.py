"""
双数据源对账（sync_check）—— MySQL agent_memory 唯一真源，Qdrant 可重建索引。

定位修正（v2.1 §7）：同步正确性完全由行级 vector_synced_at IS NULL 保证，
memory_sync_checkpoint 仅作对账断点续跑记录（旧「已同步最大 id」高水位语义
存在乱序漏同步漏洞，已废弃）。

每日任务两向：
  正向 missing —— vector_synced_at IS NULL 的 warm 行批量补写（embedding +
                  upsert 都必须批量；积压超阈值告警说明同步链路故障）
  反向 ghost   —— 按租户 scroll Qdrant 点位，对照 MySQL 清理幽灵（已被合规
                  删除/归档物理删除的记忆仍占召回名额）

Qdrant collection 若被清空/损坏：数据无需手工恢复，vector_synced_at 全量置
NULL 后重跑本任务即从 MySQL 完整重建。
"""
from __future__ import annotations

from sqlalchemy import text

from db.mysql.session import session_maker
from shared import log
from codegenx.ai_service.memory.embedding import get_embedding_client
from codegenx.ai_service.memory.memory_store import (
    scan_pending_vector_sync,
    mark_vector_synced,
    count_pending_vector_sync,
    list_tenants,
)
from codegenx.ai_service.memory.vector_store import (
    upsert_points,
    scroll_point_ids,
    delete_points_by_ids,
)
from codegenx.ai_service.schedule.memory_task_store import get_memory_task_store

# 正向单批上限与积压告警阈值（§10.1：> PENDING_ALERT_THRESHOLD 说明同步挂了）
_FORWARD_BATCH = 500
_PENDING_ALERT_THRESHOLD = 5000


async def reconcile_all_apps(llm_invoke=None) -> int:
    """全量对账一轮（llm_invoke 参数保留对齐 scheduler 签名，当前纯规则）。

    返回补写 + 清理的点位总数。
    """
    try:
        fixed = await _forward_sync()
    except Exception as exc:  # noqa: BLE001 — 单向失败不阻断另一向
        log.error("[sync_check] 正向补写异常: {}", exc)
        fixed = 0
    try:
        ghosted = await _reverse_sync()
    except Exception as exc:  # noqa: BLE001
        log.error("[sync_check] 反向清理异常: {}", exc)
        ghosted = 0
    return fixed + ghosted


async def _forward_sync() -> int:
    """正向：待同步队列 → 批量 embedding → 批量 upsert → 标记已同步。

    失败不标记（vector_synced_at 保持 NULL），下一轮自动重试；幂等天然成立。
    """
    pending_total = await count_pending_vector_sync()
    if pending_total > _PENDING_ALERT_THRESHOLD:
        log.error(
            "[sync_check] 待同步队列积压 {} 条（>{}）,同步链路疑似故障",
            pending_total, _PENDING_ALERT_THRESHOLD,
        )

    entries = await scan_pending_vector_sync(limit=_FORWARD_BATCH)
    if not entries:
        return 0

    vectors = await get_embedding_client().embed_texts([e.inject_text() for e in entries])
    await upsert_points(entries, vectors)
    await mark_vector_synced([e.id for e in entries])
    log.info("[sync_check] 正向补写 {} 个点位（队列余量≈{}）", len(entries),
             max(0, pending_total - len(entries)))

    # 检查点仅作断点续跑记录（ULID 单调可比），不承担同步正确性
    try:
        await get_memory_task_store().advance_checkpoint(
            entries[0].app_id, "warm", entries[-1].memory_id, user_id=entries[0].user_id,
        )
    except Exception as exc:  # noqa: BLE001 — 检查点失败无害
        log.debug("[sync_check] 检查点推进失败（非致命）:{}", exc)
    return len(entries)


async def _reverse_sync() -> int:
    """反向：按租户 scroll Qdrant → 对照 MySQL 清理幽灵点位。

    幽灵 = Qdrant 有但 MySQL 无（已物理删除/合规硬删）或 status != 1
    （已失效——正常情况会被正向重推覆盖，这里兜底清理漏推的）。
    """
    ghosts = 0
    for app_id, user_id in await list_tenants():
        point_ids = await scroll_point_ids(app_id, user_id)
        if not point_ids:
            continue
        alive_status = await _status_by_ids(point_ids)
        dead_ids = [
            pid for pid in point_ids
            if alive_status.get(pid) is None or alive_status[pid] != 1
        ]
        if dead_ids:
            await delete_points_by_ids(dead_ids)
            ghosts += len(dead_ids)
            log.info("[sync_check] app {}/{} 清理幽灵点位 {} 个", app_id, user_id, len(dead_ids))
    return ghosts


async def _status_by_ids(ids: list[int]) -> dict[int, int]:
    """按主键批量回查存活状态（id 来自 Qdrant 点位，主键精确回查无越权面）。"""
    async with session_maker() as session:
        rows = (
            await session.execute(
                text("SELECT id, status FROM agent_memory WHERE id IN :ids"),
                {"ids": tuple(int(i) for i in ids)},
            )
        ).all()
    return {int(r[0]): int(r[1]) for r in rows}
