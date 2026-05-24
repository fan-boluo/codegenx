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


# def _ensure_local_constant_module() -> None:
#     existing = sys.modules.get("constant")
#     expected_path = AI_SERVICE_ROOT / "constant.py"
#     if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == expected_path.resolve():
#         return
#
#     spec = importlib.util.spec_from_file_location("constant", expected_path)
#     if spec is None or spec.loader is None:
#         raise ImportError(f"Cannot load ai-service constant module from {expected_path}")
#
#     module = importlib.util.module_from_spec(spec)
#     sys.modules["constant"] = module
#     spec.loader.exec_module(module)
#
#
# _ensure_local_constant_module()

from bot.agent.runtime import AgentRuntime
from infra.mysql.session import shutdown_mysql_engine
from infra.redis.redis_client import redis_client
from monitor.health_checker import get_health_checker
from shared.config.log_config import log


class AgentAdapterService:
    def __init__(self) -> None:
        self._runtime: AgentRuntime | None = None
        self._startup_lock = asyncio.Lock()
        self._startup_summary: dict[str, object] = {}
        self._started = False
        self._health_task: asyncio.Task | None = None
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

    async def _health_check_loop(self) -> None:
        checker = get_health_checker()
        while True:
            try:
                await checker.get_system_health()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ai-service background health check failed: {}", exc)
            await asyncio.sleep(60)

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
            # runtime主流程用不到的后台任务在这里启动
            if self._health_task is None:
                self._health_task = asyncio.create_task(
                    self._health_check_loop(),
                    name="ai-service-health-check",
                )
                log.info("启动健康检查")
            self._started = True

    async def shutdown(self) -> None:
        async with self._startup_lock:
            if self._health_task is not None:
                self._health_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._health_task
                self._health_task = None

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
            log.info("stream message",event.model_dump())
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