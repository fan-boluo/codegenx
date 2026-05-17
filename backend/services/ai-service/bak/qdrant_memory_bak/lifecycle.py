class MemoryLifecycle:
    """记忆生命周期管理"""

    async def expire_short_term(self) -> int:
        """清理所有 expires_at < now 的短期记忆，返回删除数量"""
        ...

    async def update_long_term(
        point_id: str,
        new_content: str,
        merge_source_ids: list[str] = None,
    ) -> str:                      # 返回 point_id
        """更新长期记忆内容、合并来源、递增 version"""
        points, _ = self.client.scroll(
            collection_name=SHORT_TERM_COLLECTION,
            scroll_filter=self._memory_filter(user_id=user_id, app_id=app_id, collection_name=SHORT_TERM_COLLECTION),
            limit=50,
            with_payload=True,
            with_vectors=False,
        )
        ranked = sorted(
            points,
            key=lambda point: (
                float((point.payload or {}).get("importance", 0.0) or 0.0),
                int((point.payload or {}).get("created_at", 0) or 0),
            ),
            reverse=True,
        )
        return [
            point
            for point in ranked
            if float((point.payload or {}).get("importance", 0.0) or 0.0) >= 0.6
               or str((point.payload or {}).get("memory_type", "")) in {"preference", "decision"}
        ]

    async def increment_access_count(
        point_ids: list[str],
        collection: str            # "short_term_memories" | "long_term_memories"
    ) -> None:
        """检索命中后批量更新 access_count"""
        ...

    async def decay_importance(
        user_id: str,
        project: str,
        min_access_count: int = 2,
        days_stale: int = 30,
        decay_rate: float = 0.1
    ) -> list[str]:               # 返回被降级的 point_id 列表
        """长期未访问的长期记忆逐步降低 importance"""
        ...