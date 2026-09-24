"""网关聊天路由 — SSE 流 + 限流 + userId 注入。

进程内直调 ai_service 的流式处理器，不再经 HTTP 代理转发
（顺带修复了原代理指向不存在路径 /api/ai/codegen/stream 的问题）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from codegenx.ai_service.router import generate_code_stream, stop_code_stream
from codegenx.gateway.middleware.auth import require_login
from codegenx.gateway.middleware.jwt_auth import JWTUser
from codegenx.gateway.services.rate_limit_service import RateLimitService
from db.redis.redis_client import get_redis_client
from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest, AiServiceStopRequest

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/gen/code")
async def chat_to_gen_code_post(
    payload: dict[str, Any],
    login_user: JWTUser = Depends(require_login),
    redis: "Redis" = Depends(get_redis_client),  # type: ignore[type-arg]
):
    await RateLimitService(redis).check_user_rate_limit(
        login_user.user_id, "gen_code", 5
    )

    if "userId" not in payload:
        payload["userId"] = str(login_user.user_id)

    request = AiServiceGenerateRequest.model_validate(payload)
    return await generate_code_stream(request)


@router.post("/stop")
async def chat_to_gen_code_stop(
    payload: dict[str, Any],
    login_user: JWTUser = Depends(require_login),
):
    if "userId" not in payload:
        payload["userId"] = str(login_user.user_id)

    request = AiServiceStopRequest.model_validate(payload)
    return await stop_code_stream(request)
