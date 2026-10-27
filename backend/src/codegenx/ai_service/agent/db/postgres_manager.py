"""PostgreSQL 业务库只读执行通道（asyncpg）。

与 MysqlManager 同契约：只读守卫 + 超时 + list[dict] 返回。
目标库由调用方传入（公共库的 database 名，如 business），连接凭据复用
shared.config 的 POSTGRES_*（同实例、按库名切换）。
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import asyncpg

from shared import log
from shared.config.config import get_settings
from db.postgres.postgres_client import postgres_business

DEFAULT_QUERY_TIMEOUT = 30

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

_pools: dict[str, asyncpg.Pool] = {}
_lock = asyncio.Lock()


def _quote_ident(name: str) -> str:
    """schema 名做安全校验后加双引号，禁止拼接任意标识符。"""
    if not _IDENT_RE.match(name):
        raise ValueError(f"非法 schema 名: {name}")
    return f'"{name}"'


def _json_safe(value: Any) -> Any:
    """把 asyncpg 返回的 Decimal/日期/数组等转换为可序列化值。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return value


async def _get_pool(db_name: str) -> asyncpg.Pool:
    pool = _pools.get(db_name)
    if pool is not None:
        return pool
    async with _lock:
        pool = _pools.get(db_name)
        if pool is None:
            s = get_settings()
            pool = await asyncpg.create_pool(
                dsn=postgres_business(db_name),
                min_size=max(1, s.postgres_pool_min),
                max_size=max(s.postgres_pool_min, s.postgres_pool_max),
                command_timeout=DEFAULT_QUERY_TIMEOUT,
            )
            _pools[db_name] = pool
            log.info("业务 PG 连接池已创建: {}/{}", s.postgres_host, db_name)
    return pool


async def query(
    db_name: str,
    sql: str,
    *,
    schema: str = "",
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT,
) -> list[dict[str, Any]]:
    """在业务库执行只读查询，schema 通过事务级 search_path 生效。"""
    from codegenx.ai_service.utils.dbutils import check_sql_safety
    check_sql_safety(sql)
    pool = await _get_pool(db_name)

    async def _run() -> list[dict[str, Any]]:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                if schema:
                    await conn.execute(f"SET LOCAL search_path TO {_quote_ident(schema)}")
                records = await conn.fetch(sql)
                return [{k: _json_safe(v) for k, v in r.items()} for r in records]

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        log.warning("PG 查询超时 ({}s): {}", timeout_seconds, sql[:120])
        raise


async def close_pools() -> None:
    for db_name, pool in list(_pools.items()):
        await pool.close()
        _pools.pop(db_name, None)
