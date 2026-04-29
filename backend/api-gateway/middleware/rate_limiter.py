"""Rate limiter middleware."""

from __future__ import annotations

from collections import defaultdict
from time import time

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from shared.utils.cache_key_utils import CacheKeyUtils


class RateLimiter:
    """Distributed rate limiter using Redis."""

    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int
    ) -> bool:
        """Check if request is within rate limit."""
        cache_key = f"rate_limit:{key}"

        # Use Redis sorted set to track requests
        now = time()
        window_start = now - window_seconds

        # Remove old entries
        await self.redis.zremrangebyscore(cache_key, 0, window_start)

        # Count current requests in window
        count = await self.redis.zcard(cache_key)

        if count >= limit:
            return False

        # Add current request
        await self.redis.zadd(cache_key, {str(now): now})
        await self.redis.expire(cache_key, window_seconds)

        return True

    async def check_user_rate_limit(
        self,
        user_id: int,
        action: str,
        limit: int = 5,
        window_seconds: int = 60
    ) -> None:
        """Check user-specific rate limit."""
        key = CacheKeyUtils.gen_user_rate_limit_key(user_id, action)
        if not await self.check_rate_limit(key, limit, window_seconds):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {action}"
            )

    async def check_ip_rate_limit(
        self,
        ip: str,
        action: str,
        limit: int = 10,
        window_seconds: int = 60
    ) -> None:
        """Check IP-based rate limit."""
        key = f"ip:{ip}:{action}"
        if not await self.check_rate_limit(key, limit, window_seconds):
            raise HTTPException(
                status_code=429,
                detail=f"IP rate limit exceeded for {action}"
            )

    async def check_api_rate_limit(
        self,
        api_path: str,
        limit: int = 100,
        window_seconds: int = 60
    ) -> None:
        """Check API endpoint rate limit."""
        key = f"api:{api_path}"
        if not await self.check_rate_limit(key, limit, window_seconds):
            raise HTTPException(
                status_code=429,
                detail=f"API rate limit exceeded for {api_path}"
            )


# In-memory fallback for development
class InMemoryRateLimiter:
    """Simple in-memory rate limiter for development."""

    def __init__(self):
        self.requests = defaultdict(list)

    def check_rate_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Check rate limit in memory."""
        now = time()
        window_start = now - window_seconds

        # Clean old requests
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= limit:
            return False

        self.requests[key].append(now)
        return True