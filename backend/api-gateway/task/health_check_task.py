"""Periodic health check task."""

from __future__ import annotations

import asyncio
import logging

from app.core.constants import HEALTH_CHECK_INTERVAL_SECONDS
from app.db.session import session_maker
from app.services.health_check_service import HealthCheckService

logger = logging.getLogger("app")

"""
健康检查定时任务，以后的定时任务都可以这样写：

1）定义一个检查任务：execute_health_check
2）使用async_loop来循环执行：run_loop，设置事件停止机制
3）在fastapi的app设置app启动、停止时定时任务的的启停：@app.on_event("startup")  @app.on_event("shutdown")

"""
class HealthCheckTask:
    async def execute_health_check(self) -> None:
        logger.debug("执行定时健康检查任务")
        try:
            async with session_maker() as db:
                await HealthCheckService(db).check_all_providers()
        except Exception:
            logger.exception("健康检查任务执行失败")

    # 使用事件机制进行停止和睡眠
    async def run_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.execute_health_check()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEALTH_CHECK_INTERVAL_SECONDS)
            except TimeoutError:
                continue

