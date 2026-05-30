"""Chat routes forwarded by gateway to ai-service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from redis.asyncio import Redis

from infra.redis.redis_client import get_redis_client
from middleware.auth import require_login
from middleware.jwt_auth import JWTUser
from proxy.chat_proxy import ChatProxy
from services.rate_limit_service import RateLimitService


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/gen/code")
async def chat_to_gen_code_post(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    login_user: JWTUser = Depends(require_login),
    redis: "Redis" = Depends(get_redis_client),  # type: ignore[type-arg]
):
    await RateLimitService(redis).check_user_rate_limit(
        login_user.user_id, "gen_code", 5
    )

    if "userId" not in payload:
        payload["userId"] = str(login_user.user_id)

    proxy = ChatProxy()
    return await proxy.stream_sse(
        method="POST",
        path="/api/ai/codegen/stream",
        authorization=authorization,
        trace_id=getattr(request.state, "trace_id", None),
        json_body=payload,
    )


@router.post("/stop")
async def stop_chat_generation(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    login_user: JWTUser = Depends(require_login),
):
    proxy = ChatProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/ai/codegen/stop",
        authorization=authorization,
        trace_id=getattr(request.state, "trace_id", None),
        json_body=payload,
    )
