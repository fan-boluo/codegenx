"""Chat Service API skeleton.时机"""

from __future__ import annotations

from pathlib import Path
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
LOCAL_SERVICES_ROOT = Path(__file__).resolve().parent / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from fastapi import Depends, FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from chat_api import router as chat_router
from chat_history_api import router as chat_history_router
from core.auth_proxy import JWTUser, require_login
from core.service_registry import ChatServiceRegistry
from shared.config.log_config import log
from shared.config.config import get_settings
from shared.utils.result_utils import success


settings = get_settings()
app = FastAPI(title="CodeGenX Chat Service", version="1.0.0")
app.include_router(chat_router)
app.include_router(chat_history_router)
service_registry = ChatServiceRegistry()


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        request.state.trace_id = trace_id
        log.info(
            "chat-service request start traceId={} method={} path={} client={}",
            trace_id,
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        log.info(
            "chat-service request end traceId={} method={} path={} status={}",
            trace_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response


app.add_middleware(TraceIdMiddleware)


@app.on_event("startup")
async def _startup_event() -> None:
    await service_registry.startup()


@app.on_event("shutdown")
async def _shutdown_event() -> None:
    await service_registry.shutdown()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.chat_service_name}


@app.get("/api/chat/health")
async def chat_health(request: Request, current_user: JWTUser = Depends(require_login)):
    return success(
        {
            "status": "ok",
            "serviceName": settings.chat_service_name,
            "userId": current_user.user_id,
            "traceId": getattr(request.state, "trace_id", None),
        }
    )