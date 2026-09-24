"""
hot 层 Redis 缓存（P1-1，设计方案 v2.1 §3.5/§5.1）。

key: mem:hot:{app_id}:{user_id}  String(JSON)  TTL 24h
值： 该用户全部 active hot 记忆的最小注入视图（按裁剪优先级排好序）。

失效策略是「写时 DEL 而不是写时 SET」：并发写场景 SET 会把旧值写回缓存
造成脏数据；DEL 让下一次读触发重建，简单且正确。

Redis 只做缓存绝不做真源：不可用时直接回落 MySQL（idx_hot_load 单点查很快）。
"""
from __future__ import annotations

import json

from shared import log
from codegenx.ai_service.memory import metrics

HOT_CACHE_TTL_SECONDS = 86400  # 24h


def _key(app_id: str, user_id: str) -> str:
    return f"mem:hot:{app_id}:{user_id}"


def _redis():
    from db.redis.redis_client import redis_client
    return redis_client


async def get_cached_entries(app_id: str, user_id: str) -> list[dict] | None:
    """读缓存。命中返回条目列表；miss/Redis 不可用返回 None（调用方回落 MySQL）。"""
    try:
        raw = await _redis().get(_key(app_id, user_id))
    except Exception as exc:  # noqa: BLE001 — Redis 故障降级，不阻断
        metrics.inc_degrade("redis")
        log.warning("hot 缓存读取失败，回落 MySQL:{}", exc)
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        # 反序列化后二次校验租户（§8：防 key 拼接错误）
        if isinstance(data, list) and data and data[0].get("user_id") == str(user_id):
            return data
        return None
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


async def set_cached_entries(app_id: str, user_id: str, items: list[dict]) -> None:
    """回填缓存（仅元数据字段，不含正文以外的冗余）。失败静默。"""
    try:
        await _redis().set(
            _key(app_id, user_id),
            json.dumps(items, ensure_ascii=False),
            ex=HOT_CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — 缓存回填失败无害
        log.debug("hot 缓存回填失败（非致命）:{}", exc)


async def invalidate(app_id: str, user_id: str) -> None:
    """写时 DEL（§3.5）。失败静默——TTL 24h 兜底最终一致。"""
    try:
        await _redis().delete(_key(app_id, user_id))
    except Exception as exc:  # noqa: BLE001
        log.debug("hot 缓存 DEL 失败（TTL 兜底）:{}", exc)


def to_cache_items(entries) -> list[dict]:
    """MemoryEntry 列表 → 缓存最小视图（注入文本 + 预算裁剪所需字段）。"""
    return [
        {
            "memory_id": e.memory_id,
            "app_id": e.app_id,
            "user_id": e.user_id,
            "memory_type": e.memory_type,
            "inject_text": e.inject_text(),
            "token_cost": e.token_cost,
        }
        for e in entries
    ]
