from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


Resolver = Callable[[str], Awaitable[str]]


class ServiceDiscoveryCache:
    def __init__(self, ttl_seconds: int = 3) -> None:
        self.ttl_seconds = max(1, ttl_seconds)
        self._cache: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, cache_key: str, resolver: Resolver) -> str:
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(cache_key)
            if cached and cached[1] > time.monotonic():
                return cached[0]

            resolved = await resolver(cache_key)
            self._cache[cache_key] = (resolved, time.monotonic() + self.ttl_seconds)
            return resolved

    def invalidate(self, cache_key: str) -> None:
        self._cache.pop(cache_key, None)


service_discovery_cache = ServiceDiscoveryCache()
