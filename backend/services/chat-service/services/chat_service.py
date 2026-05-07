from __future__ import annotations

from collections.abc import AsyncGenerator
import time

from sqlalchemy.ext.asyncio import AsyncSession

from shared.config.log_config import log
from shared.enums.chat_history_message_type import ChatHistoryMessageTypeEnum
from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.throw_utils import ThrowUtils
from shared.monitor.monitor_context import MonitorContext, MonitorContextHolder
from shared.orm.app import App
from shared.schema.app import AppChatRequest, AppChatStopRequest, AppChatStopResponse

from core.app_client import AppServiceClient
from core.auth_proxy import JWTUser
from core.ai_client import AiServiceClient
from chat_history import ChatHistoryService


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.chat_history_service = ChatHistoryService(db)
        self.ai_service_client = AiServiceClient()
        self.app_service_client = AppServiceClient()

    # 核心方法 生成代码，调用agent
    async def chat_to_gen_code(
        self,
        request: AppChatRequest,
        login_user: JWTUser,
        trace_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        if not trace_id:
            raise ValueError("trace_id is required")
        if not request.request_id:
            raise ValueError("request_id is required")
        if not request.session_id:
            raise ValueError("session_id is required")
        log.info(
            "chat-service codegen start traceId={} appId={} userId={} stream={} messageLen={} preview={}",
            trace_id,
            request.app_id,
            login_user.user_id,
            request.stream,
            len(request.message),
            _preview_text(request.message),
        )
        app = await self._get_owned_app(request.app_id, login_user)
        code_gen_type = CodeGenTypeEnum.get_enum_by_value(app.code_gen_type)
        await self.chat_history_service.add_chat_message(
            request.app_id,
            request.message,
            ChatHistoryMessageTypeEnum.USER.value,
            login_user.user_id,
        )
        started_at = time.perf_counter()
        monitor_context = MonitorContext(
            user_id=str(login_user.user_id),
            app_id=str(request.app_id),
            trace_id=trace_id,
            request_id=request.request_id,
        )
        MonitorContextHolder.set_context(monitor_context)
        ai_response_chunks: list[str] = []
        result: dict[str, str | None] | None = None
        try:
            log.info(
                "chat-service calling ai-service traceId={} appId={} userId={} codeGenType={}",
                trace_id,
                request.app_id,
                login_user.user_id,
                code_gen_type.value if code_gen_type else None,
            )
            async for chunk in self.ai_service_client.generate_code_stream(
                user_message=request.message,
                code_gen_type=code_gen_type,
                app_id=request.app_id,
                user_id=str(login_user.user_id),
                trace_id=trace_id,
                request_id=request.request_id,
                session_id=request.session_id,
            ):
                if monitor_context.first_chunk_latency_ms is None:
                    monitor_context.first_chunk_latency_ms = int((time.perf_counter() - started_at) * 1000)
                    log.info(
                        "chat-service ai first chunk traceId={} appId={} latencyMs={} preview={}",
                        trace_id,
                        request.app_id,
                        monitor_context.first_chunk_latency_ms,
                        _preview_text(chunk),
                    )
                monitor_context.chunk_count += 1
                ai_response_chunks.append(chunk)
                yield chunk

            if ai_response_chunks:
                merged_code = "".join(ai_response_chunks)
                log.info(
                    "chat-service ai completed traceId={} appId={} chunkCount={} resultLen={} preview={}",
                    trace_id,
                    request.app_id,
                    monitor_context.chunk_count,
                    len(merged_code),
                    _preview_text(merged_code),
                )
                result = await self.app_service_client.save_generated_code(
                    app_id=request.app_id,
                    code_gen_type=code_gen_type,
                    code_content=merged_code,
                    trace_id=trace_id,
                )
                log.info(
                    "chat-service app-service save completed traceId={} appId={} result={}",
                    trace_id,
                    request.app_id,
                    result,
                )
            else:
                log.warning("chat-service ai returned empty result traceId={} appId={}", trace_id, request.app_id)
        except Exception:
            log.exception(
                "chat-service codegen failed traceId={} appId={} userId={} chunkCount={}",
                trace_id,
                request.app_id,
                login_user.user_id,
                monitor_context.chunk_count,
            )
            raise
        finally:
            monitor_context.total_latency_ms = int((time.perf_counter() - started_at) * 1000)
            log.info(
                "chat-service codegen stream traceId={} appId={} userId={} upstreamInstance={} firstChunkLatencyMs={} totalLatencyMs={} chunkCount={}",
                monitor_context.trace_id,
                monitor_context.app_id,
                monitor_context.user_id,
                monitor_context.upstream_instance,
                monitor_context.first_chunk_latency_ms,
                monitor_context.total_latency_ms,
                monitor_context.chunk_count,
            )
            MonitorContextHolder.clear_context()
            if ai_response_chunks:
                await self.chat_history_service.add_chat_message(
                    request.app_id,
                    "".join(ai_response_chunks),
                    ChatHistoryMessageTypeEnum.AI.value,
                    login_user.user_id,
                )

    async def stop_chat_generation(
        self,
        request: AppChatStopRequest,
        login_user: JWTUser,
        trace_id: str | None = None,
    ) -> AppChatStopResponse:
        if not trace_id:
            raise ValueError("trace_id is required")
        if not request.request_id:
            raise ValueError("request_id is required")
        if not request.session_id:
            raise ValueError("session_id is required")
        await self._get_owned_app(request.app_id, login_user)
        result = await self.ai_service_client.stop_generation(
            app_id=request.app_id,
            user_id=str(login_user.user_id),
            trace_id=trace_id,
            request_id=request.request_id,
            session_id=request.session_id,
            reason=request.reason,
            grace_seconds=request.grace_seconds,
        )
        return AppChatStopResponse.model_validate(result.model_dump(by_alias=True))
            

    async def _get_owned_app(self, app_id: int, login_user: JWTUser) -> App:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用 ID 错误")
        app = await self.db.get(App, app_id)
        ThrowUtils.throw_if(app is None, ErrorCode.NOT_FOUND_ERROR, "应用不存在")
        ThrowUtils.throw_if(app.user_id != login_user.user_id, ErrorCode.NO_AUTH_ERROR, "无权限访问该应用")
        return app
    
def _preview_text(text: str, limit: int = 100) -> str:
    compact = " ".join(text.split())
    return compact[:limit]