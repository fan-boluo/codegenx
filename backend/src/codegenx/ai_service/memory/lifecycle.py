"""
记忆生命周期管理 —— 跨会话整理（consolidate）与衰减归档（decay_archive）。

每日由 MemoryScheduler 定时触发（idle 时段），纯规则处理、不耗 LLM：

  consolidate_all_apps      近似去重：内容相同的旧条目置 invalid（保留最新）
  decay_and_archive_all_apps
    - 软删除：active 条目 last_accessed_at 超过 30 天 → invalid（jsonl + Qdrant）
    - 归档：created_at 超过 90 天的条目 → jsonl 移入按月 zip + Qdrant 物理删除
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shared import log
from shared.constants import DATA_ROOT_DIR
from codegenx.ai_service.utils.config import config

from codegenx.ai_service.memory.models import MemoryEntry, STATUS_ACTIVE, STATUS_INVALID
from codegenx.ai_service.memory.warm_store import get_warm_store
from codegenx.ai_service.memory.vector_store import mark_status, delete_points


def _decay_days() -> int:
    return int(getattr(config.memory.store, "decay_days", 30) or 30)


def _archive_days() -> int:
    return int(getattr(config.memory.store, "archive_days", 90) or 90)


def list_user_app_pairs() -> list[tuple[str, str]]:
    """枚举 .data 下的 用户/项目 两级目录（记忆按 user/app 维度存储）。"""
    if not DATA_ROOT_DIR.exists():
        return []
    pairs: list[tuple[str, str]] = []
    for user_dir in DATA_ROOT_DIR.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        for app_dir in user_dir.iterdir():
            if app_dir.is_dir() and not app_dir.name.startswith("."):
                pairs.append((user_dir.name, app_dir.name))
    return pairs


async def consolidate_all_apps() -> None:
    for user_id, app_id in list_user_app_pairs():
        try:
            removed = await consolidate_app(user_id, app_id)
            if removed:
                log.info("[consolidate] app {}/{} 去重 {} 条", user_id, app_id, removed)
        except Exception as exc:  # noqa: BLE001 — 单 app 失败不阻断其他 app
            log.error("[consolidate] app {}/{} 整理异常: {}", user_id, app_id, exc)


async def decay_and_archive_all_apps() -> None:
    for user_id, app_id in list_user_app_pairs():
        try:
            decayed, archived = await decay_and_archive_app(user_id, app_id)
            if decayed or archived:
                log.info("[decay_archive] app {}/{} 软删 {} 条，归档 {} 条", user_id, app_id, decayed, archived)
        except Exception as exc:  # noqa: BLE001
            log.error("[decay_archive] app {}/{} 异常: {}", user_id, app_id, exc)


async def consolidate_app(user_id: str, app_id: str) -> int:
    """单 app 近似去重：同内容（忽略首尾空白）条目只保留最新一条。"""
    store = get_warm_store(user_id, app_id)
    entries = store.scan_entries(status=STATUS_ACTIVE)
    if len(entries) < 2:
        return 0

    # 追加序扫描（ULID 单调），后者更新：先出现的重复条目失效
    seen: dict[str, str] = {}  # normalized content → 保留的 entry id
    stale_ids: list[str] = []
    for entry in entries:
        key = " ".join(entry.content.split())
        keep_id = seen.get(key)
        if keep_id is None:
            seen[key] = entry.id
        else:
            stale_ids.append(entry.id)

    if not stale_ids:
        return 0

    store.mark_invalid(stale_ids, "consolidate:duplicate")
    try:
        await mark_status(stale_ids, STATUS_INVALID)
    except Exception as exc:  # noqa: BLE001 — Qdrant 失败留给 sync_check
        log.warning("[consolidate] Qdrant 失效同步失败（留待 sync_check）:{}", exc)
    return len(stale_ids)


async def decay_and_archive_app(user_id: str, app_id: str) -> tuple[int, int]:
    """单 app 衰减 + 归档。返回 (软删条数, 归档条数)。"""
    now = datetime.now(timezone.utc)
    store = get_warm_store(user_id, app_id)

    # ── 软删除：active 且 last_accessed_at（缺省 created_at）超 decay_days ──────
    decay_ids: list[str] = []
    for entry in store.scan_entries(status=STATUS_ACTIVE):
        ref = entry.last_accessed_at or entry.created_at
        ref_dt = _parse(ref)
        if ref_dt is None:
            continue
        if now - ref_dt > timedelta(days=_decay_days()):
            decay_ids.append(entry.id)
    if decay_ids:
        store.mark_invalid(decay_ids, f"decay:{_decay_days()}d")
        try:
            await mark_status(decay_ids, STATUS_INVALID)
        except Exception as exc:  # noqa: BLE001
            log.warning("[decay_archive] Qdrant 软删同步失败（留待 sync_check）:{}", exc)

    # ── 归档：created_at 超 archive_days 的全部条目 → zip + Qdrant 物理删除 ────
    cutoff = (now - timedelta(days=_archive_days())).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    archived_ids = store.archive_before(cutoff)
    if archived_ids:
        try:
            await delete_points(archived_ids)
        except Exception as exc:  # noqa: BLE001 — 点位已随 zip 离开 jsonl，重删无害
            log.warning("[decay_archive] Qdrant 归档删除失败:{}", exc)
    return len(decay_ids), len(archived_ids)


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
