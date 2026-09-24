"""
warm 层检索器 —— 混合召回 + 三因子重排 + token 窗口。

召回（两路并行）：
  向量通道   embedding(query) → Qdrant search（app + active 过滤，top_k/阈值可配）
  关键词通道 query 全文匹配 content（MatchText），补足精确命中（路径、表名、标识符）

重排（设计文档 §5.3）：
  score = 0.7×语义相似度 + 0.2×时间衰减 + 0.1×类型权重
  时间衰减 = 0.5^(距 created_at 天数 / 半衰期30天)

产出：按 token 预算（默认 8K）截断的条目列表 + 召回命中登记（jsonl 缓冲 + Qdrant touch）。
Qdrant 不可用时向量通道降级为空，关键词通道仍可用（无 embedding 依赖）。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory.embedding import get_embedding_client
from codegenx.ai_service.memory.models import MemoryEntry, estimate_text_tokens
from codegenx.ai_service.memory.vector_store import (
    scroll_by_keyword,
    search_by_vector,
    touch_access,
)
from codegenx.ai_service.memory.warm_store import get_warm_store

KEYWORD_QUERY_MAX_CHARS = 64  # MatchText 用查询截断长度
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{3,}")


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _time_decay(entry: MemoryEntry, now: datetime, half_life_days: int) -> float:
    created = _parse_iso(entry.created_at)
    if created is None:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    return 0.5 ** (age_days / max(1, half_life_days))


def build_keyword_query(query: str) -> str:
    """从用户查询提取关键词通道的匹配文本：优先标识符（路径/表名），否则原文截断。"""
    identifiers = _IDENTIFIER_RE.findall(query or "")
    if identifiers:
        # 标识符按长度降序取最具体的若干，空格连接（MatchText 全文匹配）
        identifiers.sort(key=len, reverse=True)
        return " ".join(identifiers[:5])
    return (query or "").strip()[:KEYWORD_QUERY_MAX_CHARS]


def rerank(
    candidates: list[tuple[MemoryEntry, float]],
) -> list[tuple[MemoryEntry, float]]:
    """三因子重排：0.7×语义 + 0.2×时间衰减 + 0.1×类型权重。"""
    search_cfg = config.memory.search
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, MemoryEntry]] = []
    for entry, semantic in candidates:
        decay = _time_decay(entry, now, search_cfg.time_decay_half_life_days)
        final = (
            search_cfg.rerank_vector_weight * semantic
            + search_cfg.rerank_time_weight * decay
            + search_cfg.rerank_type_weight * entry.type_weight
        )
        scored.append((final, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(entry, score) for score, entry in scored]


async def search_warm(app_id: str, query: str) -> list[MemoryEntry]:
    """混合召回主入口：召回 → 去重 → 重排 → token 窗口 → 访问登记。"""
    search_cfg = config.memory.search
    if not search_cfg.enabled or not (query or "").strip():
        return []

    # ── 两路召回 ───────────────────────────────────────────────────────────────
    vector_hits: list[tuple[MemoryEntry, float]] = []
    keyword_hits: list[MemoryEntry] = []
    keyword_query = build_keyword_query(query)

    async def _vector_channel() -> list[tuple[MemoryEntry, float]]:
        try:
            query_vector = await get_embedding_client().embed_query(query)
        except Exception as exc:  # noqa: BLE001 — 向量通道失败降级，不影响关键词通道
            log.warning("warm 向量通道 embedding 失败，仅用关键词通道:{}", exc)
            return []
        return await search_by_vector(
            app_id=app_id,
            query_vector=query_vector,
            limit=search_cfg.top_k,
            score_threshold=search_cfg.score_threshold,
        )

    async def _keyword_channel() -> list[MemoryEntry]:
        return await scroll_by_keyword(
            app_id=app_id,
            keyword=keyword_query,
            limit=search_cfg.keyword_top_k,
        )

    vector_hits, keyword_hits = await asyncio.gather(_vector_channel(), _keyword_channel())

    # ── 合并去重（向量分优先保留） ─────────────────────────────────────────────
    merged: dict[str, tuple[MemoryEntry, float]] = {}
    for entry, score in vector_hits:
        merged[entry.id] = (entry, score)
    for entry in keyword_hits:
        if entry.id not in merged:
            merged[entry.id] = (entry, 0.35)  # 关键词命中给保底语义分

    if not merged:
        return []

    ranked = rerank(list(merged.values()))

    # ── token 窗口截断 ─────────────────────────────────────────────────────────
    budget = search_cfg.warm_token_budget
    selected: list[MemoryEntry] = []
    used = 0
    for entry, _score in ranked:
        cost = estimate_text_tokens(entry.content) + 8  # 8 = 条目包装开销
        if used + cost > budget:
            break
        selected.append(entry)
        used += cost

    # ── 访问登记（缓冲写 jsonl + 异步 touch Qdrant，均尽力而为） ────────────────
    if selected:
        get_warm_store(app_id).record_access([e.id for e in selected])
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        try:
            await touch_access([e.id for e in selected], now_iso)
        except Exception as exc:  # noqa: BLE001
            log.debug("warm touch_access 失败（非致命）:{}", exc)

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
            parts.append(f"- {e.content}")
    return "\n".join(parts)
