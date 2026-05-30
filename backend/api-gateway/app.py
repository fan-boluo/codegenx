"""FastAPI gateway application."""

from __future__ import annotations

import asyncio
import sys
import traceback
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.health import router as health_router
from api.user import router as user_router
from api.app import router as app_router
from api.chat_history import router as chat_history_router
from api.stats import router as stats_router
from api.blacklist import router as blacklist_router
from shared.utils.result_utils import error
from shared.exceptions.error_code import ErrorCode
from shared.config.log_config import log
from middleware.ip_blacklist import IpBlacklistMiddleware
from shared.config.config import get_settings
from shared.exceptions.business_exception import BusinessException

settings = get_settings()


def _format_exception(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


class TraceIdMiddleware(BaseHTTPMiddleware):
    # 链路追踪中间件，为每个请求生成唯一的traceId
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        request.state.trace_id = request_id
        request.state.login_user_id = None
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request_id
        return response


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)
    # fastapi的中间件是倒序执行的，先添加的后执行，这个黑名单拦截是最后执行的
    app.add_middleware(IpBlacklistMiddleware)
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(BusinessException)
    async def business_exception_handler(request: Request, exc: BusinessException):
        if exc.code == ErrorCode.NOT_LOGIN_ERROR.get_code():
            log.info(
                "BusinessException path={} userId={} code={} message={} traceId={}",
                request.url.path,
                getattr(request.state, "login_user_id", None),
                exc.code,
                exc.message,
                getattr(request.state, "trace_id", None),
            )
        else:
            log.error(
                "BusinessException path={} userId={} code={} message={} traceId={}\n{}",
                request.url.path,
                getattr(request.state, "login_user_id", None),
                exc.code,
                exc.message,
                getattr(request.state, "trace_id", None),
                _format_exception(exc),
            )
        return JSONResponse(
            status_code=200,
            content=error(exc.code, exc.message).model_dump(by_alias=True),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        log.error(
            "ValidationException path={} userId={} code={} message={} traceId={} details={}",
            request.url.path,
            getattr(request.state, "login_user_id", None),
            ErrorCode.PARAMS_ERROR,
            "请求参数错误",
            getattr(request.state, "trace_id", None),
            exc.errors(),
        )
        return JSONResponse(
            status_code=200,
            content=error(ErrorCode.PARAMS_ERROR.get_code(), "请求参数错误").model_dump(by_alias=True),
        )

    @app.exception_handler(Exception)
    async def runtime_exception_handler(request: Request, exc: Exception):
        log.error(
            "RuntimeException path={} userId={} code={} message={} traceId={}\n{}",
            request.url.path,
            getattr(request.state, "login_user_id", None),
            ErrorCode.SYSTEM_ERROR,
            str(exc),
            getattr(request.state, "trace_id", None),
            _format_exception(exc),
        )
        return JSONResponse(
            status_code=200,
            content=error(ErrorCode.SYSTEM_ERROR.get_code(), "系统错误").model_dump(by_alias=True),
        )

    app.include_router(user_router, prefix=settings.app_base_path)
    app.include_router(app_router, prefix=settings.app_base_path)
    app.include_router(chat_history_router, prefix=settings.app_base_path)
    app.include_router(health_router, prefix=settings.app_base_path)
    app.include_router(stats_router, prefix=settings.app_base_path)
    app.include_router(blacklist_router, prefix=settings.app_base_path)

    return app


app = create_app()
