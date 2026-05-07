"""Qdrant memory client singleton."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient, models

from shared.config.config import get_settings

SHORT_TERM_COLLECTION = "short_term_memories"
LONG_TERM_COLLECTION = "long_term_memories"
CONSOLIDATION_LOG_COLLECTION = "memory_consolidation_logs"


@dataclass(frozen=True)
class MemoryCollectionSpec:
	name: str
	distance: models.Distance = models.Distance.COSINE
	payload_indexes: tuple[tuple[str, Any], ...] = ()


MEMORY_COLLECTION_SPECS = (
	MemoryCollectionSpec(
		name=SHORT_TERM_COLLECTION,
		payload_indexes=(
			(
				"content",
				models.TextIndexParams(
					type=models.TextIndexType.TEXT,
					tokenizer=models.TokenizerType.MULTILINGUAL,
					lowercase=True,
					phrase_matching=True,
				),
			),
			("user_id", models.PayloadSchemaType.KEYWORD),
			("app_id", models.PayloadSchemaType.KEYWORD),
			("source_date", models.PayloadSchemaType.KEYWORD),
			("memory_type", models.PayloadSchemaType.KEYWORD),
			("importance", models.PayloadSchemaType.FLOAT),
			("expires_at", models.PayloadSchemaType.INTEGER),
		),
	),
	MemoryCollectionSpec(
		name=LONG_TERM_COLLECTION,
		payload_indexes=(
			(
				"content",
				models.TextIndexParams(
					type=models.TextIndexType.TEXT,
					tokenizer=models.TokenizerType.MULTILINGUAL,
					lowercase=True,
					phrase_matching=True,
				),
			),
			("user_id", models.PayloadSchemaType.KEYWORD),
			("app_id", models.PayloadSchemaType.KEYWORD),
			("memory_type", models.PayloadSchemaType.KEYWORD),
			("importance", models.PayloadSchemaType.FLOAT),
		),
	),
	MemoryCollectionSpec(
		name=CONSOLIDATION_LOG_COLLECTION,
		payload_indexes=(
			("action", models.PayloadSchemaType.KEYWORD),
			("target_id", models.PayloadSchemaType.KEYWORD),
			("performed_at", models.PayloadSchemaType.INTEGER),
		),
	),
)


class QdrantMemoryClient:
	def __init__(self) -> None:
		self.settings = get_settings()
		self.client = QdrantClient(
			host=self.settings.qdrant_url,
			port=self.settings.qdrant_port,
			api_key=self.settings.qdrant_api_key or None,
			timeout=30,
		)

	def ensure_memory_collections(self) -> dict[str, bool]:
		status: dict[str, bool] = {}
		for spec in MEMORY_COLLECTION_SPECS:
			status[spec.name] = self._ensure_collection(spec)
		return status

	def _ensure_collection(self, spec: MemoryCollectionSpec) -> bool:
		if not self.client.collection_exists(spec.name):
			self.client.create_collection(
				collection_name=spec.name,
				vectors_config=models.VectorParams(
					size=self.settings.qdrant_size,
					distance=spec.distance,
				),
			)

		self._ensure_payload_indexes(spec)
		return True

	def _ensure_payload_indexes(self, spec: MemoryCollectionSpec) -> None:
		for field_name, field_schema in spec.payload_indexes:
			try:
				if field_name == "content":
					try:
						self.client.delete_payload_index(
							collection_name=spec.name,
							field_name=field_name,
							wait=True,
						)
					except Exception:
						pass
				self.client.create_payload_index(
					collection_name=spec.name,
					field_name=field_name,
					field_schema=field_schema,
					wait=True,
				)
			except Exception:
				continue


@lru_cache(maxsize=1)
def get_qdrant_memory_client() -> QdrantMemoryClient:
	client = QdrantMemoryClient()
	client.ensure_memory_collections()
	return client


async def warm_up_qdrant_client() -> QdrantMemoryClient:
	client = await asyncio.to_thread(get_qdrant_memory_client)
	await asyncio.to_thread(client.client.get_collections)
	return client


async def shutdown_qdrant_client() -> None:
	if get_qdrant_memory_client.cache_info().currsize == 0:
		return
	client = get_qdrant_memory_client()
	close = getattr(client.client, "close", None)
	if callable(close):
		await asyncio.to_thread(close)
	get_qdrant_memory_client.cache_clear()
