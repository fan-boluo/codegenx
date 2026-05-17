from __future__ import annotations

import json
from pathlib import Path
import sys

LOCAL_SERVICES_ROOT = Path(__file__).resolve().parent / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.mysql.session import get_db_session
from shared.config.log_config import log
from shared.schema.app import AppChatRequest, AppChatStopRequest, AppChatStopResponse
from shared.schema.common import BaseResponse
from shared.utils.result_utils import success

from core.auth_proxy import JWTUser, require_login
from chat_service import ChatService


router = APIRouter(tags=["chat"])


@router.get("/api/app/chat/gen/code")
async def chat_to_gen_code_get(
    http_request: Request,
    app_id: int = Query(alias="appId"),
    message: str = Query(),
    session_id: str | None = Query(default=None, alias="sessionId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    trace_id = getattr(http_request.state, "trace_id", None)
    log.info(
        "app-service chat request traceId={} method=GET appId={} userId={} stream=true messageLen={} preview={}",
        trace_id,
        app_id,
        current_user.user_id,
        len(message),
        _preview_text(message),
    )
    request = AppChatRequest(appId=app_id, message=message, sessionId=session_id, requestId=request_id, stream=True)
    return await _stream_chat_response(http_request, request, current_user, db)


@router.post("/api/app/chat/gen/code", response_model=BaseResponse[str] | None)
async def chat_to_gen_code_post(
    payload: AppChatRequest,
    http_request: Request,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    trace_id = getattr(http_request.state, "trace_id", None)
    log.info(
        "app-service chat request traceId={} method=POST appId={} userId={} stream={} messageLen={} preview={}",
        trace_id,
        payload.app_id,
        current_user.user_id,
        payload.stream,
        len(payload.message),
        _preview_text(payload.message),
    )
    if payload.stream:
        return await _stream_chat_response(http_request, payload, current_user, db)
    try:
        chunks: list[str] = []
        async for chunk in ChatService(db).chat_to_gen_code(payload, current_user, trace_id=trace_id):
            chunks.append(chunk)
        log.info(
            "app-service non-stream completed traceId={} appId={} userId={} resultLen={} preview={}",
            trace_id,
            payload.app_id,
            current_user.user_id,
            len("".join(chunks)),
            _preview_text("".join(chunks)),
        )
        return success("".join(chunks))
    except Exception as exc:
        log.exception("chat to gen code failed traceId={} appId={} userId={}", trace_id, payload.app_id, current_user.user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/app/chat/stop", response_model=BaseResponse[AppChatStopResponse])
async def stop_chat_generation(
    payload: AppChatStopRequest,
    http_request: Request,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    trace_id = getattr(http_request.state, "trace_id", None)
    log.info(
        "app-service stop request traceId={} appId={} userId={} sessionId={} graceSeconds={} reason={}",
        trace_id,
        payload.app_id,
        current_user.user_id,
        payload.session_id,
        payload.grace_seconds,
        payload.reason,
    )
    try:
        result = await ChatService(db).stop_chat_generation(payload, current_user, trace_id=trace_id)
        return success(result)
    except Exception as exc:
        log.exception(
            "app-service stop failed traceId={} appId={} userId={} sessionId={}",
            trace_id,
            payload.app_id,
            current_user.user_id,
            payload.session_id,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _stream_chat_response(http_request: Request, request: AppChatRequest, current_user: JWTUser, db: AsyncSession) -> StreamingResponse:
    trace_id = getattr(http_request.state, "trace_id", None)

    async def event_stream():
        service = ChatService(db)
        try:
            async for chunk in service.chat_to_gen_code(request, current_user, trace_id=trace_id):
                payload = json.dumps({"d": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            yield "event: done\ndata: \n\n"
        except Exception as exc:
            log.exception(
                "app-service stream response failed traceId={} appId={} userId={}",
                trace_id,
                request.app_id,
                current_user.user_id,
            )
            payload = json.dumps(
                {
                    "error": True,
                    "message": "生成过程中出现错误，请稍后重试。",
                    "traceId": trace_id,
                },
                ensure_ascii=False,
            )
            yield f"event: business-error\ndata: {payload}\n\n"
            yield "event: done\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Trace-Id": trace_id or ""})


def _preview_text(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
