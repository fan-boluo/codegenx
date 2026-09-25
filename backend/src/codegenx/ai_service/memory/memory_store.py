"""
agent_memory 表 DAO —— 记忆唯一真源（设计方案 v2.1，MySQL）。

隔离硬约束（设计 §8）：所有查询强制携带 app_id + user_id，本模块是唯一入口，
禁止业务代码绕过直接拼 SQL。
失效硬约束（设计 §3.3）：任何 status 1→2/3/4 的流转必须走 mark_inactive()——
同一语句内置 active_slot=NULL，否则该 uk_slot 槽位永久无法写入新记忆；
warm 层失效同时重置 vector_synced_at，让同步 Worker 把新状态推到 Qdrant。

hot 读取（P0-5）：走 idx_hot_load 前缀；排序在 Python 侧做（权重来自代码内
常量表，避免 join 字典表的 filesort；单用户 hot ≤ hot_max_entries 条，成本可忽略）。
"""
from __future__ import annotations

import json

from sqlalchemy import text

from db.mysql.session import session_maker
from shared import log
from codegenx.ai_service.memory.models import MemoryEntry, LAYER_WARM, LAYER_HOT

# 行选取列（from_row 的输入契约，所有 SELECT 复用）
_ROW_COLS = (
    "id, memory_id, app_id, user_id, memory_layer, memory_type, subject, slot_key, "
    "summary, content, token_cost, source_type, confidence, status, superseded_by, "
    "session_id, source_msg_ids, task_id, hit_count, last_hit_at, vector_synced_at, "
    "created_at, updated_at"
)


def _rows_to_entries(rows) -> list[MemoryEntry]:
    return [MemoryEntry.from_row(dict(r)) for r in rows]


# ── hot 层读取（P0-5：读文件 → 查 SQL）────────────────────────────────────────

async def load_hot(app_id: str, user_id: str) -> list[MemoryEntry]:
    """全部 active hot 记忆（每轮对话开始全量加载注入 system prompt）。

    仅 status=1 且未过 valid_to；hot 无时间衰减（P0-11），排序按
    裁剪优先级（类型权重）降序 + updated_at 降序，供溢出兜底截断。
    """
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE app_id = :a AND user_id = :u AND memory_layer = 1 AND status = 1 "
                        "AND (valid_to IS NULL OR valid_to > NOW())"
                    ),
                    {"a": str(app_id), "u": str(user_id)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


async def count_hot_slots(app_id: str, user_id: str) -> int:
    """当前 active hot 条数（写入期硬上限检查，设计 §5.4）。"""
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_memory "
                    "WHERE app_id = :a AND user_id = :u AND memory_layer = 1 AND status = 1"
                ),
                {"a": str(app_id), "u": str(user_id)},
            )
        ).first()
    return int(row[0] or 0) if row else 0


# ── hot 层写入（P0-7：uk_slot 确定性 upsert，不同步调 LLM 判重）────────────────

async def upsert_hot_slot(
    app_id: str,
    user_id: str,
    entry: MemoryEntry,
    *,
    hot_max_entries: int = 80,
    consolidate_enqueuer=None,
) -> int:
    """hot 确定性写入：同槽位旧 active → superseded（释放槽位），插入新 active。

    两步同一事务（设计 §4.4）；MySQL 唯一索引允许多行 active_slot=NULL，
    superseded 历史无限保留，active 槽位始终唯一。

    Args:
        hot_max_entries: 写入期条数硬上限，达到时拒绝写入并回调
            consolidate_enqueuer（转投整理任务），不静默截断。
        consolidate_enqueuer: async callable() -> None，由调用方注入
            （避免 DAO 反向依赖 scheduler）。

    Returns:
        新行 id；达到硬上限被拒绝时返回 0。
    """
    count = await count_hot_slots(app_id, user_id)
    if count >= hot_max_entries:
        # 运行期不静默截断（P0-13）：写入拒绝 + 转投 consolidate，等压缩后再写
        from codegenx.ai_service.memory import metrics as _metrics
        _metrics.inc_write_rejected("hot_full")
        log.error(
            "hot 层达到条数硬上限({}/{}),拒绝写入并转投 consolidate: {}",
            count, hot_max_entries, entry.summary[:60],
        )
        if consolidate_enqueuer is not None:
            try:
                await consolidate_enqueuer()
            except Exception as exc:  # noqa: BLE001 — 投递失败不影响拒绝语义
                log.warning("consolidate 任务投递失败:{}", exc)
        return 0

    entry.memory_layer = LAYER_HOT
    entry.slot_key = entry.slot_key or entry.memory_id
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE agent_memory "
                "SET status = 2, active_slot = NULL, superseded_by = :mid "
                "WHERE app_id = :a AND user_id = :u AND subject = :subj "
                "AND memory_type = :t AND slot_key = :slot AND active_slot = 1"
            ),
            {
                "mid": entry.memory_id,
                "a": str(app_id), "u": str(user_id),
                "subj": entry.subject, "t": entry.memory_type, "slot": entry.slot_key,
            },
        )
        cur = await session.execute(
            text(
                "INSERT INTO agent_memory "
                "(app_id, user_id, memory_id, memory_layer, memory_type, subject, slot_key, "
                " active_slot, summary, content, token_cost, source_type, confidence, status, "
                " session_id, source_msg_ids, task_id, vector_synced_at) "
                "VALUES (:a, :u, :mid, 1, :t, :subj, :slot, 1, :sum, :content, :tc, :src, :conf, 1, "
                " :sid, :msgs, :task, NOW())"
            ),
            _insert_params(entry),
        )
        await session.commit()
        # P1-1 写时 DEL：hot 变更后下次读触发缓存重建
        from codegenx.ai_service.memory import hot_cache
        await hot_cache.invalidate(str(app_id), str(user_id))
        return int(cur.lastrowid or 0)


# ── warm 层写入（P0-7：append-only，不判重）────────────────────────────────────

async def append_warm(app_id: str, user_id: str, entry: MemoryEntry) -> int:
    """warm 追加写入：slot_key=memory_id，vector_synced_at 置 NULL 入同步队列。

    情景记忆不判重（设计 §4.5）：重复反馈是晋升 hot 的依据，幂等由
    memory_task 保证。返回新行 id。
    """
    entry.memory_layer = LAYER_WARM
    entry.slot_key = entry.memory_id  # warm 槽位即自身，uk_slot 天然不冲突
    async with session_maker() as session:
        cur = await session.execute(
            text(
                "INSERT INTO agent_memory "
                "(app_id, user_id, memory_id, memory_layer, memory_type, subject, slot_key, "
                " active_slot, summary, content, token_cost, source_type, confidence, status, "
                " session_id, source_msg_ids, task_id, vector_synced_at) "
                "VALUES (:a, :u, :mid, 2, :t, :subj, :slot, 1, :sum, :content, :tc, :src, :conf, 1, "
                " :sid, :msgs, :task, NULL)"
            ),
            _insert_params(entry),
        )
        await session.commit()
        return int(cur.lastrowid or 0)


def _insert_params(entry: MemoryEntry) -> dict:
    return {
        "a": str(entry.app_id), "u": str(entry.user_id),
        "mid": entry.memory_id, "t": entry.memory_type, "subj": entry.subject,
        "slot": entry.slot_key, "sum": entry.inject_text()[:512],
        "content": entry.content_json(), "tc": int(entry.token_cost),
        "src": int(entry.source_type), "conf": float(entry.confidence),
        "sid": entry.session_id,
        "msgs": (
            json.dumps(entry.source_msg_ids, ensure_ascii=False)
            if entry.source_msg_ids else None
        ),
        "task": entry.task_id,
    }


# ── 失效（单一入口，设计 §3.3 硬约束）──────────────────────────────────────────

async def mark_inactive(
    app_id: str,
    user_id: str,
    memory_ids: list[str],
    status: int,
    superseded_by: str = "",
) -> int:
    """批量失效（superseded=2 / revoked=3 / archived=4）。

    置 active_slot=NULL 释放 uk_slot 槽位；vector_synced_at 重置为 NULL，
    同步 Worker 会把新 status 推到 Qdrant payload（set_payload 对不存在
    的点位是 no-op，重复同步无害）。
    """
    if not memory_ids:
        return 0
    async with session_maker() as session:
        cur = await session.execute(
            text(
                "UPDATE agent_memory "
                "SET status = :st, active_slot = NULL, superseded_by = :sb, vector_synced_at = NULL "
                "WHERE app_id = :a AND user_id = :u AND memory_id IN :ids AND status = 1"
            ),
            {
                "st": int(status), "sb": superseded_by,
                "a": str(app_id), "u": str(user_id), "ids": tuple(memory_ids),
            },
        )
        await session.commit()
        # 可能涉及 hot 行（conflict_detect/人工撤销走这里），写时 DEL 缓存
        from codegenx.ai_service.memory import hot_cache
        await hot_cache.invalidate(str(app_id), str(user_id))
        return int(cur.rowcount or 0)


# ── 检索回表（设计 §5.1：Qdrant payload 只存指针，正文从这里取）────────────────

async def get_active_by_ids(app_id: str, user_id: str, ids: list[int]) -> list[MemoryEntry]:
    """按自增 id 回表；只回 active（Qdrant payload 可能滞后，MySQL 侧再过滤一次）。"""
    if not ids:
        return []
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE id IN :ids AND app_id = :a AND user_id = :u "
                        "AND memory_layer = 2 AND status = 1"
                    ),
                    {"ids": tuple(int(i) for i in ids), "a": str(app_id), "u": str(user_id)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


async def search_summary_keyword(
    app_id: str, user_id: str, keyword: str, limit: int = 10
) -> list[MemoryEntry]:
    """关键词兜底通道：summary LIKE 精确匹配（标识符/表名/路径等 embedding 弱项）。

    走 idx_warm_recall 前缀（app+user+layer+status）后在单用户行集内过滤，
   LIKE 不走索引但行集有界。正文不再进 Qdrant payload（P0-8），原 MatchText
    通道由此替代。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE app_id = :a AND user_id = :u AND memory_layer = 2 AND status = 1 "
                        "AND summary LIKE :kw "
                        "ORDER BY id DESC LIMIT :l"
                    ),
                    {"a": str(app_id), "u": str(user_id), "kw": f"%{kw}%", "l": int(limit)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


async def batch_touch_hit(app_id: str, user_id: str, ids: list[int]) -> None:
    """召回命中登记：hit_count+1、last_hit_at=NOW()（衰减与软删依据）。

    P1-7 后仅作 Redis 不可用时的兜底路径（hit_buffer.record_hits 的降级分支）；
    常规路径是 Redis Hash 聚合 + 定时批量刷回。失败静默（非致命）。
    """
    if not ids:
        return
    try:
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE agent_memory SET hit_count = hit_count + 1, last_hit_at = NOW() "
                    "WHERE id IN :ids AND app_id = :a AND user_id = :u"
                ),
                {"ids": tuple(int(i) for i in ids), "a": str(app_id), "u": str(user_id)},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — 命中登记失败不影响检索
        log.debug("batch_touch_hit 失败（非致命）:{}", exc)


async def apply_hit_flush(app_id: str, user_id: str, hits: dict[str, tuple[int, str]]) -> int:
    """P1-7 刷回落地：按 memory_id 批量回写聚合命中（count, last_hit ISO）。

    hits: {memory_id: (增量次数, ISO 时间戳)}。逐行 UPDATE（单租户一批 ≤ 若干百行）。
    返回成功行数；调用方保证仅在整批成功后 DEL 缓存键。
    """
    if not hits:
        return 0
    done = 0
    async with session_maker() as session:
        for memory_id, (count, hit_at) in hits.items():
            try:
                cur = await session.execute(
                    text(
                        "UPDATE agent_memory SET hit_count = hit_count + :c, last_hit_at = :ts "
                        "WHERE app_id = :a AND user_id = :u AND memory_id = :m"
                    ),
                    {
                        "c": int(count),
                        "ts": _iso_to_dt(hit_at),
                        "a": str(app_id), "u": str(user_id), "m": memory_id,
                    },
                )
                done += int(cur.rowcount or 0)
            except Exception as exc:  # noqa: BLE001 — 单行失败不影响其余行
                log.warning("[hit_flush] 行刷回失败 memory_id={}: {}", memory_id, exc)
        await session.commit()
    return done


def _iso_to_dt(ts: str):
    """ISO 字符串 → MySQL DATETIME 字面量；坏值返回 None（保持原 last_hit_at 不动）。"""
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


async def sample_active_warm(limit: int = 100) -> list[MemoryEntry]:
    """P1-5 抽检：随机采样 active warm 记忆（用 summary 自检索验证索引健康）。

    全局采样（不分租户）；ORDER BY RAND() 在当前体量（单表万级）可接受。
    """
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE memory_layer = 2 AND status = 1 "
                        "ORDER BY RAND() LIMIT :l"
                    ),
                    {"l": int(limit)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


# ── 向量同步队列（设计 §4.7：vector_synced_at IS NULL 即待办队列）───────────────

async def scan_pending_vector_sync(limit: int = 500) -> list[MemoryEntry]:
    """待同步 warm 行（含状态变更重推：status IN (1,2,3)）。

    排除 10 分钟内的新行——刚写入的行由写入链路内联同步，这里只兜底
    补漏（设计 §7.3），避免与在途写入互相干扰。
    """
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE memory_layer = 2 AND vector_synced_at IS NULL "
                        "AND status IN (1, 2, 3) "
                        "AND created_at < DATE_SUB(NOW(), INTERVAL 10 MINUTE) "
                        "ORDER BY id LIMIT :l"
                    ),
                    {"l": int(limit)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


async def mark_vector_synced(ids: list[int]) -> None:
    if not ids:
        return
    async with session_maker() as session:
        await session.execute(
            text("UPDATE agent_memory SET vector_synced_at = NOW() WHERE id IN :ids"),
            {"ids": tuple(int(i) for i in ids)},
        )
        await session.commit()


async def count_pending_vector_sync() -> int:
    """待同步队列长度（积压超阈值告警 = 同步链路故障，§10.1 memory.vector.pending）。"""
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_memory "
                    "WHERE memory_layer = 2 AND vector_synced_at IS NULL AND status IN (1, 2, 3)"
                )
            )
        ).first()
    return int(row[0] or 0) if row else 0


# ── consolidate（每日整理：warm 精确重复合并）─────────────────────────────────

async def merge_duplicate_warm(app_id: str, user_id: str) -> int:
    """同 summary 精确重复的 warm 记忆只保留最新（id 最大），旧的置 superseded。

    hot 层压缩（同类合并/抽象提升）为 P2-2，不在本任务范围。
    superseded_by 留空（整理合并无单一取代者）；active_slot=NULL + 状态置 2
    必须同语句（uk_slot 硬约束），vector_synced_at=NULL 让 Worker 重推状态。
    """
    async with session_maker() as session:
        cur = await session.execute(
            text(
                "UPDATE agent_memory AS m "
                "JOIN ("
                "  SELECT summary, MAX(id) AS keep_id FROM agent_memory "
                "  WHERE app_id = :a AND user_id = :u AND memory_layer = 2 AND status = 1 "
                "  GROUP BY summary HAVING COUNT(*) > 1"
                ") AS d ON m.summary = d.summary "
                "SET m.status = 2, m.active_slot = NULL, m.vector_synced_at = NULL "
                "WHERE m.app_id = :a AND m.user_id = :u AND m.memory_layer = 2 "
                "AND m.status = 1 AND m.id < d.keep_id"
            ),
            {"a": str(app_id), "u": str(user_id)},
        )
        await session.commit()
        return int(cur.rowcount or 0)


# ── 衰减 / 归档（P0-11：hot 豁免，只作用于 warm）──────────────────────────────

async def decay_warm_soft_delete(days: int, batch: int = 5000) -> int:
    """30 天未命中软删：status=4 + active_slot=NULL + vector_synced_at=NULL（入队列推 Qdrant）。

    分批 LIMIT 避免长时间持锁；数据仍在可恢复（软删优于物理删）。
    """
    async with session_maker() as session:
        cur = await session.execute(
            text(
                "UPDATE agent_memory "
                "SET status = 4, active_slot = NULL, vector_synced_at = NULL "
                "WHERE memory_layer = 2 AND status = 1 "
                "AND COALESCE(last_hit_at, created_at) < DATE_SUB(NOW(), INTERVAL :d DAY) "
                "LIMIT :b"
            ),
            {"d": int(days), "b": int(batch)},
        )
        await session.commit()
        return int(cur.rowcount or 0)


async def scan_archivable(days: int, limit: int = 2000) -> list[MemoryEntry]:
    """归档候选：status=4 且软删后再满 60 天（updated_at 口径），valid_to IS NULL 表示尚未导出。"""
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE memory_layer = 2 AND status = 4 AND valid_to IS NULL "
                        "AND updated_at < DATE_SUB(NOW(), INTERVAL :d DAY) "
                        "ORDER BY id LIMIT :l"
                    ),
                    {"d": int(days), "l": int(limit)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


async def mark_archived_exported(ids: list[int]) -> None:
    """归档导出完成：valid_to=NOW()（生效截止，后续归档扫描跳过）。"""
    if not ids:
        return
    async with session_maker() as session:
        await session.execute(
            text("UPDATE agent_memory SET valid_to = NOW() WHERE id IN :ids"),
            {"ids": tuple(int(i) for i in ids)},
        )
        await session.commit()


async def list_tenants() -> list[tuple[str, str]]:
    """枚举有记忆数据的 (app_id, user_id)（替代 v1 的 .data 目录枚举）。"""
    async with session_maker() as session:
        rows = (
            await session.execute(
                text("SELECT DISTINCT app_id, user_id FROM agent_memory")
            )
        ).all()
    return [(str(r[0]), str(r[1])) for r in rows]


# ── 合规删除（P0-10，设计 §6.4）───────────────────────────────────────────────

async def find_for_compliance_delete(
    app_id: str, user_id: str, scope: str, target: str
) -> list[tuple[int, str]]:
    """按范围定位待删除记忆，返回 [(id, memory_id), ...]。

    scope：all=该用户全部 / memory_id=单条 / subject=某主题 / source_msg_id=某条
    消息的派生记忆（source_msg_ids 反查，合规级联的依赖前提）。
    """
    if scope == "all":
        where, params = "", {}
    elif scope == "memory_id":
        where, params = " AND memory_id = :tgt", {"tgt": target}
    elif scope == "subject":
        where, params = " AND subject = :tgt", {"tgt": target}
    elif scope == "source_msg_id":
        # source_msg_ids 为 JSON 数组，NULL 行 JSON_CONTAINS 结果为 NULL 自然排除
        where, params = (
            " AND JSON_CONTAINS(source_msg_ids, JSON_QUOTE(:tgt))", {"tgt": target},
        )
    else:
        raise ValueError(f"未知合规删除范围: {scope}")
    async with session_maker() as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT id, memory_id FROM agent_memory "
                    "WHERE app_id = :a AND user_id = :u" + where
                ),
                {"a": str(app_id), "u": str(user_id), **params},
            )
        ).all()
    return [(int(r[0]), str(r[1])) for r in rows]


async def compliance_hard_delete(app_id: str, user_id: str, ids: list[int]) -> int:
    """监管要求路径：物理硬删（设计 §6.4 ②-硬删）。"""
    if not ids:
        return 0
    async with session_maker() as session:
        cur = await session.execute(
            text("DELETE FROM agent_memory WHERE id IN :ids AND app_id = :a AND user_id = :u"),
            {"ids": tuple(int(i) for i in ids), "a": str(app_id), "u": str(user_id)},
        )
        await session.commit()
    from codegenx.ai_service.memory import hot_cache
    await hot_cache.invalidate(str(app_id), str(user_id))
    return int(cur.rowcount or 0)


async def compliance_soft_delete_redact(app_id: str, user_id: str, ids: list[int]) -> int:
    """业务留痕路径：status=3 + 内容脱敏（summary/content 置固定占位）。

    active_slot 必须同语句置 NULL（uk_slot 槽位释放），vector_synced_at
    置 NULL 让 Worker 清理 Qdrant 侧状态。
    """
    if not ids:
        return 0
    redacted = json.dumps({"text": "[已删除]", "topic": ""}, ensure_ascii=False)
    async with session_maker() as session:
        cur = await session.execute(
            text(
                "UPDATE agent_memory "
                "SET status = 3, active_slot = NULL, summary = '[已删除]', content = :c, "
                "    vector_synced_at = NULL "
                "WHERE id IN :ids AND app_id = :a AND user_id = :u"
            ),
            {"c": redacted, "ids": tuple(int(i) for i in ids), "a": str(app_id), "u": str(user_id)},
        )
        await session.commit()
    from codegenx.ai_service.memory import hot_cache
    await hot_cache.invalidate(str(app_id), str(user_id))
    return int(cur.rowcount or 0)


async def count_active(app_id: str, user_id: str) -> int:
    """删除校验用：该用户 active 记忆数（合规删除后应为 0，scope=all 时）。"""
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_memory "
                    "WHERE app_id = :a AND user_id = :u AND status = 1"
                ),
                {"a": str(app_id), "u": str(user_id)},
            )
        ).first()
    return int(row[0] or 0) if row else 0


# ── 管理/排查查询（P2-7 排查工具五件套 + P2-5 管理后台）────────────────────────

async def get_by_memory_ids(
    app_id: str, user_id: str, memory_ids: list[str]
) -> list[MemoryEntry]:
    """按 memory_id 批量取完整行（uk_memory_id 唯一；租户过滤防越权）。

    P2-7 溯源反查 / P2-5 编辑前取旧条共用。
    """
    if not memory_ids:
        return []
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT {_ROW_COLS} FROM agent_memory "
                        "WHERE app_id = :a AND user_id = :u AND memory_id IN :mids"
                    ),
                    {"a": str(app_id), "u": str(user_id), "mids": tuple(memory_ids)},
                )
            )
            .mappings()
            .all()
        )
    return _rows_to_entries(rows)


async def list_memories(
    app_id: str,
    user_id: str,
    *,
    status: int = 1,
    memory_layer: int | None = None,
    memory_type: str | None = None,
    limit: int = 200,
) -> list[MemoryEntry]:
    """按租户列出记忆（P2-7 ① 全貌查询；status≤0 表示不过滤状态看全部）。"""
    sql = f"SELECT {_ROW_COLS} FROM agent_memory WHERE app_id = :a AND user_id = :u"
    params: dict = {"a": str(app_id), "u": str(user_id), "l": int(limit)}
    if status and int(status) > 0:
        sql += " AND status = :st"
        params["st"] = int(status)
    if memory_layer:
        sql += " AND memory_layer = :ml"
        params["ml"] = int(memory_layer)
    if memory_type:
        sql += " AND memory_type = :mt"
        params["mt"] = str(memory_type)
    sql += " ORDER BY memory_layer ASC, updated_at DESC LIMIT :l"
    async with session_maker() as session:
        rows = (
            (await session.execute(text(sql), params)).mappings().all()
        )
    return _rows_to_entries(rows)


async def reset_vector_sync(app_id: str, user_id: str) -> int:
    """单用户向量重建入队（P2-7 ⑤）：active warm 行 vector_synced_at 全部置 NULL，
    同步 Worker（30s）自动从 MySQL 全量重灌 Qdrant。返回入队行数。"""
    async with session_maker() as session:
        cur = await session.execute(
            text(
                "UPDATE agent_memory SET vector_synced_at = NULL "
                "WHERE app_id = :a AND user_id = :u AND memory_layer = 2 AND status = 1"
            ),
            {"a": str(app_id), "u": str(user_id)},
        )
        await session.commit()
        return int(cur.rowcount or 0)
