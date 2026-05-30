from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from middleware.auth import require_login, require_role
from middleware.jwt_auth import JWTUser
from proxy.ai_monitor_proxy import AiMonitorProxy
from shared.constants import UserRole
from shared.schema.common import BaseResponse, PageData
from shared.schema.monitor import (
    MonitorAlertRecordVO,
    MonitorCleanupSummary,
    MonitorHealthStatus,
    MonitorOverviewStats,
    MonitorSessionDetail,
    MonitorSessionSummary,
    MonitorTurnSummary,
    TokenUsageItem,
    TokenUsageQueryRequest,
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


@router.post("/token-usage/query", response_model=BaseResponse[PageData[TokenUsageItem]])
async def query_token_usage(
    request: Request,
    payload: TokenUsageQueryRequest,
    login_user: JWTUser = Depends(require_login),
) -> BaseResponse[PageData[TokenUsageItem]]:
    # admin 可传 userId 查所有用户；普通用户 gateway 层过滤
    if login_user.user_role != UserRole.ADMIN.value:
        payload.user_id = str(login_user.user_id)

    result = await AiMonitorProxy().request_json(
        method="POST",
        path="/internal/token-usage/query",
        json_body=payload.model_dump(by_alias=True),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(PageData[TokenUsageItem].model_validate(result))


@router.get("/admin/monitor/alerts", response_model=BaseResponse[PageData[MonitorAlertRecordVO]])
async def list_monitor_alerts(
    request: Request,
    page_num: int = Query(default=1, alias="pageNum"),
    page_size: int = Query(default=10, alias="pageSize"),
    status: str | None = None,
    level: str | None = None,
    rule_name: str | None = Query(default=None, alias="ruleName"),
    session_id: str | None = Query(default=None, alias="sessionId"),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[PageData[MonitorAlertRecordVO]]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path="/internal/monitor/alerts",
        params={
            "pageNum": page_num,
            "pageSize": page_size,
            "status": status,
            "level": level,
            "ruleName": rule_name,
            "sessionId": session_id,
        },
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(PageData[MonitorAlertRecordVO].model_validate(payload))


@router.get("/admin/monitor/health", response_model=BaseResponse[MonitorHealthStatus])
async def get_monitor_health(
    request: Request,
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[MonitorHealthStatus]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="GET",
        path="/internal/monitor/health",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(MonitorHealthStatus.model_validate(payload))


@router.post("/admin/monitor/cleanup", response_model=BaseResponse[MonitorCleanupSummary])
async def cleanup_monitor_history(
    request: Request,
    retention_days: int = Query(default=7, alias="retentionDays"),
    dry_run: bool = Query(default=False, alias="dryRun"),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[MonitorCleanupSummary]:
    del login_user
    payload = await AiMonitorProxy().request_json(
        method="POST",
        path="/internal/monitor/cleanup",
        params={"retentionDays": retention_days, "dryRun": dry_run},
        trace_id=getattr(request.state, "trace_id", None),
    )
    return success(MonitorCleanupSummary.model_validate(payload))
