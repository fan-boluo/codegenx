from __future__ import annotations

from contextlib import asynccontextmanager
import json
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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from monitor.health_checker import get_health_checker
from monitor.maintenance_service import get_monitor_maintenance_service
from monitor.monitor_query_service import get_monitor_query_service
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.monitor import MonitorAlertQueryRequest, MonitorSessionQueryRequest
from shared.schema.ai_service import (
    AiServiceErrorPayload,
    AiServiceGenerateRequest,
	AiServiceStopRequest,
	AiServiceStopResponse,
    AiServiceStreamChunk,
    AiServiceStreamDone,
    AiServiceStreamMeta,
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
	startup_summary = await agent_service.startup()
	# app.state.agent_runtime_startup = startup_summary
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
		"ai-service public stream request traceId={} requestId={} appId={} requestedCodeGenType={} messageLen={} preview={}",
		trace_id,
		request_id,
		request.app_id,
		request.code_gen_type,
		len(request.message),
		_preview_text(request.message),
	)
	log.info(
		"ai-service public stream request {} ",request.model_dump_json())
	try:
		# stream = agent_service.stream_message(
		# 	app_id=request.app_id,
		# 	user_id=request.user_id,
		# 	session_id=session_id,
		# 	user_message=request.message,
		# 	trace_id=trace_id,
		# 	request_id=request_id,
		# 	requested_code_gen_type=request.code_gen_type,
		# )
		stream = agent_service.stream_message(request)
		async def event_stream():
			async for chunk in stream:
				yield chunk

		return StreamingResponse(event_stream(), media_type="text/plain")
	except Exception as exc:
		log.exception(
			"ai-service public stream failed traceId={} requestId={} appId={} requestedCodeGenType= {}",
			trace_id,
			request_id,
			request.app_id,
			request.code_gen_type,
		)
		raise HTTPException(status_code=500, detail=str(exc)) from exc


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


@app.post("/internal/ai/codegen/stop", response_model=AiServiceStopResponse)
async def internal_stop_code_stream(request: AiServiceStopRequest):
	return await stop_code_stream(request)


@app.post("/internal/ai/codegen/stream")
async def internal_generate_code_stream(request: AiServiceGenerateRequest, http_request: Request):
	trace_id, request_id, session_id = _validate_call_context(request)
	host_header = http_request.headers.get("host")
	upstream_instance = host_header or f"{settings.ai_service_host}:{getattr(settings, 'ai_service_http_port', '8002')}"
	log.info(
		"ai-service internal stream request traceId={} requestId={} upstreamInstance={} appId={} requestedCodeGenType={} messageLen={} preview={}",
		trace_id,
		request_id,
		upstream_instance,
		request.app_id,
		request.code_gen_type,
		len(request.message),
		_preview_text(request.message),
	)
	try:
		stream, resolved_mode, stream_returns_events = await _build_internal_stream(request, trace_id, request_id, session_id)
	except BusinessException as exc:
		return _internal_error_response(
			status_code=400,
			error=exc,
			trace_id=trace_id,
			request_id=request_id,
			upstream_instance=upstream_instance,
		)
	except Exception as exc:
		log.exception(
			"internal ai stream bootstrap failed traceId={} requestId={} appId={} requestedCodeGenType={}",
			trace_id,
			request_id,
			request.app_id,
			request.code_gen_type,
		)
		return _internal_error_response(
			status_code=500,
			error=BusinessException(ErrorCode.SYSTEM_ERROR, f"AI 服务启动流式生成失败: {exc}"),
			trace_id=trace_id,
			request_id=request_id,
			upstream_instance=upstream_instance,
			retryable=True,
		)

	async def event_stream():
		chunk_index = 0
		first_chunk_logged = False
		meta = AiServiceStreamMeta(
			traceId=trace_id,
			requestId=request_id,
			upstreamInstance=upstream_instance,
			timeoutMs=_timeout_ms(),
			idempotencyMode="best-effort",
		)
		yield _encode_sse_event("meta", meta.model_dump_json(by_alias=True))
		try:
			async for item in stream:
				if stream_returns_events:
					event = item
					if event.event_type == "LLM_Response_Chunk":
						chunk = str(event.data or "")
						if not first_chunk_logged:
							log.info(
								"ai-service internal stream first chunk traceId={} requestId={} appId={} mode={} preview={}",
								trace_id,
								request_id,
								request.app_id,
								resolved_mode,
								_preview_text(chunk),
							)
							first_chunk_logged = True
						payload = AiServiceStreamChunk(content=chunk, index=chunk_index)
						yield _encode_sse_event("chunk", payload.model_dump_json(by_alias=True))
						chunk_index += 1
						continue

					if event.event_type == "Error":
						error_payload = AiServiceErrorPayload(
							code=ErrorCode.SYSTEM_ERROR.get_code(),
							message=str(event.data or "AI 服务流式生成失败"),
							traceId=trace_id,
							requestId=request_id,
							upstreamInstance=upstream_instance,
							retryable=False,
						)
						yield _encode_sse_event("error", error_payload.model_dump_json(by_alias=True))
						break

					yield _encode_sse_event(event.event_type, _encode_agent_event_payload(event))
					continue

				chunk = item
				if not first_chunk_logged:
					log.info(
						"ai-service internal stream first chunk traceId={} requestId={} appId={} mode={} preview={}",
						trace_id,
						request_id,
						request.app_id,
						resolved_mode,
						_preview_text(chunk),
					)
					first_chunk_logged = True
				payload = AiServiceStreamChunk(content=chunk, index=chunk_index)
				yield _encode_sse_event("chunk", payload.model_dump_json(by_alias=True))
				chunk_index += 1
			done = AiServiceStreamDone(traceId=trace_id, requestId=request_id, totalChunks=chunk_index)
			log.info(
				"ai-service internal stream completed traceId={} requestId={} appId={} mode={} totalChunks={}",
				trace_id,
				request_id,
				request.app_id,
				resolved_mode,
				chunk_index,
			)
			yield _encode_sse_event("done", done.model_dump_json(by_alias=True))
		except BusinessException as exc:
			error_payload = AiServiceErrorPayload(
				code=exc.code,
				message=exc.message,
				traceId=trace_id,
				requestId=request_id,
				upstreamInstance=upstream_instance,
				retryable=False,
			)
			yield _encode_sse_event("error", error_payload.model_dump_json(by_alias=True))
			done = AiServiceStreamDone(traceId=trace_id, requestId=request_id, totalChunks=chunk_index)
			yield _encode_sse_event("done", done.model_dump_json(by_alias=True))
		except Exception as exc:
			log.exception(
				"internal ai stream failed traceId={} requestId={} appId={} mode={}",
				trace_id,
				request_id,
				request.app_id,
				resolved_mode,
			)
			error_payload = AiServiceErrorPayload(
				code=ErrorCode.SYSTEM_ERROR.get_code(),
				message=f"AI 服务流式生成失败: {exc}",
				traceId=trace_id,
				requestId=request_id,
				upstreamInstance=upstream_instance,
				retryable=False,
			)
			yield _encode_sse_event("error", error_payload.model_dump_json(by_alias=True))
			done = AiServiceStreamDone(traceId=trace_id, requestId=request_id, totalChunks=chunk_index)
			yield _encode_sse_event("done", done.model_dump_json(by_alias=True))

	return StreamingResponse(
		event_stream(),
		media_type="text/event-stream",
		headers=_build_internal_headers(trace_id, request_id, upstream_instance, "best-effort"),
	)


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
	return await get_monitor_query_service().list_alerts(query)


@app.get("/internal/monitor/config")
async def internal_get_monitor_config():
	return await get_monitor_query_service().get_monitor_config()


@app.get("/internal/monitor/health")
async def internal_get_monitor_health():
	return await get_health_checker().get_system_health()


@app.post("/internal/monitor/cleanup")
async def internal_cleanup_monitor_history(
	retention_days: int = Query(default=7, alias="retentionDays", ge=1),
	dry_run: bool = Query(default=True, alias="dryRun"),
):
	return await get_monitor_maintenance_service().cleanup_history(
		retention_days=retention_days,
		dry_run=dry_run,
	)


@app.get("/internal/monitor/metrics", response_class=PlainTextResponse)
async def internal_get_monitor_metrics():
	return PlainTextResponse(await get_monitor_maintenance_service().render_metrics_text())


async def _build_internal_stream(request: AiServiceGenerateRequest, trace_id: str, request_id: str, session_id: str):
	if not request.message.strip():
		raise BusinessException(ErrorCode.PARAMS_ERROR, "生成消息不能为空")
	if hasattr(agent_service, "stream_events"):
		stream = agent_service.stream_events(request)
		return stream, request.code_gen_type or "agent-decided", True
	stream = agent_service.stream_message(request)
	return stream, request.code_gen_type or "agent-decided", False


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


def _internal_error_response(
	*,
	status_code: int,
	error: BusinessException,
	trace_id: str,
	request_id: str,
	upstream_instance: str,
	retryable: bool = False,
):
	payload = AiServiceErrorPayload(
		code=error.code,
		message=error.message,
		traceId=trace_id,
		requestId=request_id,
		upstreamInstance=upstream_instance,
		retryable=retryable,
	)
	return JSONResponse(
		status_code=status_code,
		content=payload.model_dump(by_alias=True),
		headers=_build_internal_headers(trace_id, request_id, upstream_instance, "error"),
	)


def _build_internal_headers(trace_id: str, request_id: str, upstream_instance: str, idempotency_mode: str) -> dict[str, str]:
	return {
		"X-Trace-Id": trace_id,
		"X-Upstream-Instance": upstream_instance,
		"X-Idempotency-Key": request_id,
		"X-Idempotency-Mode": idempotency_mode,
		"X-Request-Timeout-Ms": str(_timeout_ms()),
	}


def _timeout_ms() -> int:
	return int(settings.ai_timeout_seconds * 1000)


def _encode_sse_event(event_name: str, payload: str) -> str:
	return f"event: {event_name}\ndata: {payload}\n\n"


def _encode_agent_event_payload(event) -> str:
	payload = {
		"eventType": event.event_type,
		"state": getattr(event.state, "value", str(event.state)),
		"data": event.data,
	}
	return json.dumps(payload, ensure_ascii=False, default=str)


def _preview_text(text: str, limit: int = 80) -> str:
	compact = " ".join(text.split())
	return compact[:limit]
