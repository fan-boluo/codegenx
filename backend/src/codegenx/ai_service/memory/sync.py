"""
双数据源对账 —— json/jsonl 为真相源，Qdrant 为可重建的检索层。

sync_check 任务（每日 / 可手动触发）从 memory_sync_checkpoint 记录的
last_entry_id 起（ULID 字典序 = 追加序）增量扫描 json 侧：

  active 条目 → 未入库则向量化补写
  invalid/archived 条目 → Qdrant 状态同步为 invalid（归档点位由 decay_archive 删除）

Qdrant collection 若被清空/损坏：清掉检查点记录后重跑本任务即全量重建。
"""
from __future__ import annotations

from shared import log

from codegenx.ai_service.memory.models import MemoryEntry, STATUS_ACTIVE
from codegenx.ai_service.memory.embedding import get_embedding_client
from codegenx.ai_service.memory.vector_store import (
    mark_status,
    upsert_entries,
)
from codegenx.ai_service.memory.warm_store import get_warm_store
from codegenx.ai_service.schedule.memory_task_store import get_memory_task_store
from codegenx.ai_service.memory.lifecycle import list_user_app_pairs


async def reconcile_all_apps(llm_invoke=None) -> None:
    """全 app 对账（llm_invoke 参数保留以对齐 scheduler 签名，当前规则处理不耗 LLM）。"""
    for user_id, app_id in list_user_app_pairs():
        try:
            fixed = await reconcile_app(user_id, app_id)
            if fixed:
                log.info("[sync_check] app {}/{} 补写/修正 {} 个点位", user_id, app_id, fixed)
        except Exception as exc:  # noqa: BLE001 — 单 app 失败不阻断
            log.error("[sync_check] app {}/{} 对账异常: {}", user_id, app_id, exc)


async def reconcile_app(user_id: str, app_id: str) -> int:
    """单 app 增量对账：检查点之后的变化同步到 Qdrant，返回处理条数。"""
    tasks = get_memory_task_store()
    store = get_warm_store(user_id, app_id)
    checkpoint = await tasks.get_checkpoint(app_id, "warm", user_id=user_id)
    entries = store.scan_entries(after_id=checkpoint, status=None)
    if not entries:
        return 0

    to_upsert: list[MemoryEntry] = [e for e in entries if e.status == STATUS_ACTIVE]
    to_invalidate: list[str] = [e.id for e in entries if e.status != STATUS_ACTIVE]
    fixed = 0

    if to_upsert:
        vectors = await get_embedding_client().embed_texts([e.content for e in to_upsert])
        await upsert_entries(to_upsert, user_id, app_id, vectors)
        fixed += len(to_upsert)

    if to_invalidate:
        # set_payload 对不存在的点位是 no-op，重复同步无害
        await mark_status(to_invalidate, "invalid")
        fixed += len(to_invalidate)

    # 检查点推进到最后一条（扫描序 = ULID 序）
    await tasks.advance_checkpoint(app_id, "warm", entries[-1].id, user_id=user_id)
    return fixed
