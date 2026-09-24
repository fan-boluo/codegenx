"""hot 层压缩（P2-2，设计方案 §11-P2-2「同类合并、抽象提升」）。

补全 P0 留下的半截链路：hot 达硬上限时 writer 已转投 consolidate 任务
（writer.py _enqueue_consolidate），但 consolidate 原本只合并 warm 重复，
hot 只进不出——活跃用户的 hot 层迟早写满，之后新约束/偏好被持续拒绝。

压缩策略（保守优先，P0-11 教训：hot 是正确性层，宁可不压不可压错）：
  - 只在 active 条数达 hot_max_entries × threshold_ratio（默认 80%）时触发，
    压到阈值下即停，不追求极简；
  - 按 (subject, memory_type) 分组，组内 ≥2 条才可压缩；大组优先；
  - hard_constraint 默认豁免（hot_compress_exclude_types，逗号分隔可调）；
  - LLM 合并要求逐条保留语义，输出 merge=false / 解析失败 / 内容为空
    一律放弃该组（宁缺毋滥）；
  - 新条 source_type=3（SOURCE_MANUAL，系统变换语义——内容源自用户明说，
    但非用户原话逐字，也不是模型新推断，不得按推断处理以免被逐出 hot）；
  - slot_key 取组内最老条的槽位（后续同槽写入自然 upsert 到压缩条上）；
  - 峰值处理：插入压缩条时临时放宽上限 len(group)-1（插完立刻失效组内
    旧条，净减 group-1 条）——满员时恰恰最需要压缩，不能因上限被拒死。

触发：scheduler._do_consolidate 每日执行（warm 合并之后），LLM 与
conflict_detect 共用同一 invoke（凌晨 idle 时段，成本可控）。
"""
from __future__ import annotations

from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory import metrics
from codegenx.ai_service.memory.models import (
    MemoryEntry,
    SOURCE_MANUAL,
    STATUS_SUPERSEDED,
    estimate_text_tokens,
)
from codegenx.ai_service.memory.memory_store import (
    load_hot,
    mark_inactive,
    upsert_hot_slot,
)
from codegenx.ai_service.memory.prompts import (
    HOT_COMPRESS_SYSTEM_PROMPT,
    format_constraints_for_conflict,
    parse_compressed_memory,
)

# 单租户单次压缩的 LLM 组数上限（封顶成本，剩余留给次日）
_MAX_GROUPS_PER_RUN = 10


def _compress_config() -> tuple[bool, int, float, set[str]]:
    cfg = config.memory.store
    enabled = bool(getattr(cfg, "hot_compress_enabled", True))
    hot_max = int(getattr(cfg, "hot_max_entries", 80) or 80)
    ratio = float(getattr(cfg, "hot_compress_threshold_ratio", 0.8) or 0.8)
    exclude = {
        t.strip() for t in str(getattr(cfg, "hot_compress_exclude_types", "") or "").split(",")
        if t.strip()
    }
    return enabled, hot_max, ratio, exclude


async def compress_hot_all_apps(invoke_llm) -> dict:
    """全租户 hot 压缩（scheduler._do_consolidate 调用）。单租户失败不阻断。"""
    from codegenx.ai_service.memory.memory_store import list_tenants
    try:
        tenants = await list_tenants()
    except Exception as exc:  # noqa: BLE001 — MySQL 不可用本轮放弃
        log.error("[hot_compress] 租户枚举失败:{}", exc)
        return {}
    total = {"tenants_compressed": 0, "groups_compressed": 0, "entries_merged": 0}
    for app_id, user_id in tenants:
        try:
            stats = await compress_hot(app_id, user_id, invoke_llm)
            if stats.get("groups_compressed"):
                total["tenants_compressed"] += 1
                total["groups_compressed"] += int(stats["groups_compressed"])
                total["entries_merged"] += int(stats["entries_merged"])
        except Exception as exc:  # noqa: BLE001
            log.error("[hot_compress] app {}/{} 压缩异常: {}", app_id, user_id, exc)
    if total["groups_compressed"]:
        metrics.log_event(
            "hot_compress_done",
            tenants=total["tenants_compressed"],
            groups=total["groups_compressed"],
            entries_merged=total["entries_merged"],
        )
    return total


async def compress_hot(app_id: str, user_id: str, invoke_llm) -> dict:
    """单租户 hot 压缩。返回统计（groups_compressed / entries_merged / skipped）。"""
    enabled, hot_max, ratio, exclude_types = _compress_config()
    stats = {"groups_compressed": 0, "entries_merged": 0, "skipped": ""}
    if not enabled:
        stats["skipped"] = "disabled"
        return stats

    entries = await load_hot(app_id, user_id)
    threshold = max(1, int(hot_max * ratio))
    if len(entries) < threshold:
        stats["skipped"] = f"below_threshold({len(entries)}/{threshold})"
        return stats

    # 分组：仅可压缩类型、组内 ≥2 条；大组优先
    groups: dict[tuple[str, str], list[MemoryEntry]] = {}
    for e in entries:
        if e.memory_type in exclude_types:
            continue
        groups.setdefault((e.subject, e.memory_type), []).append(e)
    candidate_groups = sorted(
        (items for items in groups.values() if len(items) >= 2),
        key=len, reverse=True,
    )
    if not candidate_groups:
        stats["skipped"] = "no_compressible_group"
        return stats

    current_count = len(entries)
    for group in candidate_groups:
        if current_count <= threshold or stats["groups_compressed"] >= _MAX_GROUPS_PER_RUN:
            break
        subject, memory_type = group[0].subject, group[0].memory_type
        merged = await _merge_group(app_id, user_id, invoke_llm, memory_type, group)
        if merged is None:
            continue
        # slot_key 取组内最老条：后续同槽写入自然落在压缩条上
        oldest = min(group, key=lambda e: e.created_at or "")
        new_entry = MemoryEntry(
            app_id=str(app_id),
            user_id=str(user_id),
            memory_type=memory_type,
            subject=subject,
            slot_key=oldest.slot_key or oldest.memory_id,
            summary=merged["summary"][:500],
            content=merged["content"],
            topic=merged["topic"][:32],
            token_cost=estimate_text_tokens(merged["content"]) + 4,
            source_type=SOURCE_MANUAL,  # 系统变换（见模块 docstring），非推断
            confidence=1.00,
        )
        try:
            # 临时放宽上限 len(group)-1：插完立刻失效组内旧条（净减），见 docstring
            new_id = await upsert_hot_slot(
                app_id, user_id, new_entry,
                hot_max_entries=hot_max + len(group) - 1,
                consolidate_enqueuer=None,
            )
        except Exception as exc:  # noqa: BLE001 — 单组失败继续下一组
            log.error("[hot_compress] app {}/{} 压缩条写入失败: {}", app_id, user_id, exc)
            continue
        if not new_id:
            stats["skipped"] = "insert_rejected"
            continue
        # 组内旧条全部失效（同槽那条已被 upsert 置 superseded，此处幂等）
        await mark_inactive(
            app_id, user_id, [e.memory_id for e in group],
            STATUS_SUPERSEDED, superseded_by=new_entry.memory_id,
        )
        current_count -= len(group) - 1
        stats["groups_compressed"] += 1
        stats["entries_merged"] += len(group)
        metrics.log_event(
            "hot_compressed", app_id=str(app_id), user_id=str(user_id),
            memory_type=memory_type, merged_from=len(group),
            new_memory_id=new_entry.memory_id,
        )
        log.info(
            "[hot_compress] app {}/{} 类型 {} 合并 {} 条 → 1 条（当前 {}）",
            app_id, user_id, memory_type, len(group), current_count,
        )
    return stats


async def _merge_group(
    app_id: str, user_id: str, invoke_llm,
    memory_type: str, group: list[MemoryEntry],
) -> dict | None:
    """LLM 合并一组同类记忆；放弃（merge=false / 解析失败 / LLM 异常）返回 None。"""
    try:
        raw = await invoke_llm(
            messages=[
                {"role": "system", "content": HOT_COMPRESS_SYSTEM_PROMPT},
                {"role": "user", "content": format_constraints_for_conflict(
                    [f"({memory_type}) {e.inject_text()[:200]}" for e in group]
                )},
            ],
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001 — LLM 故障放弃该组，等下次运行
        log.warning("[hot_compress] app {}/{} LLM 合并失败（放弃该组）: {}", app_id, user_id, exc)
        return None
    merged = parse_compressed_memory(raw)
    if merged is None:
        log.debug("[hot_compress] app {}/{} 组 {} LLM 判定不可合并或输出不可解析", app_id, user_id, memory_type)
        return None
    return merged
