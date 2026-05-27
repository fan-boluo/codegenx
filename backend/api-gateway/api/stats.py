from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from middleware.auth import require_role
from middleware.jwt_auth import JWTUser
from proxy.ai_monitor_proxy import AiMonitorProxy
from shared.constants import UserRole
from shared.schema.common import BaseResponse, PageData
from shared.schema.monitor import (
    MonitorOverviewStats,
    MonitorSessionDetail,
    MonitorSessionSummary,
    MonitorTurnSummary,
)
from shared.utils.result_utils import success

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/admin/monitor/overview", response_model=BaseResponse[MonitorOverviewStats])
async def get_monitor_overview(
    request: Request,
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[MonitorOverviewStats]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path="/internal/monitor/overview",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(MonitorOverviewStats.model_validate(payload))


@router.get("/admin/monitor/sessions", response_model=BaseResponse[PageData[MonitorSessionSummary]])
async def list_monitor_sessions(
    request: Request,
    page_num: int = Query(default=1, alias="pageNum"),
    page_size: int = Query(default=10, alias="pageSize"),
    status: str | None = None,
    app_id: str | None = Query(default=None, alias="appId"),
    user_id: str | None = Query(default=None, alias="userId"),
    session_id: str | None = Query(default=None, alias="sessionId"),
    trace_id: str | None = Query(default=None, alias="traceId"),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[PageData[MonitorSessionSummary]]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path="/internal/monitor/sessions",
        params={
            "pageNum": page_num,
            "pageSize": page_size,
            "status": status,
            "appId": app_id,
            "userId": user_id,
            "sessionId": session_id,
            "traceId": trace_id,
        },
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(PageData[MonitorSessionSummary].model_validate(payload))


@router.get("/admin/monitor/sessions/{session_id}", response_model=BaseResponse[MonitorSessionDetail])
async def get_monitor_session_detail(
    session_id: str,
    request: Request,
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[MonitorSessionDetail]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path=f"/internal/monitor/sessions/{session_id}",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(MonitorSessionDetail.model_validate(payload))


@router.get("/admin/monitor/sessions/{session_id}/turns/{turn_id}", response_model=BaseResponse[MonitorTurnSummary])
async def get_monitor_turn_detail(
    session_id: str,
    turn_id: str,
    request: Request,
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[MonitorTurnSummary]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path=f"/internal/monitor/sessions/{session_id}/turns/{turn_id}",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(MonitorTurnSummary.model_validate(payload))


@router.get("/admin/monitor/config", response_model=BaseResponse[dict[str, Any]])
async def get_monitor_config(
    request: Request,
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[dict[str, Any]]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path="/internal/monitor/config",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(payload)


@router.get("/admin/monitor/metrics", response_class=PlainTextResponse)
async def get_monitor_metrics(
    request: Request,
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> PlainTextResponse:
    del login_user
    payload = await AiMonitorProxy().request_text(
        method="GET",
        path="/metrics",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return PlainTextResponse(payload)
