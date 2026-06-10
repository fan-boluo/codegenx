from __future__ import annotations

from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import sys

import json as json_lib

from langchain_core.tools import retriever

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

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from core.service_registry import AiServiceRegistry
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.constants import get_current_session_dir
from bot.session.manager import SessionManager
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.ai_service import (
	AiServiceGenerateRequest,
	AiServiceStopRequest,
	AiServiceStopResponse
)
from shared.utils.result_utils import success
from guardrail.prompt_safety_input_guardrail import validate_prompt_safety
from shared.schema.monitor import (
	MonitorAlertQueryRequest,
	MonitorSessionQueryRequest,
	TokenUsageQueryRequest,
)
from monitor.monitor_query_service import get_monitor_query_service
from monitor.maintenance_service import get_monitor_maintenance_service

from pydantic import BaseModel

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
service_registry = AiServiceRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
	await agent_service.startup()
	await service_registry.startup()
	log.info("ai-service startup completed ")
	try:
		yield
	finally:
		await service_registry.shutdown()
		await agent_service.shutdown()
		log.info("ai-service runtime shutdown completed")


app = FastAPI(title="CodeGenX AI Service", version="1.0.0", lifespan=lifespan)


@app.post("/api/ai/chat/gen")
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
			"ai-service public stream failed traceId={} requestId={} appId={} error:{}",
			trace_id,
			request_id,
			request.app_id,
			str(exc)
		)
		raise


@app.post("/api/ai/chat/stop")
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
		return success(AiServiceStopResponse.model_validate(result).model_dump())
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
	try:
		result =  await get_monitor_query_service().get_overview()
		return success(result)
	except Exception as exc:
		log.exception(
			"ai-service internal/monitor/overview failed "
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc


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
	try:
		result =  await get_monitor_query_service().list_sessions(query)
		return success(result)
	except Exception as exc:
		log.exception(
			"ai-service /internal/monitor/sessions failed "
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/internal/monitor/sessions/{session_id}")
async def internal_get_monitor_session_detail(session_id: str):
	detail = await get_monitor_query_service().get_session_detail(session_id)
	if detail is None:
		log.exception("ai-service /internal/monitor/sessions failed")
		raise HTTPException(status_code=404, detail="session not found")
	return success(detail)


@app.get("/internal/monitor/sessions/{session_id}/turns/{turn_id}")
async def internal_get_monitor_turn_detail(session_id: str, turn_id: str):
	detail = await get_monitor_query_service().get_turn_detail(session_id, turn_id)
	if detail is None:
		log.exception("ai-service /internal/monitor/sessions failed")
		raise HTTPException(status_code=404, detail="turn not found")
	return success(detail)


@app.get("/internal/monitor/config")
async def internal_get_monitor_config():
	try:
		result =  await get_monitor_query_service().get_monitor_config()
		return success(result)
	except Exception as exc:
		log.exception(
			"ai-service /internal/monitor/config failed "
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/internal/monitor/alerts")
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
	except Exception as exc:
		log.exception(
			"ai-service /internal/monitor/alerts failed "
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/monitor/cleanup")
async def internal_cleanup_monitor_history(
	retention_days: int = Query(default=7, alias="retentionDays"),
	dry_run: bool = Query(default=False, alias="dryRun"),
):
	try:
		result = await get_monitor_maintenance_service().cleanup_history(
			retention_days=retention_days, dry_run=dry_run
		)
		return success(result)
	except Exception as exc:
		log.exception(
			"ai-service /internal/monitor/cleanup failed "
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc



# token消耗查询（内部端点，由 gateway 转发，auth 已在 gateway 层处理）
@app.post("/internal/token-usage/query")
async def internal_query_token_usage(query: TokenUsageQueryRequest):
	try :
		result = await get_monitor_query_service().query_token_usage(query)
		return success(result)
	except Exception as exc:
		log.exception(
			"ai-service /internal/token-usage/query failed "
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc




@app.get("/metrics", response_class=PlainTextResponse)
async def get_metrics():
	"""Prometheus metrics endpoint (scraped by Prometheus — internal network only)."""
	return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── 会话历史 API ──────────────────────────────────────────

class SessionListItem(BaseModel):
	session_id: str
	first_message: str
	create_time: str


@app.get("/api/ai/sessions/{app_id}")
async def list_sessions(app_id: int, limit: int = Query(default=5, ge=1, le=20)):
	"""列出 app 下最近的 session，从 session_index.json 直接读取（无目录扫描）。"""
	entries = SessionManager.read_session_index(str(app_id))
	return success([
		SessionListItem(
			session_id=e.get("session_id", ""),
			first_message=e.get("first_message", ""),
			create_time=e.get("create_time", ""),
		)
		for e in entries[:limit]
	])


@app.get("/api/ai/sessions/{app_id}/{session_id}/messages")
async def get_session_messages(
	app_id: int,
	session_id: str,
	limit: int = Query(default=50, ge=1, le=200),
):
	"""加载指定 session 的最近 N 条消息。"""
	session_dir = get_current_session_dir(app_id, session_id)
	if not session_dir.exists():
		raise HTTPException(status_code=404, detail="会话不存在")

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


@app.get("/api/ai/sessions/{app_id}/{session_id}/alive")
async def check_session_alive(app_id: int, session_id: str):
	"""检查 session 在内存池中是否活跃。"""
	alive = await agent_service._get_runtime().session_pool.exists(session_id)
	return success({"alive": alive})


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
