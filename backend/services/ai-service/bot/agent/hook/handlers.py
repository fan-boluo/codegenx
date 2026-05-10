from __future__ import annotations

import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
import uuid

from agent import runtime
from agent.context import get_context_assembler
from agent.runtime_schema import AgentState
from memory.memory_manager import get_memory_manager
from monitor.alert_evaluator import get_monitor_alert_evaluator
from monitor.monitor_pipeline import get_monitor_pipeline
from monitor.span_context import SpanContext
from monitor.telemetry_schema import SessionTelemetry, TelemetryStatus, TurnTelemetry, TurnToolMetrics
from bot.session.manager import SessionManager
from prompt.runtime_prompt import DEFAULT_PROMPT_TEMPLATE
from shared.config.log_config import log

if TYPE_CHECKING:
    from bot.agent.runtime import RuntimeSessionState


MEMORY_TOOL_NAMES = {
    "memory_search",
    "memory_get",
    "write_short_term",
    "write_long_term",
    "write_identity_memory",
}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _session_status(state: Any) -> TelemetryStatus:
    normalized = str(getattr(state, "value", state) or "").lower()
    if normalized == "failed":
        return TelemetryStatus.ERROR
    if normalized == "stopped":
        return TelemetryStatus.STOPPED
    if normalized == "completed":
        return TelemetryStatus.SUCCESS
    return TelemetryStatus.RUNNING


def _ensure_session_telemetry(session: Any) -> SessionTelemetry:
    telemetry = getattr(session, "telemetry", None)
    if isinstance(telemetry, SessionTelemetry):
        return telemetry

    telemetry = SessionTelemetry(
        trace_id=session.trace_id,
        session_id=session.session_id,
        app_id=session.app_id,
        user_id=session.user_id,
        started_at=_utcnow(),
        status=TelemetryStatus.RUNNING,
    )
    session.telemetry = telemetry
    return telemetry


def _ensure_turn_telemetry(turn: Any, session: Any) -> TurnTelemetry:
    telemetry = getattr(turn, "telemetry", None)
    if isinstance(telemetry, TurnTelemetry):
        return telemetry

    telemetry = TurnTelemetry(
        trace_id=session.trace_id,
        session_id=session.session_id,
        request_id=str(getattr(turn.request, "request_id", "") or ""),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        started_at=_utcnow(),
        status=TelemetryStatus.RUNNING,
    )
    turn.telemetry = telemetry
    return telemetry


def _tool_status(result: Any) -> TelemetryStatus:
    if isinstance(result, dict):
        if result.get("error"):
            return TelemetryStatus.ERROR
        if result.get("success") is False:
            return TelemetryStatus.ERROR
    return TelemetryStatus.SUCCESS


def _memory_hits(result: Any) -> int:
    if isinstance(result, dict):
        details = result.get("details")
        if isinstance(details, dict) and isinstance(details.get("results"), list):
            return len(details["results"])
        data = result.get("data")
        if isinstance(data, list):
            return len(data)
    return 0


def _duration_ms_from_datetimes(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


def _db_only_span(trace_id: str) -> SpanContext:
    return SpanContext(trace_id=trace_id, span_id=uuid.uuid4().hex[:16], span=None)


async def _persist_span(
    *,
    session: Any,
    span_ctx: Any,
    operation_name: str,
    start_time: datetime,
    status: str,
    attributes: dict[str, Any] | None = None,
    parent_span_id: str | None = None,
    turn_id: str = "",
    turn_number: int = 0,
    end_time: datetime | None = None,
) -> None:
    if span_ctx is None:
        return

    await get_monitor_pipeline().persist_span(
        app_id=session.app_id,
        user_id=session.user_id,
        trace_id=span_ctx.trace_id,
        span_id=span_ctx.span_id,
        parent_span_id=parent_span_id,
        session_id=session.session_id,
        turn_id=turn_id,
        turn_number=turn_number,
        operation_name=operation_name,
        start_time=start_time,
        end_time=end_time,
        duration_ms=_duration_ms_from_datetimes(start_time, end_time) if end_time is not None else None,
        status=status,
        attributes=attributes,
    )


async def on_session_start(session: RuntimeSessionState, **kwargs):
    runtime = session.runtime or None
    request = session.request or None
    if runtime is None or request is None:
        log.warning("runtime or request is None ,on session start pass")
        return
    # 上下文
    context = kwargs.get("context")
    if context is None:
        return

    session_manager = SessionManager(str(request.app_id))
    session.session_manager = session_manager

    # 组装tool skill到session
    await get_context_assembler().build_fix_context(session)

    # 聊天记录
    now = _utcnow().isoformat()
    request_dict = request.model_dump()
    request_dict["started_at"]=now
    session_manager.append_chat_history_message(
        session.session_id,
        request_dict,
    )

    # 监控初始化
    span_attribute = request_dict.copy()
    span_attribute['last_activity_at'] = now
    span_attribute['turn_number'] = 0

    session.state = AgentState.RUNNING

    telemetry = _ensure_session_telemetry(session)
    telemetry.model = getattr(session.runtime.agent_config, "resolved_model_name", "unknown")
    telemetry.token_budget = 0
    pipeline = get_monitor_pipeline()
    # session需要
    # 根span的固定值写入，封装一下



    # 子span初始化，写入需要初始化的属性，封装一下 就用tele_schema里面的
    session.session_span = pipeline.on_session_start(
        session_id=session.session_id,
        user_id=session.user_id,
        trace=session.client_version,
        model=telemetry.model,
        tokens_remaining=0,
    )
    session.session_span_started_at = _utcnow()
    await _persist_span(
        session=session,
        span_ctx=session.session_span,
        operation_name="agent.session",
        start_time=session.session_span_started_at,
        status=TelemetryStatus.RUNNING.value,
        attributes={
            "client.version": session.client_version,
            "model.name": telemetry.model,
            "span.type": "root",
            "user.id": session.user_id,
        },
    )


async def on_turn_start(turn: Any, **kwargs):
    session = kwargs["session"]
    snapshot = {
        "session": dict(session.audit_context),
        "turn": {
            "request_id": turn.request.request_id,
            "turn_id": turn.turn_id,
            "turn_number": turn.turn_number,
            "code_dir": turn.code_dir,
            "safe_paths": list(turn.safe_paths),
            "workspace_metadata": dict(turn.workspace_metadata),
            "knowledge_cache": dict(turn.knowledge_cache),
            "plan_summary": turn.plan_summary,
            "context": turn.context.model_dump(),
        },
    }
    snapshot_path = session.session_manager.save_turn_snapshot(turn.turn_id, snapshot)
    turn.snapshot_path = str(snapshot_path)
    telemetry = _ensure_turn_telemetry(turn, session)
    telemetry.turn_number = turn.turn_number
    turn_span = get_monitor_pipeline().on_turn_start(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
    )
    turn.active_span_refs["turn"] = turn_span or _db_only_span(session.trace_id)
    turn.active_span_refs["turn_started_at_utc"] = _utcnow()
    await _persist_span(
        session=session,
        span_ctx=turn.active_span_refs["turn"],
        operation_name="agent.turn",
        start_time=turn.active_span_refs["turn_started_at_utc"],
        status=TelemetryStatus.RUNNING.value,
        parent_span_id=getattr(session.session_span, "span_id", None),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        attributes={
            "turn.id": turn.turn_id,
            "turn.number": turn.turn_number,
        },
    )
async def pre_llm_call(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    prompt_tokens = int(kwargs.get("prompt_tokens", 0) or 0)
    projected_total_tokens = int(kwargs.get("projected_total_tokens", 0) or 0)
    telemetry.context.token_count = prompt_tokens
    telemetry.context.token_usage = projected_total_tokens
    turn.active_span_refs["llm_started_at"] = time.perf_counter()
    turn.active_span_refs["llm_started_at_utc"] = _utcnow()
    llm_runtime_span = get_monitor_pipeline().on_llm_call_start(
        session_id=session.session_id,
        turn=turn.turn_number,
        turn_id=turn.turn_id,
    )
    turn.active_span_refs["llm_runtime"] = llm_runtime_span
    turn.active_span_refs["llm"] = llm_runtime_span or _db_only_span(session.trace_id)
    await _persist_span(
        session=session,
        span_ctx=turn.active_span_refs["llm"],
        operation_name="llm.call",
        start_time=turn.active_span_refs["llm_started_at_utc"],
        status=TelemetryStatus.RUNNING.value,
        parent_span_id=getattr(turn.active_span_refs.get("turn"), "span_id", None),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        attributes={
            "turn.id": turn.turn_id,
            "llm.prompt_tokens": prompt_tokens,
        },
    )


async def post_llm_call(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    usage = dict(kwargs.get("usage") or {})
    started_at = turn.active_span_refs.pop("llm_started_at", None)
    llm_span = turn.active_span_refs.pop("llm", None)
    llm_runtime_span = turn.active_span_refs.pop("llm_runtime", None)
    total_ms = max(0, int((time.perf_counter() - started_at) * 1000)) if started_at else 0

    telemetry.llm.prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    telemetry.llm.completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    telemetry.llm.total_ms = total_ms
    telemetry.llm.status = TelemetryStatus.SUCCESS
    session_telemetry = _ensure_session_telemetry(session)

    get_monitor_pipeline().on_context_assembly(
        session_id=session.session_id,
        token_count=telemetry.context.token_count,
        turn_id=turn.turn_id,
    )
    get_monitor_pipeline().on_llm_call_end(
        span_ctx=llm_runtime_span,
        session_id=session.session_id,
        model=getattr(session.runtime.agent_config, "resolved_model_name", "unknown"),
        prompt_tokens=telemetry.llm.prompt_tokens,
        completion_tokens=telemetry.llm.completion_tokens,
        first_token_ms=telemetry.llm.first_token_ms,
        total_ms=telemetry.llm.total_ms,
        status="ok",
    )
    await _persist_span(
        session=session,
        span_ctx=llm_span,
        operation_name="llm.call",
        start_time=turn.active_span_refs.pop("llm_started_at_utc", _utcnow()),
        end_time=_utcnow(),
        status=TelemetryStatus.SUCCESS.value,
        parent_span_id=getattr(turn.active_span_refs.get("turn"), "span_id", None),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        attributes={
            "llm.prompt_tokens": telemetry.llm.prompt_tokens,
            "llm.completion_tokens": telemetry.llm.completion_tokens,
            "llm.first_token_ms": telemetry.llm.first_token_ms,
            "llm.total_ms": telemetry.llm.total_ms,
        },
    )

    alerts = await get_monitor_alert_evaluator().evaluate_llm(
        trace_id=session.trace_id,
        session_telemetry=session_telemetry,
        turn_telemetry=telemetry,
        token_budget=session_telemetry.token_budget,
        projected_total_tokens=telemetry.context.token_usage,
    )
    if alerts:
        log.info("LLM alerts session={} turn={} count={}", session.session_id, turn.turn_id, len(alerts))


async def pre_tool_use(turn: Any, **kwargs):
    session = kwargs["session"]
    tool_call = kwargs.get("tool_call") or {}
    tool_name = str(tool_call.get("name", "") or "")
    tool_id = str(tool_call.get("id") or tool_name)
    runtime_span = get_monitor_pipeline().on_tool_call_start(
        session_id=session.session_id,
        tool_name=tool_name,
        turn_id=turn.turn_id,
    )
    turn.active_span_refs.setdefault("tool", {})[tool_id] = {
        "tool_name": tool_name,
        "started_at": time.perf_counter(),
        "started_at_utc": _utcnow(),
        "runtime_span": runtime_span,
        "span": runtime_span or _db_only_span(session.trace_id),
    }
    tool_state = turn.active_span_refs["tool"][tool_id]
    await _persist_span(
        session=session,
        span_ctx=tool_state.get("span"),
        operation_name=f"tool.{tool_name}",
        start_time=tool_state["started_at_utc"],
        status=TelemetryStatus.RUNNING.value,
        parent_span_id=getattr(turn.active_span_refs.get("turn"), "span_id", None),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        attributes={
            "tool.name": tool_name,
        },
    )


async def post_tool_use(turn: Any, **kwargs):
    session = kwargs["session"]
    tool_call = kwargs.get("tool_call") or {}
    result = kwargs.get("result")
    tool_id = str(tool_call.get("id") or tool_call.get("name") or "")
    tool_state = turn.active_span_refs.setdefault("tool", {}).pop(tool_id, {})
    tool_name = str(tool_state.get("tool_name") or tool_call.get("name") or "unknown")
    started_at = tool_state.get("started_at")
    latency_ms = max(0, int((time.perf_counter() - started_at) * 1000)) if started_at else 0
    status = _tool_status(result)

    telemetry = _ensure_turn_telemetry(turn, session)
    telemetry.tool.append(
        TurnToolMetrics(
            tool_name=tool_name,
            latency_ms=latency_ms,
            status=status,
            call_count=1,
        )
    )

    get_monitor_pipeline().on_tool_call_end(
        span_ctx=tool_state.get("runtime_span"),
        session_id=session.session_id,
        tool_name=tool_name,
        latency_ms=latency_ms,
        status="error" if status == TelemetryStatus.ERROR else "ok",
    )
    await _persist_span(
        session=session,
        span_ctx=tool_state.get("span"),
        operation_name=f"tool.{tool_name}",
        start_time=tool_state.get("started_at_utc", _utcnow()),
        end_time=_utcnow(),
        status=status.value,
        parent_span_id=getattr(turn.active_span_refs.get("turn"), "span_id", None),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        attributes={
            "tool.name": tool_name,
            "tool.latency_ms": latency_ms,
        },
    )

    if tool_name in MEMORY_TOOL_NAMES:
        hits = _memory_hits(result)
        telemetry.memory.hits += hits
        telemetry.memory.latency_ms += latency_ms
        telemetry.memory.source = tool_name
        get_monitor_pipeline().on_memory_retrieval(
            session_id=session.session_id,
            hits=hits,
            latency_ms=latency_ms,
            turn_id=turn.turn_id,
            source=tool_name,
        )

    alerts = await get_monitor_alert_evaluator().evaluate_tool(
        trace_id=session.trace_id,
        session_telemetry=_ensure_session_telemetry(session),
        turn_telemetry=telemetry,
        tool_name=tool_name,
        tool_status=status.value,
    )
    if alerts:
        log.info("Tool alerts session={} turn={} tool={} count={}", session.session_id, turn.turn_id, tool_name, len(alerts))


async def on_turn_end(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    telemetry.ended_at = _utcnow()
    finished_at = float(turn.finished_at or time.time())
    started_at = float(turn.started_at or finished_at)
    telemetry.duration_ms = max(0, int((finished_at - started_at) * 1000))
    telemetry.status = _session_status(turn.state)

    get_monitor_pipeline().on_turn_end(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        status="error" if telemetry.status == TelemetryStatus.ERROR else "ok",
        duration_ms=telemetry.duration_ms,
        prompt_tokens=telemetry.llm.prompt_tokens,
        completion_tokens=telemetry.llm.completion_tokens,
        tool_call_count=len(telemetry.tool),
        memory_hits=telemetry.memory.hits,
    )
    await _persist_span(
        session=session,
        span_ctx=turn.active_span_refs.get("turn"),
        operation_name="agent.turn",
        start_time=turn.active_span_refs.pop("turn_started_at_utc", telemetry.started_at or _utcnow()),
        end_time=telemetry.ended_at,
        status=telemetry.status.value,
        parent_span_id=getattr(session.session_span, "span_id", None),
        turn_id=turn.turn_id,
        turn_number=turn.turn_number,
        attributes={
            "turn.id": turn.turn_id,
            "turn.duration_ms": telemetry.duration_ms,
            "turn.prompt_tokens": telemetry.llm.prompt_tokens,
            "turn.completion_tokens": telemetry.llm.completion_tokens,
            "turn.tool_call_count": len(telemetry.tool),
            "turn.memory_hits": telemetry.memory.hits,
        },
    )
    await get_monitor_pipeline().persist_turn_metrics(telemetry)


async def on_error(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    telemetry.status = TelemetryStatus.ERROR
    error = kwargs.get("error")
    get_monitor_pipeline().on_error(
        session_id=session.session_id,
        span_ctx=turn.active_span_refs.get("turn"),
        exception=error if isinstance(error, Exception) else RuntimeError(str(error)),
        error_type=type(error).__name__ if isinstance(error, Exception) else "RuntimeError",
    )


async def on_session_end(session: Any, **kwargs):
    telemetry = _ensure_session_telemetry(session)
    telemetry.ended_at = _utcnow()
    telemetry.status = _session_status(session.state)
    telemetry.end_reason = str(kwargs.get("end_reason", "completed") or "completed")

    await get_monitor_pipeline().persist_session_metrics(telemetry)

    get_monitor_pipeline().on_session_end(
        session_id=session.session_id,
        root_span=session.session_span,
        end_reason=telemetry.end_reason,
        total_turns=telemetry.total_turns,
        total_tokens=telemetry.total_prompt_tokens + telemetry.total_completion_tokens,
        status="error" if telemetry.status == TelemetryStatus.ERROR else "ok",
    )
    await _persist_span(
        session=session,
        span_ctx=session.session_span,
        operation_name="agent.session",
        start_time=getattr(session, "session_span_started_at", telemetry.started_at or _utcnow()),
        end_time=telemetry.ended_at,
        status=telemetry.status.value,
        attributes={
            "session.end_reason": telemetry.end_reason,
            "session.total_turns": telemetry.total_turns,
            "session.total_tokens": telemetry.total_prompt_tokens + telemetry.total_completion_tokens,
        },
    )
