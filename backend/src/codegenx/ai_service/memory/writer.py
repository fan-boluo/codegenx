"""
warm 层写入器 —— 条件触发的记忆落库（json 事实源优先，Qdrant 跟进）。

单条候选记忆的写入决策（设计文档 §5.2）：
  1. 向量检索最相似的旧记忆（top longMatchesTopK）
  2. 相似度 ≥ shortDuplicatedScoreThreshold(0.90) → 判重复，跳过
  3. 相似度 ∈ [longMatchesScoreThreshold(0.70), 0.90) → LLM 仲裁
     DUPLICATE 跳过 / UPDATE 合并改写 / CONFLICT 新胜旧失效 / KEEP_BOTH 都留
  4. 相似度 < 0.70 → 视为无匹配，直接写入

写入顺序：先写 jsonl（事实源），再 upsert Qdrant（失败留给 sync_check 修复，
不回滚 —— json 侧成立即写入成功）。
"""
from __future__ import annotations

from typing import Awaitable, Callable

from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory.models import MemoryEntry, MEMORY_TYPE_WEIGHTS
from codegenx.ai_service.memory.embedding import get_embedding_client
from codegenx.ai_service.memory.vector_store import search_by_vector, upsert_entries, mark_status
from codegenx.ai_service.memory.warm_store import get_warm_store
from codegenx.ai_service.memory.prompts import (
    MEMORY_ADJUDICATE_SYSTEM_PROMPT,
    format_adjudicate_user_prompt,
    parse_adjudication,
)

# LLM 调用签名：由调用方注入（离线任务走 scheduler 的受控小模型通道）
LlmInvoke = Callable[..., Awaitable[str]]


async def write_memories(
    user_id: str,
    app_id: str,
    session_id: str,
    candidates: list[dict],
    llm_invoke: LlmInvoke,
) -> int:
    """批量写入候选记忆，返回实际落库条数。单条失败跳过，不阻断整批。"""
    if not candidates:
        return 0
    store_cfg = config.memory.store
    written = 0
    pending_upsert: list[tuple[MemoryEntry, list[float]]] = []

    for cand in candidates:
        try:
            count = await _write_one(
                user_id=user_id,
                app_id=app_id,
                session_id=session_id,
                candidate=cand,
                llm_invoke=llm_invoke,
                top_k=int(store_cfg.longMatchesTopK or 3),
                dup_threshold=float(store_cfg.shortDuplicatedScoreThreshold or 0.90),
                match_threshold=float(store_cfg.longMatchesScoreThreshold or 0.7),
                pending_upsert=pending_upsert,
            )
            written += count
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批
            log.error("记忆写入单条失败（跳过）: {} | {}", exc, str(cand)[:120])

    # jsonl 已全部落盘，向量层批量跟进（失败不回滚，sync_check 会对账补写）
    if pending_upsert:
        try:
            await upsert_entries(
                [e for e, _ in pending_upsert],
                user_id,
                app_id,
                [v for _, v in pending_upsert],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("warm 向量层批量 upsert 失败（留待 sync_check 修复）:{}", exc)

    return written


async def _write_one(
    user_id: str,
    app_id: str,
    session_id: str,
    candidate: dict,
    llm_invoke: LlmInvoke,
    top_k: int,
    dup_threshold: float,
    match_threshold: float,
    pending_upsert: list[tuple[MemoryEntry, list[float]]],
) -> int:
    content = str(candidate.get("content", "") or "").strip()
    if not content:
        return 0
    memory_type = str(candidate.get("memory_type", "") or "").strip()
    if memory_type not in MEMORY_TYPE_WEIGHTS:
        memory_type = "project_background"  # 非法类型兜底为权重最低档
    topic = str(candidate.get("topic", "") or "").strip()

    # ── 1. 相似检索 ────────────────────────────────────────────────────────────
    matches: list[tuple[MemoryEntry, float]] = []
    query_vector: list[float] = []
    try:
        query_vector = await get_embedding_client().embed_query(content)
        matches = await search_by_vector(user_id, app_id, query_vector, limit=top_k)
    except Exception as exc:  # noqa: BLE001 — 检索失败按无匹配处理（写入仍继续）
        log.warning("写入前相似检索失败（按无匹配处理）:{}", exc)

    best_score = matches[0][1] if matches else 0.0

    # ── 2/3/4. 决策 ───────────────────────────────────────────────────────────
    supersede_ids: list[str] = []
    final_content = content

    if matches and best_score >= dup_threshold:
        log.debug("记忆判重复（score={:.2f}），跳过:{}", best_score, content[:50])
        return 0

    if matches and best_score >= match_threshold:
        adjudication = await _adjudicate(
            llm_invoke, content,
            [(e.id, e.content) for e, _ in matches],
        )
        action = adjudication["action"]
        if action == "DUPLICATE":
            return 0
        if action == "UPDATE":
            final_content = adjudication["content"] or content
            supersede_ids = [matches[0][0].id]  # 只改写最相似的一条
        elif action == "CONFLICT":
            supersede_ids = [matches[0][0].id]
        # KEEP_BOTH → 直接落新条

    # ── 落库：jsonl（事实源）先行 ───────────────────────────────────────────────
    entry = MemoryEntry(
        layer="warm",
        topic=topic,
        memory_type=memory_type,
        content=final_content,
        source_session_id=session_id,
    )
    get_warm_store(user_id, app_id).append(entry)

    # 旧记忆失效：jsonl 侧重写 + Qdrant 状态同步（后者失败留 sync_check）
    if supersede_ids:
        get_warm_store(user_id, app_id).mark_invalid(supersede_ids, f"superseded:{entry.id}")
        try:
            await mark_status(supersede_ids, "invalid")
        except Exception as exc:  # noqa: BLE001
            log.warning("旧记忆 Qdrant 失效同步失败（留待 sync_check）:{}", exc)

    pending_upsert.append((entry, query_vector))
    return 1


async def _adjudicate(
    llm_invoke: LlmInvoke,
    new_content: str,
    matches: list[tuple[str, str]],
) -> dict:
    """LLM 仲裁新记忆与相似旧记忆的关系；失败按 KEEP_BOTH 保守处理。"""
    try:
        raw = await llm_invoke(
            messages=[
                {"role": "system", "content": MEMORY_ADJUDICATE_SYSTEM_PROMPT},
                {"role": "user", "content": format_adjudicate_user_prompt(new_content, matches)},
            ],
            max_tokens=512,
        )
        return parse_adjudication(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("记忆仲裁 LLM 调用失败（按 KEEP_BOTH）:{}", exc)
        return {"action": "KEEP_BOTH", "content": ""}
