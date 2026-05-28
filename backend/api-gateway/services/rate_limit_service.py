import time

from redis.asyncio import Redis
from core.constants import RATE_LIMIT_IP_PREFIX, CHAT_USER_RATE_LIMIT_PER_SECOND


class RateLimitService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def try_acquire(self, key: str, limit: int, window_seconds: int) -> bool:
        current = await self.redis.incr(key)
        if current == 1:
            await self.redis.expire(key, window_seconds)
        return int(current) <= limit

    async def get_available_permits(self, key: str, limit: int) -> int:
        current = await self.redis.get(key)
        used = int(current) if current else 0
        remain = limit - used
        return remain if remain > 0 else 0

    async def check_user_rate_limit(self, user_id: int, action: str, limit: int | None = None) -> bool:
        effective_limit = limit if limit is not None else CHAT_USER_RATE_LIMIT_PER_SECOND
        key = f"rate_limit:user:{user_id}:{action}"
        return await self.try_acquire(key, effective_limit, 1)

    async def check_ip_rate_limit(self, ip: str, limit: int) -> bool:
        key = f"{RATE_LIMIT_IP_PREFIX}{ip}"
        return await self.try_acquire(key, limit, 1)

    def try_acquire_with_sliding_window(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds

        lua_script = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        local member = ARGV[4]

        redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
        local current_count = redis.call('ZCARD', key)

        if current_count < limit then
            redis.call('ZADD', key, now, member)
            redis.call('EXPIRE', key, tonumber(ARGV[5]))
            return 1
        else
            return 0
        end
        """

        request_id = str(now)
        expire_time = int(window_seconds) + 1

        result = self.redis.eval(lua_script, 1, key, now, window_start, limit, request_id, expire_time)
        print(result)
        return result == 1
