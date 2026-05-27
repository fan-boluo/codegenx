from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib.util
from collections.abc import AsyncGenerator
import os
from pathlib import Path
import sys

from shared.schema.ai_service import AiServiceGenerateRequest

REPO_ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))
BOT_ROOT = AI_SERVICE_ROOT / "bot"
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from bot.agent.runtime import AgentRuntime
from infra.mysql.session import shutdown_mysql_engine
from infra.redis.redis_client import redis_client
from shared.config.log_config import log


class AgentAdapterService:
    def __init__(self) -> None:
        self._runtime: AgentRuntime | None = None
        self._startup_lock = asyncio.Lock()
        self._started = False
        self._telemetry_started = False

    def _get_runtime(self) -> AgentRuntime:
        runtime = self._runtime
        if runtime is None:
            runtime = AgentRuntime()
            self._runtime = runtime
        return runtime

    def _init_telemetry(self) -> bool:
        # OTLP telemetry removed — monitor module now uses its own collectors.
        return False

    async def startup(self) -> None:
        async with self._startup_lock:
            if self._started:
                return
            # TODO 后台任务在哪里启动，现在有的是健康检查和session poll
            # 开启监控
            telemetry_started = self._init_telemetry()
            runtime = self._get_runtime()
            # runtime.start 启动runtime需要的任务
            await runtime.start()
            log.info("启动runtime完毕")
            self._started = True

    async def shutdown(self) -> None:
        async with self._startup_lock:
            if self._runtime is not None:
                with suppress(Exception):
                    await self._runtime.stop()
                self._runtime = None
            # 关闭本服务的实例
            with suppress(Exception):
                await redis_client.aclose()

            with suppress(Exception):
                await shutdown_mysql_engine()

            self._started = False

    async def stream_message(
        self,
        request: AiServiceGenerateRequest
    ) -> AsyncGenerator[str, None]:
        runtime = self._get_runtime()
        async for event in runtime.submit_request(request):
            log.info("stream message",event.model_dump_json()+ "\n")
            yield event.model_dump_json() + "\n"

    async def stop_session(
        self,
        *,
        app_id: int,
        user_id: str | None = None,
        session_id: str,
        trace_id: str,
        request_id: str,
        reason: str | None = None,
        grace_seconds: float | None = None,
    ) -> dict[str, object]:
        runtime = self._runtime
        if runtime is None:
            return {
                "accepted": False,
                "sessionId": session_id,
                "stoppedRequestCount": 0,
                "droppedRequestCount": 0,
                "activeRequestIds": [],
                "droppedRequestIds": [],
                "activeTurnIds": [],
            }
        return await runtime.stop_request(
            session_id=session_id,
            request_id=request_id,
            reason=str(reason or "user-stop"),
            grace_seconds=grace_seconds,
        )