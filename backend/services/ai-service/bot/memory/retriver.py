from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
import math

from qdrant_client import models

from bot.llm.llm import EmbeddingClient
from bot.memory.hybrid import apply_mmr, merge_hybrid_results, normalize_scores
from bot.memory.schema import MemorySearchResult, MemoryType
from bot.utils.config import load_config
from infra.qdrant.client import get_qdrant_memory_client
from infra.qdrant.client import LONG_TERM_COLLECTION, SHORT_TERM_COLLECTION


class MemoryRetriever:
    """记忆检索模块"""
    def __init__(self, qdrant_client=None, embedder=None):
        memory_config = load_config().memory
        memory_search_config = memory_config.search
        memory_store_config = memory_config.store
        self.qdrant = qdrant_client or get_qdrant_memory_client()
        self.client = getattr(self.qdrant, "client", self.qdrant)
        self.embedder = embedder or EmbeddingClient()
        self.search_top_k = max(1, int(memory_search_config.search_top_k))
        self.search_score_threshold = float(memory_search_config.search_score_threshold)
        self.lookback_days = max(1, int(memory_search_config.short_lookback_days))
        self.hybrid_vector_weight = float(memory_search_config.hybrid_vector_weight)
        self.long_term_weight = float(memory_search_config.long_term_weight)
        self.merge_result_similarity = float(memory_search_config.merge_result_similarity)


    async def retrieve(
        self,
        user_id: str,
        app_id: str,
        query: str,
        is_keyword: bool = False,
        is_vector: bool = False,
        is_hybrid: bool = True,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[MemorySearchResult]:
        """
        SessionStart / 上下文组装时调用。
        内部：长期库 + 短期库联合召回 → 去重 → 排序 → 截断
        """
        resolved_top_k = self._resolve_limit(top_k)
        resolved_threshold = self._resolve_score_threshold(score_threshold)
        long_results = await self.search_long_term(
            user_id=user_id,
            app_id=app_id,
            query=query,
            is_keyword=is_keyword,
            is_vector=is_vector,
            is_hybrid=is_hybrid,
            top_k=resolved_top_k,
            score_threshold=resolved_threshold,
        )
        short_results = await self.search_short_term(
            user_id=user_id,
            app_id=app_id,
            query=query,
            lookback_days=self.lookback_days,
            is_keyword=is_keyword,
            is_vector=is_vector,
            is_hybrid=is_hybrid,
            top_k=resolved_top_k // 2,  # 短期只取一半
            score_threshold=resolved_threshold,
        )
        if long_results and short_results:
            merged = await self.deduplicate(long_results, short_results)
        else:
            merged = [*long_results, *short_results]
        ranked = await self.rerank_by_weight(merged)
        final_results = ranked[:resolved_top_k]
        await self._update_access_count(final_results)
        return final_results

    async def search_long_term(
        self,
        user_id: str,
        app_id: str,
        query: str,
        is_keyword: bool = False,
        is_vector: bool = False,
        is_hybrid: bool = True,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[MemorySearchResult]:
        """单独检索长期记忆库"""
        return await self._search_by_mode(
            user_id=user_id,
            app_id=app_id,
            query=query,
            memory_source=MemoryType.LONG,
            is_keyword=is_keyword,
            is_vector=is_vector,
            is_hybrid=is_hybrid,
            top_k=top_k,
            score_threshold=score_threshold,
        )

    async def search_short_term(
        self,
        user_id: str,
        app_id: str,
        query: str,
        lookback_days: int | None = None,
        is_keyword: bool = False,
        is_vector: bool = False,
        is_hybrid: bool = True,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[MemorySearchResult]:
        """单独检索短期记忆库（仅近N天且未过期）"""
        return await self._search_by_mode(
            user_id=user_id,
            app_id=app_id,
            query=query,
            memory_source=MemoryType.SHORT,
            is_keyword=is_keyword,
            is_vector=is_vector,
            is_hybrid=is_hybrid,
            top_k=top_k,
            score_threshold=score_threshold,
            lookback_days=lookback_days,
        )

    async def deduplicate(
        self,
        long_results: list[MemorySearchResult],
        short_results: list[MemorySearchResult]
    ) -> list[MemorySearchResult]:
        """长期优先去重，短期与长期语义重复则丢弃"""
        merged: list[MemorySearchResult] = []
        merged.extend(long_results)
        # 对每条短期记忆，查它与长期命中结果的相似度
        for short in short_results:
            hit = False
            for long in long_results:
                # 短期记忆向量 vs 长期记忆向量，直接算余弦距离
                similarity = self._cosine_similarity(short.vector, long.vector)
                if similarity > self.merge_result_similarity:
                    hit = True
                    break
            if not hit:
                merged.append(short)
        return merged

    async def rerank_by_weight(
        self,
        results: list[MemorySearchResult]
    ) -> list[MemorySearchResult]:
        """按重要性加权重排序，长期记忆权重 > 短期，并且短期按照时间衰减"""
        long_weight = self.long_term_weight
        short_weight = 1 - self.long_term_weight

        # 当前时间戳（毫秒）
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        half_life_days = 3.0  # 半衰期天数
        half_life_ms = half_life_days * 24 * 3600 * 1000

        def apply_decay(score: float, created_at: int | None) -> float:
            if created_at is None:
                return score  # 无时间戳则不衰减
            age_ms = max(0, now_ms - created_at)
            decay = math.exp(-math.log(2) * age_ms / half_life_ms)
            return score * decay

        reranked = list(results or [])
        for item in reranked:
            if item.type == MemoryType.SHORT:
                item.score = apply_decay(item.score, getattr(item, "created_at", None))

        reranked.sort(
            key=lambda item: (
                item.score * (long_weight if item.type == MemoryType.LONG else short_weight),
                1 if item.type == MemoryType.LONG else 0,
            ),
            reverse=True,
        )
        return reranked

    async def inject_into_context(
        self,
        memories: list[dict],
        system_prompt: str
    ) -> str:
        """将检索到的记忆格式化注入 system prompt"""
        ...

    async def hybrid_search(
        self,
        user_id: str,
        app_id: str,
        query: str,
        memory_source: MemoryType | None,
        top_k: int | None = None,
        score_threshold: float | None = None,
        lookback_days: int | None = None,
    )->list[MemorySearchResult]:
        resolved_top_k = self._resolve_limit(top_k)
        resolved_threshold = self._resolve_score_threshold(score_threshold)
        vector_results = await self.vector_search(
            user_id=user_id,
            app_id=app_id,
            query=query,
            memory_source=memory_source,
            limit=resolved_top_k * 2,
            score_threshold=resolved_threshold,
            lookback_days=lookback_days,
        )
        keyword_results = await self.keyword_search(
            user_id=user_id,
            app_id=app_id,
            keyword=query,
            memory_source=memory_source,
            limit=resolved_top_k,
            score_threshold=resolved_threshold,
            lookback_days=lookback_days,
        )
        normalized_vector = normalize_scores(list(vector_results))
        normalized_keyword = normalize_scores(list(keyword_results))
        merged = merge_hybrid_results(
            normalized_vector,
            normalized_keyword,
            vector_weight=self.hybrid_vector_weight,
            text_weight=1-self.hybrid_vector_weight,
            min_score=resolved_threshold,
        )

        return apply_mmr(merged, limit=resolved_top_k)

    async def keyword_search(
            self,
            user_id:str,
            app_id:str,
            keyword:str,
            memory_source:MemoryType | None,
            limit: int | None = None,
            score_threshold: float | None = None,
            lookback_days: int | None = None,
    )->list[MemorySearchResult]:
        """
        Args:
            user_id:
            app_id:
            keyword:
            memory_source: 没有就长短期一起搜
            limit:
            score_threshold:
            lookback_days:

        Returns:

        """
        normalized_keyword = str(keyword or "").strip().lower()
        if not normalized_keyword:
            return []

        resolved_limit = self._resolve_limit(limit)
        resolved_threshold = self._resolve_score_threshold(score_threshold)
        resolved_lookback_days = self._resolve_lookback_days(lookback_days)
        scan_limit = max(50, resolved_limit * 20)

        results: list[MemorySearchResult] = []
        for collection_name in self._resolve_collection_names(memory_source):
            points = self._scroll_points(
                collection_name=collection_name,
                user_id=user_id,
                app_id=app_id,
                limit=scan_limit,
                lookback_days=resolved_lookback_days if collection_name == SHORT_TERM_COLLECTION else None,
                extra_must_conditions=[
                    models.FieldCondition(
                        key="content",
                        match=models.MatchText(text=normalized_keyword),
                    )
                ],
            )
            memory_type = self._collection_to_memory_type(collection_name)
            for point in points:
                result = self._keyword_point_to_result(
                    point,
                    memory_type,
                    normalized_keyword,
                    resolved_threshold,
                )
                if result:
                    results.append(result)

        return self._sort_results(results)[:resolved_limit]


    async def vector_search(self,user_id:str,app_id:str,query:str,
                            memory_source:MemoryType | None,
                            limit: int | None = None,
                            score_threshold: float | None = None,
                            lookback_days: int | None = None,
                            ) \
            ->list[MemorySearchResult]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        resolved_limit = self._resolve_limit(limit)
        resolved_threshold = self._resolve_score_threshold(score_threshold)
        resolved_lookback_days = self._resolve_lookback_days(lookback_days)
        query_embedding = await self.embedder.embed(normalized_query)

        merged=[]
        for collection_name in self._resolve_collection_names(memory_source):
            points = self._query_points(
                collection_name=collection_name,
                user_id=user_id,
                app_id=app_id,
                query=query_embedding,
                limit=resolved_limit,
                score_threshold=resolved_threshold,
                lookback_days=resolved_lookback_days if collection_name == SHORT_TERM_COLLECTION else None,
            )
            memory_type = self._collection_to_memory_type(collection_name)
            for point in points:
                result = self._point_to_result(point, memory_type, resolved_threshold)
                if result:
                    merged.append(result)
                    # self._merge_result(merged, result)

        return self._sort_results(merged)[:resolved_limit]

    async def _search_by_mode(
        self,
        user_id: str,
        app_id: str,
        query: str,
        memory_source: MemoryType,
        is_keyword: bool,
        is_vector: bool,
        is_hybrid: bool,
        top_k: int | None,
        score_threshold: float | None,
        lookback_days: int | None = None,
    ) -> list[MemorySearchResult]:
        search_mode = self._resolve_search_mode(is_keyword, is_vector, is_hybrid)
        if search_mode == "keyword":
            return await self.keyword_search(
                user_id=user_id,
                app_id=app_id,
                keyword=query,
                memory_source=memory_source,
                limit=top_k,
                score_threshold=score_threshold,
                lookback_days=lookback_days,
            )
        if search_mode == "hybrid":
            return await self.hybrid_search(
                user_id=user_id,
                app_id=app_id,
                query=query,
                memory_source=memory_source,
                top_k=top_k,
                score_threshold=score_threshold,
                lookback_days=lookback_days,
            )
        return await self.vector_search(
            user_id=user_id,
            app_id=app_id,
            query=query,
            memory_source=memory_source,
            limit=top_k,
            score_threshold=score_threshold,
            lookback_days=lookback_days,
        )

    def _resolve_limit(self, limit: int | None) -> int:
        if limit is None:
            return self.search_top_k
        return max(1, int(limit))

    def _resolve_score_threshold(self, score_threshold: float | None) -> float:
        if score_threshold is None:
            return self.search_score_threshold
        return max(0.0, min(1.0, float(score_threshold)))

    def _resolve_lookback_days(self, lookback_days: int | None) -> int:
        if lookback_days is None:
            return self.lookback_days
        return max(1, int(lookback_days))

    def _resolve_search_mode(self, is_keyword: bool, is_vector: bool, is_hybrid: bool) -> str:
        if is_hybrid or (is_keyword and is_vector):
            return "hybrid"
        if is_keyword:
            return "keyword"
        return "vector"

    def _now_ts(self) -> int:
        return int(datetime.now(UTC).timestamp())

    def _cutoff_date(self, lookback_days: int) -> datetime.date:
        return (datetime.now(UTC) - timedelta(days=max(1, int(lookback_days)))).date()

    def _memory_filter(self, user_id: str, app_id: str, collection_name: str) -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
            models.FieldCondition(key="app_id", match=models.MatchValue(value=app_id)),
        ]
        if collection_name == SHORT_TERM_COLLECTION:
            must.append(models.FieldCondition(key="expires_at", range=models.Range(gte=self._now_ts())))
        return models.Filter(must=must)

    def _query_points(
        self,
        collection_name: str,
        user_id: str,
        app_id: str,
        query: list[float],
        limit: int,
        score_threshold: float,
        lookback_days: int | None = None,
    ) -> list[Any]:
        response = self.client.query_points(
            collection_name=collection_name,
            query=query,
            query_filter=self._memory_filter(user_id=user_id, app_id=app_id, collection_name=collection_name),
            limit=max(1, int(limit)),
            score_threshold=float(score_threshold),
            with_payload=True,
            with_vectors=True,
        )
        points = list(response.points if hasattr(response, "points") else [])
        return self._filter_short_term_points(points, lookback_days) if collection_name == SHORT_TERM_COLLECTION else points

    def _scroll_points(
        self,
        collection_name: str,
        user_id: str,
        app_id: str,
        limit: int,
        lookback_days: int | None = None,
        extra_must_conditions: list[models.Condition] | None = None,
    ) -> list[Any]:
        memory_filter = self._memory_filter(user_id=user_id, app_id=app_id, collection_name=collection_name)
        if extra_must_conditions:
            memory_filter.must.extend(extra_must_conditions)
        points, _ = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=memory_filter,
            limit=max(1, int(limit)),
            with_payload=True,
            with_vectors=True,
        )
        results = list(points or [])
        return self._filter_short_term_points(results, lookback_days) if collection_name == SHORT_TERM_COLLECTION else results

    def _filter_short_term_points(self, points: list[Any], lookback_days: int | None) -> list[Any]:
        resolved_lookback_days = self._resolve_lookback_days(lookback_days)
        cutoff_date = self._cutoff_date(resolved_lookback_days)
        filtered: list[Any] = []
        for point in points or []:
            payload = point.payload or {}
            source_date = payload.get("source_date")
            if not source_date:
                filtered.append(point)
                continue
            try:
                point_date = datetime.fromisoformat(str(source_date)).date()
            except ValueError:
                filtered.append(point)
                continue
            if point_date >= cutoff_date:
                filtered.append(point)
        return filtered

    def _resolve_collection_names(self, memory_source: MemoryType | None) -> list[str]:
        if memory_source == MemoryType.LONG:
            return [LONG_TERM_COLLECTION]
        if memory_source == MemoryType.SHORT:
            return [SHORT_TERM_COLLECTION]
        return [LONG_TERM_COLLECTION, SHORT_TERM_COLLECTION]

    def _collection_to_memory_type(self, collection_name: str) -> MemoryType:
        return MemoryType.LONG if collection_name == LONG_TERM_COLLECTION else MemoryType.SHORT

    def _memory_type_to_collection_name(self, memory_type: MemoryType) -> str:
        return LONG_TERM_COLLECTION if memory_type == MemoryType.LONG else SHORT_TERM_COLLECTION

    async def _update_access_count(self, results: list[MemorySearchResult]) -> None:
        for result in results or []:
            point_id = str(result.id or "").strip()
            if not point_id:
                continue
            collection_name = self._memory_type_to_collection_name(result.type)
            current_access_count = int(result.access_count or 0)
            self.client.set_payload(
                collection_name=collection_name,
                payload={"access_count": current_access_count + 1},
                points=[point_id],
                wait=True,
            )
            result.access_count = current_access_count + 1

    def _keyword_point_to_result(
        self,
        point: Any,
        memory_type: MemoryType,
        normalized_keyword: str,
        score_threshold: float,
    ) -> MemorySearchResult | None:
        payload = point.payload or {}
        text = str(payload.get("content", "") or "").strip()
        if not text:
            return None

        normalized_text = text.lower()
        hit_count = normalized_text.count(normalized_keyword)
        if hit_count <= 0:
            return None

        importance = float(payload.get("importance", 0.0) or 0.0)
        normalized_hits = min(1.0, hit_count / 3)
        score = min(1.0, 0.8 * normalized_hits + 0.2 * importance)
        if score < score_threshold:
            return None
        return MemorySearchResult(
            id=str(getattr(point, "id", "") or ""),
            text=text,
            snippet=self._build_snippet(text),
            score=float(score),
            type=memory_type,
            access_count=int(payload.get("access_count", 0) or 0),
            importance=float(payload.get("importance", 0.0) or 0.0),
            version=int(payload.get("version", 0) or 0) if payload.get("version") is not None else None,
            category=str(payload.get("memory_type", "") or "") or None,
            vector=self._extract_vector(point),
        )

    def _point_to_result(
        self,
        point: Any,
        memory_type: MemoryType,
        score_threshold: float,
    ) -> MemorySearchResult | None:
        payload = point.payload or {}
        text = str(payload.get("content", "") or "")
        score = float(getattr(point, "score", 0.0) or 0.0)
        if score < score_threshold:
            return None
        return MemorySearchResult(
            id=str(getattr(point, "id", "") or ""),
            text=text,
            snippet=self._build_snippet(text),
            score=score,
            type=memory_type,
            access_count=int(payload.get("access_count", 0) or 0),
            importance=float(payload.get("importance", 0.0) or 0.0),
            version=int(payload.get("version", 0) or 0) if payload.get("version") is not None else None,
            category=str(payload.get("memory_type", "") or "") or None,
            vector=self._extract_vector(point),
        )

    def _extract_vector(self, point: Any) -> list[float] | None:
        vector = getattr(point, "vector", None)
        if isinstance(vector, list):
            return [float(value) for value in vector]
        if isinstance(vector, dict):
            for value in vector.values():
                if isinstance(value, list):
                    return [float(item) for item in value]
        return None

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot_product = sum(x * y for x, y in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot_product / (left_norm * right_norm)

    def _merge_result(self, merged: dict[str, MemorySearchResult], result: MemorySearchResult) -> None:
        merge_key = str(result.id or "") or str(result.text or "")
        existing = merged.get(merge_key)
        if existing is None:
            merged[merge_key] = result
            return
        if result.score > existing.score:
            merged[merge_key] = result
            return
        if result.score == existing.score and result.type == MemoryType.LONG and existing.type != MemoryType.LONG:
            merged[merge_key] = result

    def _sort_results(self, results: list[MemorySearchResult]) -> list[MemorySearchResult]:
        ranked = list(results or [])
        ranked.sort(
            key=lambda item: (
                item.score,
                1 if item.type == MemoryType.LONG else 0,
            ),
            reverse=True,
        )
        return ranked

    def _build_snippet(self, text: str, length: int = 200) -> str:
        normalized_text = str(text or "")
        if len(normalized_text) <= length:
            return normalized_text
        return normalized_text[:length] + "..."


@lru_cache(maxsize=1)
def get_memory_retriever() -> MemoryRetriever:
    return MemoryRetriever()