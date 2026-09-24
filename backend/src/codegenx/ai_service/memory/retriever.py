"""
warm 层检索器 —— 向量召回 → 应用层重排 → 回表（设计方案 v2.1 §5.1/§5.2）。

链路（P0-8 瘦 payload 后的重排必须在应用层做）：
  1. Qdrant search top-N（强制租户 filter，N 明显大于 k）
  2. MySQL summary LIKE 兜底通道（标识符/表名/路径等 embedding 弱项；
     正文不进 payload 后，v1 的 Qdrant MatchText 通道由该查询替代）
  3. 按 agent_memory.id 回表取 summary/content，MySQL 侧再过滤 status=1
     （Qdrant payload 可能滞后，尽力而为的过滤必须复核）
  4. 应用层重排 final = 0.7×sim_norm + 0.2×decay + 0.1×type_weight
     - sim_norm = (cosine_score + 1) / 2，余弦 [-1,1] 归一到 [0,1]
     - decay = 0.5^(age_days / half_life)，age 按 COALESCE(last_hit_at, created_at)，
       half_life 按类型（memory_type_dict.half_life_d 的代码内镜像）
  5. token 预算窗口截断（warm 是概率性召回，少一条不影响正确性）
  6. 命中登记（hit_count/last_hit_at；P1-7 改 Redis 聚合，现为同步批量刷回）

Qdrant 不可用时向量通道降级为空，关键词通道仍可用（无 embedding 依赖）。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory.models import MemoryEntry, estimate_text_tokens, half_life_of
from codegenx.ai_service.memory.memory_store import (
    get_active_by_ids,
    search_summary_keyword,
    batch_touch_hit,
)
from codegenx.ai_service.memory.vector_store import search_by_vector

KEYWORD_QUERY_MAX_CHARS = 64  # 关键词兜底通道的查询截断长度
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{3,}")
_KEYWORD_FALLBACK_SCORE = 0.35  # 关键词命中的保底语义分（未归一口径，rerank 统一归一）


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _time_decay(entry: MemoryEntry, now: datetime) -> float:
    """半衰期指数衰减（§5.2）；反复命中的记忆以 last_hit_at 保持热度。"""
    ref = _parse_iso(entry.last_hit_at or entry.created_at)
    if ref is None:
        return 0.0
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - ref).total_seconds() / 86400.0)
    return 0.5 ** (age_days / max(1, half_life_of(entry.memory_type)))


def build_keyword_query(query: str) -> str:
    """关键词兜底通道的匹配文本：优先标识符（路径/表名），否则原文截断。"""
    identifiers = _IDENTIFIER_RE.findall(query or "")
    if identifiers:
        identifiers.sort(key=len, reverse=True)
        return " ".join(identifiers[:5])
    return (query or "").strip()[:KEYWORD_QUERY_MAX_CHARS]


def rerank(candidates: list[tuple[MemoryEntry, float]]) -> list[tuple[MemoryEntry, float]]:
    """三因子重排：0.7×sim_norm + 0.2×decay + 0.1×type_weight（§5.2）。"""
    cfg = config.memory.search
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, MemoryEntry]] = []
    for entry, semantic in candidates:
        sim_norm = max(0.0, min(1.0, (semantic + 1) / 2))
        decay = _time_decay(entry, now)
        final = (
            cfg.rerank_vector_weight * sim_norm
            + cfg.rerank_time_weight * decay
            + cfg.rerank_type_weight * entry.type_weight
        )
        scored.append((final, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(entry, score) for score, entry in scored]


async def search_warm(app_id: str, user_id: str, query: str) -> list[MemoryEntry]:
    """混合召回主入口：召回 → 合并 → 回表 → 重排 → token 窗口 → 命中登记。"""
    cfg = config.memory.search
    if not cfg.enabled or not (query or "").strip():
        return []

    # ── 双路召回 ───────────────────────────────────────────────────────────────
    semantic: dict[int, float] = {}

    async def _vector_channel() -> None:
        try:
            query_vector = await get_embedding_client().embed_query(query)
        except Exception as exc:  # noqa: BLE001 — 向量通道失败降级，不影响关键词通道
            log.warning("warm 向量通道 embedding 失败，仅用关键词通道:{}", exc)
            return
        for pid, score in await search_by_vector(
            app_id=app_id,
            user_id=user_id,
            query_vector=query_vector,
            limit=cfg.top_k,
            score_threshold=cfg.score_threshold,
        ):
            semantic[pid] = score

    keyword_query = build_keyword_query(query)

    async def _keyword_channel() -> list[MemoryEntry]:
        if not keyword_query:
            return []
        try:
            return await search_summary_keyword(
                app_id=app_id, user_id=user_id,
                keyword=keyword_query, limit=cfg.keyword_top_k,
            )
        except Exception as exc:  # noqa: BLE001 — 同上，降级为空
            log.error("warm 关键词通道失败:{}", exc)
            return []

    await _vector_channel()
    keyword_entries = await _keyword_channel()

    # ── 合并去重（向量分优先保留）+ 回表（§5.1：MySQL 侧再过滤 status=1）───────
    merged: dict[int, tuple[MemoryEntry, float]] = {e.id: (e, _KEYWORD_FALLBACK_SCORE) for e in keyword_entries}
    vector_ids = [pid for pid in semantic if pid not in merged]
    if vector_ids:
        try:
            for e in await get_active_by_ids(app_id, user_id, vector_ids):
                if e.id not in merged:
                    merged[e.id] = (e, semantic.get(e.id, 0.0))
        except Exception as exc:  # noqa: BLE001
            log.error("warm 回表失败:{}", exc)
    if not merged:
        return []

    ranked = rerank(list(merged.values()))

    # ── token 窗口截断（§5.4：warm 按分数降序累加，超预算即停）──────────────────
    budget = cfg.warm_token_budget
    selected: list[MemoryEntry] = []
    used = 0
    for entry, _score in ranked:
        cost = entry.token_cost or (estimate_text_tokens(entry.inject_text()) + 8)
        if used + cost > budget:
            break
        selected.append(entry)
        used += cost

    # ── 命中登记（衰减与软删依据；过渡同步刷回，P1-7 改 Redis 聚合）─────────────
    if selected:
        await batch_touch_hit(app_id, user_id, [e.id for e in selected])

    return selected


def format_warm_entries_prompt(entries: list[MemoryEntry]) -> str:
    """warm 层注入格式：按话题分组展示，控制重复感。"""
    if not entries:
        return ""
    by_topic: dict[str, list[MemoryEntry]] = {}
    for e in entries:
        by_topic.setdefault(e.topic or "其他", []).append(e)
    parts = ["# 相关记忆（跨会话沉淀，按相关性排序）"]
    for topic, items in by_topic.items():
        parts.append(f"\n## {topic}")
        for e in items:
            parts.append(f"- {e.inject_text()}")
    return "\n".join(parts)
