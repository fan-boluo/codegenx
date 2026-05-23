
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from infra.redis.redis_client import redis_client
from monitor.monitor_store import MonitorStore, get_monitor_store
from shared.schema.monitor import MonitorComponentHealth, MonitorHealthStatus
from utils.config import load_config


_HEALTH_CHECKER_SINGLETON: "HealthChecker | None" = None
_HEALTH_CHECKER_LOCK = Lock()


class HealthChecker:
    def __init__(
        self,
        *,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        monitor_store: MonitorStore | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory or session_maker
        self._monitor_store = monitor_store or get_monitor_store()

    async def check_llm_connectivity(self) -> bool:
        config = load_config()
        agent = config.get_default_agent()
        provider = config.get_provider(agent.provider)
        return bool((provider.api_base or config.providers.custom.api_base) and agent.resolved_model_name)

    async def check_tool_availability(self, tool_name: str) -> bool:
        return bool(tool_name and tool_name.strip())

    async def get_system_health(self) -> MonitorHealthStatus:
        monitor_enabled = bool(load_config().monitor.enabled)
        components = [
            await self._check_mysql(),
            await self._check_redis(),
            self._check_store_runtime(),
            self._check_llm_runtime(),
        ]
        statuses = [item.status for item in components]
        overall_status = "up"
        if any(status == "down" for status in statuses):
            overall_status = "down"
        elif any(status == "degraded" for status in statuses):
            overall_status = "degraded"

        return MonitorHealthStatus(
            enabled=monitor_enabled,
            overallStatus=overall_status,
            degraded=overall_status == "degraded",
            checkedAt=datetime.utcnow(),
            components=components,
        )

    async def _check_mysql(self) -> MonitorComponentHealth:
        started = time.perf_counter()
        try:
            async with self._db_session_factory() as session:
                await session.execute(text("SELECT 1"))
            latency_ms = int((time.perf_counter() - started) * 1000)
            return MonitorComponentHealth(
                name="mysql",
                status="up",
                checkedAt=datetime.utcnow(),
                latencyMs=latency_ms,
                message="ok",
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return MonitorComponentHealth(
                name="mysql",
                status="down",
                checkedAt=datetime.utcnow(),
                latencyMs=latency_ms,
                message=str(exc),
            )

    async def _check_redis(self) -> MonitorComponentHealth:
        started = time.perf_counter()
        try:
            pong = await redis_client.ping()
            latency_ms = int((time.perf_counter() - started) * 1000)
            return MonitorComponentHealth(
                name="redis",
                status="up" if pong else "down",
                checkedAt=datetime.utcnow(),
                latencyMs=latency_ms,
                message="ok" if pong else "ping failed",
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return MonitorComponentHealth(
                name="redis",
                status="down",
                checkedAt=datetime.utcnow(),
                latencyMs=latency_ms,
                message=str(exc),
            )

    def _check_store_runtime(self) -> MonitorComponentHealth:
        runtime_state = self._monitor_store.get_status()
        degraded = bool(runtime_state.get("degraded"))
        return MonitorComponentHealth(
            name="monitor_store",
            status="degraded" if degraded else "up",
            checkedAt=datetime.utcnow(),
            consecutiveFailures=int(runtime_state.get("consecutive_failures") or 0),
            lastSuccessAt=runtime_state.get("last_success_at"),
            lastErrorAt=runtime_state.get("last_error_at"),
            message=str(runtime_state.get("last_error") or "ok"),
            metadata={
                "lastAction": runtime_state.get("last_action") or "",
                "totalFailures": int(runtime_state.get("total_failures") or 0),
                "totalRetries": int(runtime_state.get("total_retries") or 0),
            },
        )

    def _check_llm_runtime(self) -> MonitorComponentHealth:
        config = load_config()
        agent = config.get_default_agent()
        provider = config.get_provider(agent.provider)
        ready = bool((provider.api_key or config.providers.custom.api_key) and agent.resolved_model_name)
        return MonitorComponentHealth(
            name="llm_config",
            status="up" if ready else "degraded",
            checkedAt=datetime.utcnow(),
            message="configured" if ready else "llm api key or model is missing",
            metadata={
                "provider": agent.provider,
                "model": agent.resolved_model_name,
            },
        )


def get_health_checker() -> HealthChecker:
    global _HEALTH_CHECKER_SINGLETON
    if _HEALTH_CHECKER_SINGLETON is not None:
        return _HEALTH_CHECKER_SINGLETON

    with _HEALTH_CHECKER_LOCK:
        if _HEALTH_CHECKER_SINGLETON is None:
            _HEALTH_CHECKER_SINGLETON = HealthChecker()
    return _HEALTH_CHECKER_SINGLETON


# ============================================================
# 6. 业务判断（非 OTel 范畴）
# ============================================================

class QuotaGuard:

    async def check_quota_exceeded(
        self,
        session_id: str,
        tokens_used: int,
        max_tokens: int
    ) -> bool: ...

    async def heartbeat(
        self,
        session_id: str,
        turn: int
    ) -> None: ...
