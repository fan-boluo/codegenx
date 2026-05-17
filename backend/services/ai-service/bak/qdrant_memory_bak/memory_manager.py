from __future__ import annotations

from threading import Lock

from bot.llm.llm import EmbeddingClient
from bot.memory.memory_writer import MemoryWriter
from bot.memory.retriver import MemoryRetriever
from infra.qdrant.client import get_qdrant_memory_client
from memory.schema import MemorySearchResult

_MEMORY_MANAGER_SINGLETON: "MemoryManager | None" = None
_MEMORY_MANAGER_LOCK = Lock()


class MemoryManager:
    """记忆管理门面，对外暴露统一入口。"""

    def __init__(self, qdrant_client=None, embedder=None):
        self.qdrant = qdrant_client or get_qdrant_memory_client()
        self.embedder = embedder or EmbeddingClient()
        self.writer = MemoryWriter(self.qdrant, self.embedder)
        self.retriever = MemoryRetriever(self.qdrant, self.embedder)

    def warm_up(self) -> "MemoryManager":
        if hasattr(self.qdrant, "ensure_memory_collections"):
            self.qdrant.ensure_memory_collections()
        return self

    async def retrieve_user_query(self, user_id: str, project: str,query:str) -> list[MemorySearchResult]:
        return await self.retriever.retrieve(
            user_id=user_id,
            app_id=project,
            query=query
        )

    async def auto_remember(
        self,
        user_id: str,
        project: str,
        content: str,
        memory_type: str,
    ) -> str | None:
        return await self.writer.add_short_term_memory(
            user_id=user_id,
            app_id=project,
            content=content,
            memory_type=memory_type,
        )


def get_memory_manager() -> MemoryManager:
    global _MEMORY_MANAGER_SINGLETON
    if _MEMORY_MANAGER_SINGLETON is not None:
        return _MEMORY_MANAGER_SINGLETON

    with _MEMORY_MANAGER_LOCK:
        if _MEMORY_MANAGER_SINGLETON is None:
            _MEMORY_MANAGER_SINGLETON = MemoryManager().warm_up()
    return _MEMORY_MANAGER_SINGLETON