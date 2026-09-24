"""
warm_memories 向量检索层（Qdrant）。

定位：json/jsonl 是事实源，Qdrant 只是检索加速层 —— 任何时刻可以清空 collection
后从 jsonl 全量重建（sync.py 负责对账）。本模块只做读写原语，不做业务决策：

  ensure_warm_collection   建库 + payload 索引（幂等，启动时调用）
  upsert_entries           写入/覆盖条目点位
  search_by_vector         语义检索（filter: app_id + status=active）
  scroll_by_keyword        关键词检索（content 全文匹配）
  mark_status              批量改状态（软删除同步）
  touch_access             批量更新访问时间
  delete_points            物理删除（归档同步）
  count_active             对账用计数

向量维度取 config.embedding.dimensions（唯一来源）。qdrant-client 是同步 SDK，
统一 asyncio.to_thread 包裹，避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio

from qdrant_client import models

from db.qdrant.client import get_qdrant_client_manager
from shared import log
from codegenx.ai_service.utils.config import config
from codegenx.ai_service.memory.models import MemoryEntry, ulid_to_uuid

WARM_COLLECTION = "warm_memories"


def _to_point_id(entry_id: str) -> str:
    """json 侧 ULID 主键 → Qdrant 点位 ID（UUID 格式）。"""
    return ulid_to_uuid(entry_id)


def _payload_indexes() -> list[tuple[str, models.PayloadSchemaType | models.TextIndexParams]]:
    return [
        ("app_id", models.PayloadSchemaType.KEYWORD),
        ("user_id", models.PayloadSchemaType.KEYWORD),
        ("status", models.PayloadSchemaType.KEYWORD),
        ("memory_type", models.PayloadSchemaType.KEYWORD),
        ("topic", models.PayloadSchemaType.KEYWORD),
        ("created_at", models.PayloadSchemaType.DATETIME),
        ("last_accessed_at", models.PayloadSchemaType.DATETIME),
        (
            "content",
            models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.MULTILINGUAL,
                min_token_size=1,
            ),
        ),
    ]


def entry_to_payload(entry: MemoryEntry, app_id: str) -> dict:
    payload = entry.to_dict()
    payload["app_id"] = app_id
    return payload


def _active_filter(app_id: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key="app_id", match=models.MatchValue(value=app_id)),
            models.FieldCondition(key="status", match=models.MatchValue(value="active")),
        ]
    )


async def ensure_warm_collection() -> None:
    """启动时确保 collection 与索引存在（幂等）。"""
    manager = get_qdrant_client_manager()
    await asyncio.to_thread(
        manager.ensure_collection,
        WARM_COLLECTION,
        config.embedding.dimensions,
        _payload_indexes(),
    )


async def upsert_entries(entries: list[MemoryEntry], app_id: str, vectors: list[list[float]]) -> None:
    """条目与向量一一对应写入。"""
    if not entries:
        return
    points = [
        models.PointStruct(
            id=_to_point_id(entry.id),
            vector=vector,
            payload=entry_to_payload(entry, app_id),
        )
        for entry, vector in zip(entries, vectors)
    ]
    client = get_qdrant_client_manager().client
    await asyncio.to_thread(
        client.upsert,
        collection_name=WARM_COLLECTION,
        points=points,
        wait=True,
    )


async def search_by_vector(
    app_id: str,
    query_vector: list[float],
    limit: int = 10,
    score_threshold: float | None = None,
) -> list[tuple[MemoryEntry, float]]:
    """语义检索，返回 (entry, score) 列表，按分数降序。"""
    client = get_qdrant_client_manager().client
    try:
        hits = await asyncio.to_thread(
            client.query_points,
            collection_name=WARM_COLLECTION,
            query=query_vector,
            query_filter=_active_filter(app_id),
            limit=limit,
            score_threshold=score_threshold or 0.0,
            with_payload=True,
        )
    except Exception as exc:  # noqa: BLE001 — 检索降级为空结果而非中断对话
        log.error("warm 向量检索失败:{}", exc)
        return []
    result = []
    for point in hits.points:
        entry = MemoryEntry.from_dict(dict(point.payload or {}))
        if entry is not None:
            result.append((entry, point.score))
    return result


async def scroll_by_keyword(
    app_id: str,
    keyword: str,
    limit: int = 10,
) -> list[MemoryEntry]:
    """关键词检索（content 全文 MatchText），作为混合召回的精确匹配通道。"""
    if not keyword.strip():
        return []
    client = get_qdrant_client_manager().client
    flt = models.Filter(
        must=[
            models.FieldCondition(key="app_id", match=models.MatchValue(value=app_id)),
            models.FieldCondition(key="status", match=models.MatchValue(value="active")),
            models.FieldCondition(key="content", match=models.MatchText(text=keyword)),
        ]
    )
    try:
        points, _ = await asyncio.to_thread(
            client.scroll,
            collection_name=WARM_COLLECTION,
            scroll_filter=flt,
            limit=limit,
            with_payload=True,
        )
    except Exception as exc:  # noqa: BLE001 — 同上，降级为空结果
        log.error("warm 关键词检索失败:{}", exc)
        return []
    entries = []
    for point in points:
        entry = MemoryEntry.from_dict(dict(point.payload or {}))
        if entry is not None:
            entries.append(entry)
    return entries


async def mark_status(ids: list[str], status: str) -> None:
    """批量更新点位状态（软删除同步）。入参为 ULID 主键。"""
    if not ids:
        return
    client = get_qdrant_client_manager().client
    await asyncio.to_thread(
        client.set_payload,
        collection_name=WARM_COLLECTION,
        payload={"status": status},
        points=[_to_point_id(i) for i in ids],
        wait=True,
    )


async def touch_access(ids: list[str], accessed_at: str) -> None:
    """批量更新访问时间（30 天未访问软删除的判断依据）。入参为 ULID 主键。"""
    if not ids:
        return
    client = get_qdrant_client_manager().client
    await asyncio.to_thread(
        client.set_payload,
        collection_name=WARM_COLLECTION,
        payload={"last_accessed_at": accessed_at},
        points=[_to_point_id(i) for i in ids],
        wait=True,
    )


async def delete_points(ids: list[str]) -> None:
    """物理删除点位（归档同步）。入参为 ULID 主键。"""
    if not ids:
        return
    client = get_qdrant_client_manager().client
    selector = models.PointIdsList(points=[_to_point_id(i) for i in ids])
    await asyncio.to_thread(
        client.delete,
        collection_name=WARM_COLLECTION,
        points_selector=selector,
        wait=True,
    )


async def count_active(app_id: str) -> int:
    """对账用：当前 app 的 active 点位数。"""
    client = get_qdrant_client_manager().client
    try:
        info = await asyncio.to_thread(
            client.count,
            collection_name=WARM_COLLECTION,
            count_filter=_active_filter(app_id),
            exact=True,
        )
        return info.count
    except Exception as exc:
        log.error("warm 点位计数失败:{}", exc)
        return -1
