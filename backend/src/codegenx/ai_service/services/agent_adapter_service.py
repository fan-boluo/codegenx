from __future__ import annotations

from collections.abc import AsyncGenerator

from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest

from codegenx.ai_service.system_app import get_app, init_app
from shared import log


class AgentAdapterService:
    """薄壳：生命周期与引擎访问全部委托 SystemApp 全局容器（docs/SystemApp架构设计.md §3.3）。"""

    async def startup(self) -> None:
        await init_app().startup()

    async def shutdown(self) -> None:
        await get_app().shutdown()

    async def stream_message(
        self,
        request: AiServiceGenerateRequest
    ) -> AsyncGenerator[str, None]:
        runtime = get_app().runtime
        async for event in runtime.submit_request(request):
            log.info("stream message {}", event.model_dump_json())
            yield event.model_dump_json() + "\n"

    async def stop_session(
        self,
        *,
        app_id: str,
        user_id: str | None = None,
        session_id: str,
        trace_id: str,
        request_id: str,
        reason: str | None = None,
        grace_seconds: float | None = None,
    ) -> dict[str, object]:
        # 容器未启动（lifespan 未跑完）时与旧「runtime 未创建」行为一致：不受理
        try:
            runtime = get_app().runtime
        except RuntimeError:
            runtime = None
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