from __future__ import annotations

from collections.abc import AsyncGenerator

from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest

from codegenx.ai_service.system_app import get_app, init_app, start_app, stop_app
from shared import log


class AgentAdapterService:
    """薄壳：生命周期与引擎访问全部委托 SystemApp 全局容器（docs/SystemApp架构设计.md §3.3）。"""

    async def startup(self) -> None:
        # 两段式启动：init_app 注册组件+广播同步钩子；start_app 在事件循环内完成异步启动
        app = init_app()
        await start_app(app)

    async def shutdown(self) -> None:
        # 组件逆序关闭后，再统一释放基础设施（redis/qdrant/mysql）
        await stop_app()

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
        )