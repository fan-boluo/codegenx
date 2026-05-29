import time

from redis.asyncio import Redis
from core.constants import (
    RATE_LIMIT_IP_PREFIX,
    RATE_LIMIT_API_PREFIX,
    CHAT_USER_RATE_LIMIT_PER_SECOND,
    API_RATE_LIMIT_MAX,
    API_RATE_LIMIT_WINDOW_SECONDS,
)


class RateLimitService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    # 使用固定窗口的，因为AI调用本身就有延迟，出现窗口边界流量激增的情况很难
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

    async def check_ip_rate_limit(self, ip: str, limit: int, window_seconds: int = 1) -> bool:
        key = f"{RATE_LIMIT_IP_PREFIX}{ip}"
        return await self.try_acquire(key, limit, window_seconds)

    async def check_api_rate_limit(self, api_path: str, limit: int = API_RATE_LIMIT_MAX, window_seconds: int = API_RATE_LIMIT_WINDOW_SECONDS) -> bool:
        key = f"{RATE_LIMIT_API_PREFIX}{api_path}"
        return await self.try_acquire(key, limit, window_seconds)

    # 下面是使用滑动窗口的
    # 为什么都要设置过期时间：如果用户就只访问了一次，那么它的这个key将一直留存，因为只有在下次调用的时候才会去清理过期数据
    # 不调用就永远不会过期，因此才要设置过期时间，而且是+1s作为缓冲
    def try_acquire_with_sliding_window(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds

        # 1. 原子操作: 清理过期数据 + 计数 + 添加当前数据
        # 使用 Lua 脚本保证原子性, 避免竞争问题
        lua_script = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        local member = ARGV[4]

        -- 1. 删除窗口外的数据
        redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

        -- 2. 获取当前窗口内的数量
        local current_count = redis.call('ZCARD', key)

        -- 3. 如果未超限, 添加新数据
        if current_count < limit then
            redis.call('ZADD', key, now, member)
            -- 设置过期时间, 防止内存泄漏 (虽然ZREMRANGEBYSCORE会清理, 但设置过期更安全)
            redis.call('EXPIRE', key, tonumber(ARGV[5]))
            return 1 -- 允许
        else
            return 0 -- 拒绝
        end
        """

        request_id = str(now)  # 唯一ID
        expire_time = int(window_seconds) + 1  # 窗口大小 + 1秒

        # 执行 Lua 脚本
        result = self.redis.eval(lua_script, 1, key, now, window_start, limit, request_id, expire_time)
        print(result)
        return result == 1

