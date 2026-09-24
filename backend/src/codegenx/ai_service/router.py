"""AI 服务路由：Agent 对话流 / 会话历史 / 监控 / Prometheus 指标。

由原 ai-service/app.py 提取（去 sys.path 注入 / importlib 加载 / Nacos 注册）。
- chat_router  → /api/ai/*（原公开端点，现统一走网关认证）
- monitor_router → /api/stats/admin/*（替代原 routes.yaml 对 /internal 的重写转发，收紧为管理员）
- metrics_router → /metrics（无前缀，供 Prometheus 抓取）
"""

from __future__ import annotations

import json as json_lib

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from codegenx.ai_service.services.agent_adapter_service import AgentAdapterService
from codegenx.ai_service.session.manager import SessionManager
from codegenx.ai_service.guardrail.prompt_safety_input_guardrail import validate_prompt_safety
from codegenx.ai_service.monitor.monitor_query_service import get_monitor_query_service
from codegenx.ai_service.monitor.maintenance_service import get_monitor_maintenance_service
from codegenx.app_service.services.access import require_participant_by_id
from codegenx.gateway.middleware.auth import require_login, require_role
from codegenx.gateway.middleware.jwt_auth import JWTUser
from db.mysql.session import get_db_session
from shared import log
from shared.constants import get_current_session_dir
from codegenx.user_service.user_enums import UserRole
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from codegenx.ai_service.schema.ai_schema import (
    AiServiceGenerateRequest,
    AiServiceStopRequest,
    AiServiceStopResponse,
)
from codegenx.ai_service.monitor.schema.monitor import (
    MonitorAlertQueryRequest,
    MonitorSessionQueryRequest,
    TokenUsageQueryRequest,
)
from shared.utils.result_utils import success

agent_service = AgentAdapterService()


def get_agent_service() -> AgentAdapterService:
    """供单体应用 lifespan 启停 AgentRuntime。"""
    return agent_service


# ── 对话 / 会话历史 ──────────────────────────────────────────

chat_router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_login)])


class SessionListItem(BaseModel):
    session_id: str
    first_message: str
    create_time: str


@chat_router.post("/chat/gen")
async def generate_code_stream(request: AiServiceGenerateRequest):
    trace_id, request_id, session_id = _validate_call_context(request)
    log.info(
        "ai-service public stream request traceId={} requestId={} appId={} messageLen={} preview={}",
        trace_id,
        request_id,
        request.app_id,
        len(request.message),
        request.message[:80],
    )
    log.info("ai-service public stream request {} ", request.model_dump_json())
    try:
        stream = agent_service.stream_message(request)
        async def event_stream():
            async for chunk in stream:
                yield chunk

        return StreamingResponse(event_stream(), media_type="text/plain")
    except Exception as exc:
        log.exception(
            "ai-service public stream failed traceId={} requestId={} appId={} error:{}",
            trace_id,
            request_id,
            request.app_id,
            str(exc),
        )
        raise


@chat_router.post("/chat/stop")
async def stop_code_stream(request: AiServiceStopRequest):
    trace_id, request_id, session_id = _validate_stop_context(request)
    log.info(
        "ai-service public stop request traceId={} requestId={} appId={} sessionId={} reason={} graceSeconds={}",
        trace_id,
        request_id,
        request.app_id,
        session_id,
        request.reason,
        request.grace_seconds,
    )
    try:
        result = await agent_service.stop_session(
            app_id=request.app_id,
            user_id=request.user_id,
            session_id=session_id,
            trace_id=trace_id,
            request_id=request_id,
            reason=request.reason,
            grace_seconds=request.grace_seconds,
        )
        return success(AiServiceStopResponse.model_validate(result).model_dump(by_alias=True))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception(
            "ai-service public stop failed traceId={} requestId={} appId={} sessionId={}",
            trace_id,
            request_id,
            request.app_id,
            session_id,
        )
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


@chat_router.get("/sessions/{app_id}")
async def list_sessions(
    app_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    login_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    """列出「当前用户 + app」下最近的 session（会话按用户隔离）。"""
    await require_participant_by_id(db, app_id, login_user)
    entries = SessionManager.read_session_index(str(login_user.user_id), str(app_id))
    return success([
        SessionListItem(
            session_id=e.get("session_id", ""),
            first_message=e.get("first_message", ""),
            create_time=e.get("create_time", ""),
        )
        for e in entries[:limit]
    ])


@chat_router.get("/sessions/{app_id}/{session_id}/messages")
async def get_session_messages(
    app_id: int,
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    login_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    """加载指定 session 的最近 N 条消息（仅限项目成员，读取自己目录）。"""
    await require_participant_by_id(db, app_id, login_user)
    session_dir = get_current_session_dir(str(login_user.user_id), str(app_id), session_id)
    if not session_dir.exists():
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "会话不存在")

    messages: list[dict] = []

    # 从 chat_history JSONL 读取
    history_file = session_dir / f"chat_history_{session_id}.jsonl"
    try:
        if history_file.exists():
            lines = history_file.read_text(encoding="utf-8").splitlines()
            for line in lines[-limit:]:
                try:
                    msg = json_lib.loads(line.strip())
                    if isinstance(msg, dict):
                        messages.append(msg)
                except Exception:
                    continue
    except Exception as exc:
        log.warning("读取会话消息失败 session={}/{} err={}", app_id, session_id, exc)

    return success(messages)


@chat_router.get("/sessions/{app_id}/{session_id}/alive")
async def check_session_alive(
    app_id: int,
    session_id: str,
    login_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    """检查 session 在内存池中是否活跃（仅限项目成员）。"""
    await require_participant_by_id(db, app_id, login_user)
    alive = await agent_service._get_runtime().session_pool.exists(session_id)
    return success({"alive": alive})


# ── 监控 / 统计（原 /internal 端点，改挂 /api/stats/admin 并收紧为管理员） ──

monitor_router = APIRouter(prefix="/stats/admin", tags=["monitor"], dependencies=[Depends(require_role(UserRole.ADMIN))])


@monitor_router.get("/monitor/overview")
async def internal_get_monitor_overview():
    try:
        result = await get_monitor_query_service().get_overview()
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("monitor overview failed")
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


@monitor_router.get("/monitor/sessions")
async def internal_list_monitor_sessions(
    page_num: int = Query(default=1, alias="pageNum"),
    page_size: int = Query(default=10, alias="pageSize"),
    status: str | None = None,
    app_id: str | None = Query(default=None, alias="appId"),
    user_id: str | None = Query(default=None, alias="userId"),
    session_id: str | None = Query(default=None, alias="sessionId"),
    trace_id: str | None = Query(default=None, alias="traceId"),
):
    query = MonitorSessionQueryRequest(
        pageNum=page_num,
        pageSize=page_size,
        status=status,
        appId=app_id,
        userId=user_id,
        sessionId=session_id,
        traceId=trace_id,
    )
    try:
        result = await get_monitor_query_service().list_sessions(query)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("monitor list sessions failed")
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


@monitor_router.get("/monitor/sessions/{session_id}")
async def internal_get_monitor_session_detail(session_id: str):
    detail = await get_monitor_query_service().get_session_detail(session_id)
    if detail is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "session not found")
    return success(detail)


@monitor_router.get("/monitor/sessions/{session_id}/turns/{turn_id}")
async def internal_get_monitor_turn_detail(session_id: str, turn_id: str):
    detail = await get_monitor_query_service().get_turn_detail(session_id, turn_id)
    if detail is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "turn not found")
    return success(detail)


@monitor_router.get("/monitor/config")
async def internal_get_monitor_config():
    try:
        result = await get_monitor_query_service().get_monitor_config()
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("monitor config failed")
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


@monitor_router.get("/monitor/alerts")
async def internal_list_monitor_alerts(
    page_num: int = Query(default=1, alias="pageNum"),
    page_size: int = Query(default=10, alias="pageSize"),
    status: str | None = None,
    level: str | None = None,
    rule_name: str | None = Query(default=None, alias="ruleName"),
    session_id: str | None = Query(default=None, alias="sessionId"),
):
    query = MonitorAlertQueryRequest(
        pageNum=page_num,
        pageSize=page_size,
        status=status,
        level=level,
        ruleName=rule_name,
        sessionId=session_id,
    )
    try:
        result = await get_monitor_query_service().list_alerts(query)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("monitor list alerts failed")
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


@monitor_router.post("/monitor/cleanup")
async def internal_cleanup_monitor_history(
    retention_days: int = Query(default=7, alias="retentionDays"),
    dry_run: bool = Query(default=False, alias="dryRun"),
):
    try:
        result = await get_monitor_maintenance_service().cleanup_history(
            retention_days=retention_days, dry_run=dry_run
        )
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("monitor cleanup failed")
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


@monitor_router.post("/token-usage/query")
async def internal_query_token_usage(query: TokenUsageQueryRequest):
    try:
        result = await get_monitor_query_service().query_token_usage(query)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("token usage query failed")
        raise BusinessException(ErrorCode.SYSTEM_ERROR, str(exc)) from exc


# ── Prometheus 指标（无 /api 前缀挂载） ──────────────────────

metrics_router = APIRouter(tags=["metrics"])


@metrics_router.get("/metrics", response_class=PlainTextResponse)
async def get_metrics():
    """Prometheus metrics endpoint (scraped by Prometheus — internal network only)."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── 请求上下文校验 ──────────────────────────────────────────

def _validate_call_context(request: AiServiceGenerateRequest) -> tuple[str, str, str]:
    trace_id = str(request.trace_id or "").strip()
    request_id = str(request.request_id or "").strip()
    session_id = str(request.session_id or "").strip()
    if not trace_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "traceId 不能为空")
    if not request_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "requestId 不能为空")
    if not session_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "sessionId 不能为空")
    validate_prompt_safety(request.message)
    return trace_id, request_id, session_id


def _validate_stop_context(request: AiServiceStopRequest) -> tuple[str, str, str]:
    trace_id = str(request.trace_id or "").strip()
    request_id = str(request.request_id or "").strip()
    session_id = str(request.session_id or "").strip()
    if not trace_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "traceId 不能为空")
    if not request_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "requestId 不能为空")
    if not session_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "sessionId 不能为空")
    return trace_id, request_id, session_id
