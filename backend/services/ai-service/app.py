from __future__ import annotations

from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
	sys.path.insert(0, str(AI_SERVICE_ROOT))
BOT_ROOT = AI_SERVICE_ROOT / "bot"
if str(BOT_ROOT) not in sys.path:
	sys.path.insert(0, str(BOT_ROOT))
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))
LOCAL_SERVICES_ROOT = Path(__file__).resolve().parent / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
	sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from monitor.maintenance_service import get_monitor_maintenance_service
from monitor.monitor_query_service import get_monitor_query_service
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.monitor import MonitorSessionQueryRequest, TokenUsageQueryRequest
from shared.schema.ai_service import (
    AiServiceGenerateRequest,
    AiServiceStopRequest,
    AiServiceStopResponse
)


def _load_local_module(module_name: str, file_name: str):
	spec = importlib.util.spec_from_file_location(f"ai_service_{module_name}", LOCAL_SERVICES_ROOT / file_name)
	if spec is None or spec.loader is None:
		raise ImportError(f"Cannot load local module {module_name} from {file_name}")
	module = importlib.util.module_from_spec(spec)
	sys.modules[f"ai_service_{module_name}"] = module
	spec.loader.exec_module(module)
	return module


AgentAdapterService = _load_local_module("agent_adapter_service", "agent_adapter_service.py").AgentAdapterService

agent_service = AgentAdapterService()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
	await agent_service.startup()
	log.info("ai-service startup completed ")
	try:
		yield
	finally:
		await agent_service.shutdown()
		log.info("ai-service runtime shutdown completed")


app = FastAPI(title="CodeGenX AI Service", version="1.0.0", lifespan=lifespan)


@app.post("/api/ai/codegen/stream")
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
	log.info(
		"ai-service public stream request {} ",request.model_dump_json())
	try:
		stream = agent_service.stream_message(request)
		async def event_stream():
			async for chunk in stream:
				yield chunk

		return StreamingResponse(event_stream(), media_type="text/plain")
	except Exception as exc:
		log.exception(
			"ai-service public stream failed traceId={} requestId={} appId={}",
			trace_id,
			request_id,
			request.app_id,
		)
		raise


@app.post("/api/ai/codegen/stop", response_model=AiServiceStopResponse)
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
		return AiServiceStopResponse.model_validate(result)
	except Exception as exc:
		log.exception(
			"ai-service public stop failed traceId={} requestId={} appId={} sessionId={}",
			trace_id,
			request_id,
			request.app_id,
			session_id,
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc


# 监控
@app.get("/internal/monitor/overview")
async def internal_get_monitor_overview():
	return await get_monitor_query_service().get_overview()


@app.get("/internal/monitor/sessions")
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
	return await get_monitor_query_service().list_sessions(query)


@app.get("/internal/monitor/sessions/{session_id}")
async def internal_get_monitor_session_detail(session_id: str):
	detail = await get_monitor_query_service().get_session_detail(session_id)
	if detail is None:
		raise HTTPException(status_code=404, detail="session not found")
	return detail


@app.get("/internal/monitor/sessions/{session_id}/turns/{turn_id}")
async def internal_get_monitor_turn_detail(session_id: str, turn_id: str):
	detail = await get_monitor_query_service().get_turn_detail(session_id, turn_id)
	if detail is None:
		raise HTTPException(status_code=404, detail="turn not found")
	return detail


@app.get("/internal/monitor/config")
async def internal_get_monitor_config():
	return await get_monitor_query_service().get_monitor_config()


# token消耗查询（内部端点，由 gateway 转发，auth 已在 gateway 层处理）
@app.post("/internal/token-usage/query")
async def internal_query_token_usage(query: TokenUsageQueryRequest):
    return await get_monitor_query_service().query_token_usage(query)


@app.get("/metrics", response_class=PlainTextResponse)
async def get_metrics():
    """Prometheus metrics endpoint (scraped by Prometheus — internal network only)."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)



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
