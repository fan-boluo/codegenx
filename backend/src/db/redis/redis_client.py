"""Redis client singleton."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as redis

from shared.config.config import get_settings

settings = get_settings()
redis_client = redis.from_url(settings.redis_dsn, decode_responses=True)

# 同MySQL的，也是fastapi的依赖注入函数，认证时会用到
async def get_redis_client() -> AsyncGenerator[redis.Redis, None]:
    yield redis_client
