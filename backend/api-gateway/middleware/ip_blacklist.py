"""IP blacklist middleware."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware

from shared.utils.result_utils import error
from shared.exceptions.error_code import ErrorCode
from shared.config.log_config import log
from infra.redis.redis_client import redis_client
from services.blacklist_service import BlacklistService
from shared.utils.request import get_client_ip

class IpBlacklistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = get_client_ip(request)
        try:
            blocked = await BlacklistService(redis_client).is_blocked(client_ip)
        except RedisError as exc:
            log.warning("ip blacklist check skipped clientIp={} error={}", client_ip, exc)
            blocked = False
        if blocked:
            payload = error(ErrorCode.FORBIDDEN_ERROR.get_code(), "您的 IP 已被封禁")
            return JSONResponse(
                status_code=403,
                content=payload.model_dump(by_alias=True),
            )
        return await call_next(request)
