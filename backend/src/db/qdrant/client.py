"""Qdrant 连接管理 —— 纯基础设施层。

只负责：连接单例、通用 collection 创建与 payload 索引、预热/关闭生命周期。
不包含任何业务 collection 定义（业务 schema 由 memory/vector_store.py 维护）。
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, Sequence

from qdrant_client import QdrantClient, models

from shared.config.config import get_settings


class QdrantClientManager:
    """Qdrant 连接单例 + 通用建库能力。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = QdrantClient(
            host=self.settings.qdrant_url,
            port=self.settings.qdrant_port,
            api_key=self.settings.qdrant_api_key or None,
            timeout=30,
        )

    def ensure_collection(
        self,
        name: str,
        vector_size: int,
        payload_indexes: Sequence[tuple[str, Any]] = (),
        distance: models.Distance = models.Distance.COSINE,
        recreate_text_index: bool = False,
    ) -> bool:
        """确保 collection 存在（不存在则按给定向量维度创建），并补齐 payload 索引。

        参数:
            vector_size: 向量维度，必须与 embedding 输出维度一致（调用方负责）。
            payload_indexes: [(字段名, 索引 schema), ...]
            recreate_text_index: 为 True 时先删除已存在的 text 索引再重建
                                 （text 索引参数变更时 qdrant 不支持原地更新）。
        """
        if not self.client.collection_exists(name):
            self.client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=distance,
                ),
            )

        for field_name, field_schema in payload_indexes:
            try:
                if recreate_text_index and field_name == "content":
                    try:
                        self.client.delete_payload_index(
                            collection_name=name,
                            field_name=field_name,
                            wait=True,
                        )
                    except Exception:
                        pass
                self.client.create_payload_index(
                    collection_name=name,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
            except Exception:
                # 索引已存在等非致命情况：跳过该字段，不影响整体可用性
                continue
        return True

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


@lru_cache(maxsize=1)
def get_qdrant_client_manager() -> QdrantClientManager:
    return QdrantClientManager()


async def warm_up_qdrant_client() -> QdrantClientManager:
    """启动预热：创建连接并做一次真实请求，失败快速暴露配置问题。"""
    manager = await asyncio.to_thread(get_qdrant_client_manager)
    await asyncio.to_thread(manager.client.get_collections)
    return manager


async def shutdown_qdrant_client() -> None:
    if get_qdrant_client_manager.cache_info().currsize == 0:
        return
    manager = get_qdrant_client_manager()
    await asyncio.to_thread(manager.close)
    get_qdrant_client_manager.cache_clear()
