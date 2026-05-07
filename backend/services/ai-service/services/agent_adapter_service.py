from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib.util
from collections.abc import AsyncGenerator
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_local_constant_module() -> None:
    existing = sys.modules.get("constant")
    expected_path = AI_SERVICE_ROOT / "constant.py"
    if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == expected_path.resolve():
        return

    spec = importlib.util.spec_from_file_location("constant", expected_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load ai-service constant module from {expected_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["constant"] = module
    spec.loader.exec_module(module)


_ensure_local_constant_module()

from bot.agent.runtime import AgentEvent, AgentRuntime
from bot.bus import RuntimeTurnRequest
from bot.utils.config import load_config
from infra.mysql.session import shutdown_mysql_engine
from infra.qdrant.client import shutdown_qdrant_client
from infra.redis.redis_client import redis_client
from monitor.health_checker import get_health_checker
from monitor.telemetry_sdk import TelemetrySDK
from shared.config.log_config import log


class AgentAdapterService:
    def __init__(self) -> None:
        self._runtime: AgentRuntime | None = None
        self._startup_lock = asyncio.Lock()
        self._startup_summary: dict[str, object] = {}
        self._started = False
        self._health_task: asyncio.Task | None = None
        self._telemetry_started = False

    def _app_key(self, app_id: int) -> str:
        return str(app_id)

    def _get_runtime(self) -> AgentRuntime:
        runtime = self._runtime
        if runtime is None:
            runtime = AgentRuntime()
            self._runtime = runtime
        return runtime

    def _init_telemetry(self) -> bool:
        endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip()
        if not endpoint:
            return False

        try:
            TelemetrySDK.init(
                service_name="codegenx-ai-service",
                service_version="1.0.0",
                otlp_endpoint=endpoint,
                resource_attributes={"service.component": "ai-service"},
            )
            self._telemetry_started = True
            return True
        except Exception as exc:
            log.warning("Failed to initialize telemetry SDK: {}", exc)
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

    async def startup(self) -> dict[str, object]:
        async with self._startup_lock:
            if self._started:
                return dict(self._startup_summary)

            telemetry_started = self._init_telemetry()
            summary = await AgentRuntime.startup_process_runtime()
            runtime = self._get_runtime()
            await runtime.start()

            if self._health_task is None:
                self._health_task = asyncio.create_task(
                    self._health_check_loop(),
                    name="ai-service-health-check",
                )

            summary["telemetry_started"] = telemetry_started
            summary["runtime_mode"] = "shared"
            summary["preloaded_runtimes"] = ["shared-runtime"]
            summary["background_tasks"] = [
                "agent_runtime_dispatcher",
                "otel_span_flush" if telemetry_started else "otel_span_flush_skipped",
                "otel_metrics_flush" if telemetry_started else "otel_metrics_flush_skipped",
                "health_checker",
            ]
            self._startup_summary = summary
            self._started = True
            return dict(summary)

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

            if self._telemetry_started:
                with suppress(Exception):
                    TelemetrySDK.shutdown()
                self._telemetry_started = False

            with suppress(Exception):
                await redis_client.aclose()
            with suppress(Exception):
                await shutdown_qdrant_client()
            with suppress(Exception):
                await shutdown_mysql_engine()

            self._started = False

    def _build_turn_request(
        self,
        *,
        app_id: int,
        user_id: str | None,
        session_id: str,
        user_message: str,
        trace_id: str,
        request_id: str,
        requested_code_gen_type: str | None,
    ) -> RuntimeTurnRequest:
        normalized_user_id = str(user_id or "").strip()
        metadata = {
            "request_id": request_id,
            "trace_id": trace_id,
        }
        if requested_code_gen_type:
            metadata["requested_code_gen_type"] = requested_code_gen_type
        if normalized_user_id:
            metadata["user_id"] = normalized_user_id
        return RuntimeTurnRequest(
            app_id=self._app_key(app_id),
            user_id=normalized_user_id,
            session_id=session_id,
            turn_id=request_id,
            trace_id=trace_id,
            request_id=request_id,
            user_input=user_message,
            requested_code_gen_type=requested_code_gen_type,
            client_version="ai-service",
            metadata=metadata,
        )

    async def stream_events(
        self,
        *,
        app_id: int,
        user_id: str | None = None,
        session_id: str,
        user_message: str,
        trace_id: str,
        request_id: str,
        requested_code_gen_type: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        request = self._build_turn_request(
            app_id=app_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            trace_id=trace_id,
            request_id=request_id,
            requested_code_gen_type=requested_code_gen_type,
        )
        runtime = self._get_runtime()
        async for event in runtime.submit_turn(request):
            yield event

    async def stream_message(
        self,
        *,
        app_id: int,
        user_id: str | None = None,
        session_id: str,
        user_message: str,
        trace_id: str,
        request_id: str,
        requested_code_gen_type: str | None = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self.stream_events(
            app_id=app_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            trace_id=trace_id,
            request_id=request_id,
            requested_code_gen_type=requested_code_gen_type,
        ):
            if event.event_type == "LLM_Response_Chunk" and event.data:
                yield str(event.data)
                continue
            if event.event_type == "Error":
                message = str(event.data or "agent execution failed")
                raise RuntimeError(message)

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
                "stoppedTurnCount": 0,
                "droppedTurnCount": 0,
                "activeTurnIds": [],
                "droppedTurnIds": [],
            }
        return await runtime.stop_session(
            session_id=session_id,
            reason=str(reason or "user-stop"),
            grace_seconds=grace_seconds,
        )

    def get_session_id(self, app_id: int, session_id: str) -> str:
        return session_id