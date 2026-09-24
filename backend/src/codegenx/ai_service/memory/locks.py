"""
Redis 会话提炼锁（P1-6，设计方案 v2.1 §7.4）。

同一会话并发提炼用 mem:extract:lock:{session_id}（NX EX 300）互斥；
锁不是正确性的唯一屏障——锁失效时水位推进检查（last_seq 已前进则丢弃）
+ at-least-once 语义兜底，即「锁 + 幂等双保险」。

原 v1 的分布式文件锁随 json 事实源一起移除，多实例写同一用户记忆的
正确性由 MySQL 事务 + uk_slot 唯一约束保证，不再需要文件锁。
"""
from __future__ import annotations

import secrets

from shared import log
from codegenx.ai_service.memory import metrics

LOCK_TTL_SECONDS = 300  # §3.5


def _redis():
    from db.redis.redis_client import redis_client
    return redis_client


def _key(session_id: str) -> str:
    return f"mem:extract:lock:{session_id}"


async def acquire(session_id: str) -> str | None:
    """尝试抢锁。成功返回锁令牌（释放时校验归属），失败/Redis 不可用返回 None。

    Redis 不可用 → 返回特殊降级令牌 ""：直接放行（单消费者部署下安全，
    多实例部署时由水位检查兜底），打降级点。
    """
    token = secrets.token_hex(8)
    try:
        ok = await _redis().set(_key(session_id), token, nx=True, ex=LOCK_TTL_SECONDS)
        return token if ok else None
    except Exception as exc:  # noqa: BLE001 — Redis 故障降级放行
        metrics.inc_degrade("redis")
        log.warning("提炼锁 Redis 不可用，降级放行（水位检查兜底）: {}", exc)
        return ""


async def release(session_id: str, token: str) -> None:
    """释放锁（仅持锁者能删：Lua 校验令牌，防止误删他人的锁）。"""
    if token == "":
        return  # 降级放行的无锁模式，无事可做
    try:
        await _redis().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1, _key(session_id), token,
        )
    except Exception as exc:  # noqa: BLE001 — 释放失败靠 TTL 300s 过期兜底
        log.debug("提炼锁释放失败（TTL 兜底）: {}", exc)
