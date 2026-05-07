from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, List
from uuid import uuid4

from qdrant_client import models

from bot.llm.llm import EmbeddingClient
from bot.llm.async_client import AsyncLLMClient
from bot.utils.config import load_config
from bot.utils.log_utils import log
from infra.qdrant.client import (
    CONSOLIDATION_LOG_COLLECTION,
    LONG_TERM_COLLECTION,
    SHORT_TERM_COLLECTION,
    get_qdrant_memory_client,
)


SHORT_TERM_TYPES = {"preference", "decision", "fact", "todo"}
LONG_TERM_TYPES = {"preference", "decision", "principle", "fact"}
CONFLICT_STRATEGIES = {"update", "keep_both", "skip"}


class MemoryWriter:
    """记忆写入模块"""

    def __init__(self, qdrant_client=None, embedder=None, judge_llm=None):
        memory_store_config = load_config().memory.store
        self.qdrant = qdrant_client or get_qdrant_memory_client()
        self.client = getattr(self.qdrant, "client", self.qdrant)
        self.embedder = embedder or EmbeddingClient()
        self.judge_llm = judge_llm or AsyncLLMClient()
        self.default_ttl_days = max(1, int(memory_store_config.ttlDays))
        self.short_duplicate_score_threshold = float(memory_store_config.shortDuplicatedScoreThreshold)
        self.long_matches_score_threshold = float(memory_store_config.longMatchesScoreThreshold)
        self.long_matches_top_k = max(1, int(memory_store_config.longMatchesTopK))
        self.long_direct_write_importance_threshold = float(memory_store_config.longDirectWriteImportanceThreshold)

        if hasattr(self.qdrant, "ensure_memory_collections"):
            self.qdrant.ensure_memory_collections()

    async def add_short_term_memory(
        self,
        user_id: str,
        app_id: str,
        content: str,
        memory_type: str,
        importance: float = 0.5,
        ttl_days: int | None = None,
    ) -> str:
        """LLM 自动追加短期记忆"""
        
        source_date = datetime.today().date()
        embedding = await self.embedder.embed(content)
        is_duplicate, duplicate_id = await self.check_duplicate(
            user_id=user_id,
            app_id=app_id,
            vector=embedding,
            threshold=self.short_duplicate_score_threshold,
        )
        if is_duplicate and duplicate_id:
            log.warning(f"记忆已存在，不必添加，已有记忆id:{duplicate_id}")
            return duplicate_id

        now = self._now_ts()
        resolved_ttl_days = self.default_ttl_days if ttl_days is None else max(1, int(ttl_days))
        expires_at = int((datetime.now(UTC) + timedelta(days=resolved_ttl_days)).timestamp())
        point_id = str(uuid4())
        payload = {
            "user_id": user_id,
            "app_id": app_id,
            "content": content,
            "memory_type": memory_type,
            "source_date": source_date,
            "importance": self._clamp_importance(importance),
            "access_count": 0,
            "expires_at": expires_at,
            "created_at": now,
        }

        self.client.upsert(
            collection_name=SHORT_TERM_COLLECTION,
            points=[models.PointStruct(id=point_id, vector=embedding, payload=payload)],
            wait=True,
        )
        return point_id

    async def consolidate_to_long_term(
        self,
        user_id: str,
        app_id: str,
        candidate_ids: list[str] = None,  # 源自短期记忆的id
    ) -> list[str]:
        """从短期记忆提炼为长期记忆（向量召回 + 批量 LLM 决策）"""
        log.debug(f"提取项目id：{candidate_ids}")
        normalized_user_id = self._normalize_required_value(user_id, "user_id")
        normalized_app_id = self._normalize_required_value(app_id, "app_id")
        # 搜索短期记忆
        candidates = self._load_short_term_candidates(normalized_user_id, normalized_app_id, candidate_ids)
        if not candidates:
            return []
        # 搜索短期记忆对应的
        prepared_candidates, matched_pool, direct_new_candidate_ids = await self._prepare_consolidation_candidates(
            normalized_user_id,
            normalized_app_id,
            candidates,
        )

        decisions: list[dict[str, Any]] = []
        if matched_pool:
            log.debug(f"匹配到与现有长期记忆高相似度的内容，由大模型判断:{matched_pool}")
            decisions.extend(await self._plan_matched_pool_actions_v2(matched_pool))
        decisions.extend(self._build_direct_new_decisions(prepared_candidates, direct_new_candidate_ids))

        target_ids: list[str] = []
        for decision in decisions:
            log.debug(f"decision:{decision}")
            candidate_id = str(decision.get("short_term_id", ""))
            action = str(decision.get("action", "ignore") or "ignore").strip().lower()
            prepared = prepared_candidates.get(candidate_id)
            if not prepared or action == "ignore":
                continue

            target_id = await self._apply_consolidation_decision(
                normalized_user_id,
                normalized_app_id,
                prepared,
                decision,
                action,
            )
            if target_id:
                target_ids.append(target_id)

        return list(dict.fromkeys(target_ids))

    async def _prepare_consolidation_candidates(
        self,
        user_id: str,
        app_id: str,
        candidates: list[Any],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
        """
        搜索每个candidates对应的长期向量，有则进入匹配池，否则new
        return:
          prepared_candidates 每个短期记忆对应的短期记忆信息，及match的长期记忆信息
          matched_pool：短期及匹配的长期，后面直接传大模型的
          direct_new_candidate_ids：可以直接新建长期的短期的id
        """
        prepared_candidates: dict[str, dict[str, Any]] = {}
        matched_pool: list[dict[str, Any]] = []
        direct_new_candidate_ids: list[str] = []

        for candidate in candidates:
            payload = candidate.payload or {}
            candidate_id = str(candidate.id)
            normalized_content = self._normalize_content(str(payload.get("content", "")))
            if not normalized_content:
                continue

            embedding = await self.embedder.embed(normalized_content)
            importance = self._clamp_importance(float(payload.get("importance", 0.5) or 0.5))
            matches = self._deduplicate_matches_by_id(
                self._search_long_term_matches(
                    user_id=user_id,
                    app_id=app_id,
                    vector=embedding,
                    limit=self.long_matches_top_k,
                    threshold=self.long_matches_score_threshold,
                )
            )

            prepared_candidates[candidate_id] = {
                "source_ids": [candidate_id], # 短期
                "content": normalized_content,
                "memory_type": self._map_to_long_term_type(str(payload.get("memory_type", "fact"))),
                "importance": importance,  # 短期
                "embedding": embedding,  # 短期
                "matches": matches,  # 匹配的长期的
                "match_payloads": {  # 匹配的长期的
                    str(match.id): dict(match.payload or {})
                    for match in matches
                },
            }

            if matches:
                matched_pool.append(
                    {
                        "short_term_id": candidate_id, # 短期
                        "content": normalized_content,  # 短期
                        "memory_type": prepared_candidates[candidate_id]["memory_type"], # 短期
                        "importance": importance,  # 短期
                        "matches": [self._serialize_long_term_match(match) for match in matches], # 长期
                    }
                )
            elif importance >= self.long_direct_write_importance_threshold:
                direct_new_candidate_ids.append(candidate_id)

        return prepared_candidates, matched_pool, direct_new_candidate_ids

    def _build_direct_new_decisions(
        self,
        prepared_candidates: dict[str, dict[str, Any]],
        candidate_ids: list[str],
    ) -> list[dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        for candidate_id in candidate_ids:
            log.debug(f"高相似度且未匹配到，直接新增:{candidate_id}")
            prepared = prepared_candidates.get(candidate_id)
            if not prepared:
                continue
            decisions.append(
                {
                    "short_term_id": candidate_id,
                    "action": "new",
                    "new_content": prepared["content"],
                    "reason": "高重要度直接提炼为长期记忆",
                }
            )
        return decisions

    async def _apply_consolidation_decision(
        self,
        user_id: str,
        app_id: str,
        prepared: dict[str, Any],
        decision: dict[str, Any],
        action: str,
    ) -> str | None:
        source_ids = prepared["source_ids"]
        normalized_content = str(decision.get("new_content") or prepared["content"])
        memory_type = prepared["memory_type"]
        importance = prepared["importance"]  # 短期的
        matches = prepared["matches"]
        # 原来的长期记忆
        match_payloads = prepared.get("match_payloads", {})

        if action == "duplicate":
            self._write_log(
                action="duplicate",
                source_ids=source_ids,
                target_id=str(decision.get("target_long_id") or (str(matches[0].id) if matches else "")),
                result_content=normalized_content,
            )
            return None

        if action == "new":
            embedding = await self._resolve_content_embedding(normalized_content, prepared)
            return self._create_long_term_memory(
                user_id,
                app_id,
                normalized_content,
                memory_type,
                source_ids,
                importance,
                embedding,
            )

        if action != "update":
            return None

        target_id = self._resolve_update_target_id(decision, matches, match_payloads)
        if not target_id:
            # 大模型没有给出要更新的id-target_id，则fallback到新增
            embedding = await self._resolve_content_embedding(normalized_content, prepared)
            return self._create_long_term_memory(
                user_id,
                app_id,
                normalized_content,
                memory_type,
                source_ids,
                importance,
                embedding,
            )

        payload = self._build_long_term_payload(
            user_id=user_id,
            app_id=app_id,
            content=normalized_content,
            memory_type=memory_type,
            source_ids=source_ids,
            importance=importance,
            existing_payload=match_payloads.get(target_id, {}),
        )
        embedding = await self._resolve_content_embedding(normalized_content, prepared)
        self._upsert_long_term_memory(target_id, embedding, payload)
        self._write_log(
            action="update",
            source_ids=source_ids,
            target_id=target_id,
            result_content=normalized_content,
        )
        return target_id

    async def _resolve_content_embedding(
        self,
        content: str,
        prepared: dict[str, Any],
    ) -> List[float]:
        if content == prepared.get("content"):
            return prepared["embedding"]
        return await self.embedder.embed(content)

    def _resolve_update_target_id(
        self,
        decision: dict[str, Any],
        matches: list[Any],
        match_payloads: dict[str, dict[str, Any]],
    ) -> str:
        target_id = str(decision.get("target_long_id") or "")
        valid_match_ids = set(match_payloads.keys())
        if target_id and target_id not in valid_match_ids:
            target_id = ""
        if not target_id and matches:
            target_id = str(matches[0].id)
        return target_id

    def _create_long_term_memory(
        self,
        user_id: str,
        app_id: str,
        content: str,
        memory_type: str,
        source_ids: list[str],
        importance: float,
        embedding: List[float],
    ) -> str:
        target_id = str(uuid4())
        payload = self._build_long_term_payload(
            user_id=user_id,
            app_id=app_id,
            content=content,
            memory_type=memory_type,
            source_ids=source_ids,
            importance=importance,
        )
        self._upsert_long_term_memory(target_id, embedding, payload)
        self._write_log(
            action="new",
            source_ids=source_ids,
            target_id=target_id,
            result_content=content,
        )
        return target_id

    def _build_long_term_payload(
        self,
        user_id: str,
        app_id: str,
        content: str,
        memory_type: str,
        source_ids: list[str],
        importance: float,
        existing_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """

        Args:
            user_id:
            app_id:
            content:
            memory_type:
            source_ids: 短期的
            importance: 短期的
            existing_payload: 原来长期的

        Returns:

        """
        now = self._now_ts()
        payload = dict(existing_payload or {})
        existing_sources = [str(item) for item in payload.get("source_short_term_ids", [])]
        merged_sources = list(dict.fromkeys(existing_sources + [str(item) for item in source_ids]))
        has_existing = bool(payload)

        return {
            "user_id": user_id,
            "app_id": app_id,
            "content": content,
            "memory_type": memory_type,
            "source_short_term_ids": merged_sources,
            "importance": (
                max(float(payload.get("importance", 0.0) or 0.0), float(importance))
                if has_existing
                else max(self.long_direct_write_importance_threshold, importance)
            ),
            "access_count": int(payload.get("access_count", 0) or 0),
            "version": int(payload.get("version", 1) or 1) + (1 if has_existing else 0),
            "created_at": int(payload.get("created_at", now) or now),
            "updated_at": now,
        }

    def _upsert_long_term_memory(
        self,
        point_id: str,
        embedding: List[float],
        payload: dict[str, Any],
    ) -> None:
        self.client.upsert(
            collection_name=LONG_TERM_COLLECTION,
            points=[models.PointStruct(id=point_id, vector=embedding, payload=payload)],
            wait=True,
        )

    def _serialize_long_term_match(self, match: Any) -> dict[str, Any]:
        return {
            "id": str(match.id),
            "score": float(getattr(match, "score", 0.0) or 0.0),
            "content": str((match.payload or {}).get("content", "")),
            "memory_type": str((match.payload or {}).get("memory_type", "")),
            "version": int((match.payload or {}).get("version", 1) or 1),
        }

    async def _plan_matched_pool_actions_v2(self, matched_pool: list[dict[str, Any]])\
            -> list[dict[str, Any]]:
        """
        LLM 决策模板：
        返回 JSON 数组：
        [
          {
            "short_term_id": "stm-xxx",
            "action": "new|update|duplicate|ignore",
            "target_long_id": "ltm-xxx",       // update 时必填
            "new_content": "提炼后的内容",       // new/update 时必填
            "reason": "简短说明"
          }
        ]
        """
        system_prompt = (
            "你是专业的【前端项目记忆合并决策器】。你的任务是基于短期记忆与长期记忆的向量匹配结果，"
            "对记忆进行智能合并、更新、去重或忽略，确保长期记忆始终保持**准确、精简、高价值、可用于前端代码生成**。\n"
            "你必须深度思考：项目架构、技术栈、组件规范、全局规则、UI 约束、功能需求、开发约定、页面结构等核心信息。\n\n"
            "输出要求：仅输出标准 JSON 数组，不要任何解释、文字、注释或多余内容。\n"
            "数组中每个对象必须包含字段：short_term_id, action, target_long_id, new_content, reason。\n"
            "action 仅限四种：new、update、duplicate、ignore。\n\n"
            "严格执行规则：\n"
            "1. duplicate：语义、内容、规则、架构描述完全重复，无任何新增信息 → 标记重复。\n"
            "2. update：语义相近、属于同一【架构/组件/规则/需求/页面/等】，可增量融合、补充细节、强化约束 → 必须指定 target_long_id 并生成融合后的 new_content。\n"
            "3. new：与现有记忆不冲突，但包含**新架构、新组件、新规则、新页面、新需求、新约束**等具备长期价值的内容 → 必须生成完整规范的 new_content。\n"
            "4. ignore：无长期价值、临时闲聊、错误信息、无关噪声、不影响项目架构与代码生成的内容 → 直接忽略。\n\n"
            "思考维度：项目架构、技术栈选型、全局样式规则、组件设计规范、页面结构、功能需求、交互约定、接口约束、开发流程、命名规范。"
        )
        user_prompt = json.dumps({"matched_pool": matched_pool}, ensure_ascii=False)

        try:
            content = await self.judge_llm.invoke(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=2048,
            )
            # 直接解析为 JSON 数组
            text = str(content or "").strip()
            if text.startswith("["):
                return json.loads(text)
            # 兼容 LLM 可能包裹 JSON 的情况
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                return json.loads(text[start:end+1])
        except Exception as exc:
            log.warning("LLM planning for long-term consolidation (v2) failed: {}", exc)
        # fallback: 全部 update
        fallback = []
        for item in matched_pool:
            matches = item.get("matches") or []
            target_id = str(matches[0].get("id", "")) if matches else ""
            fallback.append({
                "short_term_id": str(item.get("short_term_id", "") or item.get("candidate_id", "")),
                "action": "update" if target_id else "ignore",
                "target_long_id": target_id,
                "new_content": item.get("content", ""),
                "reason": "fallback"
            })
        return fallback

    async def check_duplicate(
        self,
        user_id: str,
        app_id: str,
        vector:List[float],
        threshold: float | None = None,
    ) -> tuple[bool, str | None]:
        """写入前去重检查，返回是否已有语义重复的记忆"""
        resolved_threshold = self.short_duplicate_score_threshold if threshold is None else float(threshold)
        response = self.client.query_points(
            collection_name=SHORT_TERM_COLLECTION,
            query=vector,
            query_filter=self._memory_filter(user_id=user_id, app_id=app_id, collection_name=SHORT_TERM_COLLECTION),
            limit=1,
            score_threshold=resolved_threshold,
            with_payload=True,
            with_vectors=False,
        )
        points = response.points if hasattr(response, "points") else []
        if not points:
            return False,None
        return True,str(points[0].id)

    def _normalize_required_value(self, value: str, field_name: str) -> str:
        normalized = str(value or "").strip()
        # if not normalized:
        #     raise ValueError(f"{field_name} is required")
        return normalized

    def _normalize_content(self, content: str) -> str:
        normalized = str(content or "").strip()
        # if not normalized:
        #     raise ValueError("content is required")
        return normalized


    def _map_to_long_term_type(self, memory_type: str) -> str:
        normalized = str(memory_type or "fact").strip().lower()
        if normalized in LONG_TERM_TYPES:
            return normalized
        if normalized == "todo":
            return "fact"
        return "fact"

    def _clamp_importance(self, importance: float) -> float:
        return max(0.0, min(1.0, float(importance)))

    def _validate_source_date(self, source_date: str) -> None:
        datetime.strptime(source_date, "%Y-%m-%d")

    def _now_ts(self) -> int:
        return int(datetime.now(UTC).timestamp())

    def _search_long_term_matches(
        self,
        user_id: str,
        app_id: str,
        vector: List[float],
        limit: int | None = None,
        threshold: float | None = None,
    ) -> list[Any]:
        resolved_limit = self.long_matches_top_k if limit is None else max(1, int(limit))
        resolved_threshold = self.long_matches_score_threshold if threshold is None else float(threshold)
        response = self.client.query_points(
            collection_name=LONG_TERM_COLLECTION,
            query=vector,
            query_filter=self._memory_filter(user_id=user_id, app_id=app_id, collection_name=LONG_TERM_COLLECTION),
            limit=resolved_limit,
            score_threshold=resolved_threshold,
            with_payload=True,
            with_vectors=False,
        )
        points = response.points if hasattr(response, "points") else []
        return list(points or [])

    def _deduplicate_matches_by_id(self, matches: list[Any]) -> list[Any]:
        unique: list[Any] = []
        seen_ids: set[str] = set()
        for match in matches or []:
            match_id = str(getattr(match, "id", "")).strip()
            if not match_id or match_id in seen_ids:
                continue
            seen_ids.add(match_id)
            unique.append(match)
        return unique


    def _memory_filter(self, user_id: str, app_id: str, collection_name: str) -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
            models.FieldCondition(key="app_id", match=models.MatchValue(value=app_id)),
        ]
        if collection_name == SHORT_TERM_COLLECTION:
            must.append(models.FieldCondition(key="expires_at", range=models.Range(gte=self._now_ts())))
        return models.Filter(must=must)

    def _load_short_term_candidates(self, user_id: str, app_id: str, candidate_ids: list[str] | None) -> list[Any]:
        if candidate_ids:
            points = self.client.retrieve(
                collection_name=SHORT_TERM_COLLECTION,
                ids=[str(item) for item in candidate_ids],
                with_payload=True,
                with_vectors=False,
            )
            return [
                point
                for point in points
                if (point.payload or {}).get("user_id") == user_id and (point.payload or {}).get("app_id") == app_id
            ]

    def _write_log(
        self,
        action: str,
        source_ids: list[str],
        target_id: str,
        result_content: str,
    ) -> None:
        performed_at = self._now_ts()
        point_id = str(uuid4())
        payload = {
            "action": str(action).strip().lower(),
            "source_ids": [str(item) for item in source_ids],
            "target_id": str(target_id),
            "result_content": str(result_content),
            "performed_at": performed_at,
        }
        vector_size = int(getattr(getattr(self.qdrant, "settings", None), "qdrant_size", 1024) or 1024)
        self.client.upsert(
            collection_name=CONSOLIDATION_LOG_COLLECTION,
            points=[models.PointStruct(id=point_id, vector=[0.0] * vector_size, payload=payload)],
            wait=True,
        )
        log.info("memory write log stored action={} target_id={}", action, target_id)


@lru_cache(maxsize=1)
def get_memory_writer() -> MemoryWriter:
    return MemoryWriter()
