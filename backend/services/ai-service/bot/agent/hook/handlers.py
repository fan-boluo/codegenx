from __future__ import annotations

import secrets
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agent import runtime
from agent.context import get_context_assembler
from agent.runtime_schema import AgentState
from memory.memory_manager import get_memory_manager
from monitor.alert_evaluator import get_monitor_alert_evaluator
from monitor.monitor_pipeline import get_monitor_pipeline
from monitor.telemetry_schema import (
    OperationName,
    SessionTelemetry,
    SpanRecord,
    TelemetryStatus,
    TurnLLMMetrics,
    TurnMemoryMetrics,
    TurnTelemetry,
    TurnToolMetrics,
)
from bot.session.manager import SessionManager
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


def _new_span_id() -> str:
    return secrets.token_hex(8)


def _session_status(state: Any) -> TelemetryStatus:
    normalized = str(getattr(state, "value", state) or "").lower()
    if normalized == "failed":
        return TelemetryStatus.ERROR
    if normalized == "stopped":
        return TelemetryStatus.STOPPED
    if normalized == "completed":
        return TelemetryStatus.SUCCESS
    return TelemetryStatus.RUNNING


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


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


# ---------------------------------------------------------------------------
# Hook implementations
# ---------------------------------------------------------------------------


async def on_session_start(session: RuntimeSessionState, **kwargs):
    req = session.request
    if req is None:
        log.warning("on_session_start: request is None, skipping")
        return

    context = kwargs.get("context")
    if context is None:
        return

    session_manager = SessionManager(str(req.app_id))
    session.session_manager = session_manager

    await get_context_assembler().build_fix_context(session)

    now = _utcnow().isoformat()
    request_dict = req.model_dump()
    request_dict["started_at"] = now
    session_manager.append_chat_history_message(session.session_id, request_dict)

    session.state = AgentState.RUNNING

    # Initialize session-level monitoring state
    telemetry = _ensure_session_telemetry(session)
    telemetry.model = getattr(session.runtime.agent_config, "resolved_model_name", "unknown")
    telemetry.token_budget = 0

    pipeline = get_monitor_pipeline()
    session.span_collector = pipeline.new_span_collector()
    session.root_span_id = _new_span_id()
    session.root_span_started_at = _utcnow()

    pipeline.add_span(
        session.span_collector,
        SpanRecord(
            span_id=session.root_span_id,
            trace_id=session.trace_id,
            session_id=session.session_id,
            app_id=session.app_id,
            user_id=session.user_id,
            operation_name=OperationName.SESSION.value,
            start_time=session.root_span_started_at,
            status="running",
            attributes={
                "client.version": session.client_version,
                "model.name": telemetry.model,
            },
        ),
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

    turn_span_id = _new_span_id()
    turn.active_span_refs = {
        "turn_span_id": turn_span_id,
        "turn_started_at": _utcnow(),
    }

    get_monitor_pipeline().add_span(
        session.span_collector,
        SpanRecord(
            span_id=turn_span_id,
            parent_span_id=session.root_span_id,
            trace_id=session.trace_id,
            session_id=session.session_id,
            app_id=session.app_id,
            user_id=session.user_id,
            turn_id=turn.turn_id,
            turn_number=turn.turn_number,
            operation_name=OperationName.TURN.value,
            start_time=turn.active_span_refs["turn_started_at"],
            status="running",
            attributes={
                "turn.id": turn.turn_id,
                "turn.number": turn.turn_number,
            },
        ),
    )

    alerts = await get_monitor_alert_evaluator().evaluate_turn_start(
        trace_id=session.trace_id,
        session_telemetry=_ensure_session_telemetry(session),
        turn_telemetry=telemetry,
    )
    if alerts:
        log.info("TurnStart alerts session={} turn={} count={}", session.session_id, turn.turn_id, len(alerts))


async def pre_llm_call(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    prompt_tokens = int(kwargs.get("prompt_tokens", 0) or 0)
    projected_total_tokens = int(kwargs.get("projected_total_tokens", 0) or 0)
    telemetry.context.token_count = prompt_tokens
    telemetry.context.token_usage = projected_total_tokens

    llm_span_id = _new_span_id()
    turn.active_span_refs["llm_span_id"] = llm_span_id
    turn.active_span_refs["llm_started_at"] = time.perf_counter()
    turn.active_span_refs["llm_started_at_utc"] = _utcnow()

    get_monitor_pipeline().add_span(
        session.span_collector,
        SpanRecord(
            span_id=llm_span_id,
            parent_span_id=turn.active_span_refs.get("turn_span_id"),
            trace_id=session.trace_id,
            session_id=session.session_id,
            app_id=session.app_id,
            user_id=session.user_id,
            turn_id=turn.turn_id,
            turn_number=turn.turn_number,
            operation_name=OperationName.LLM.value,
            start_time=turn.active_span_refs["llm_started_at_utc"],
            status="running",
            attributes={"llm.prompt_tokens": prompt_tokens},
        ),
    )


async def post_llm_call(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    usage = dict(kwargs.get("usage") or {})
    started_perf = turn.active_span_refs.pop("llm_started_at", None)
    started_utc = turn.active_span_refs.pop("llm_started_at_utc", _utcnow())
    llm_span_id = turn.active_span_refs.pop("llm_span_id", None)
    total_ms = max(0, int((time.perf_counter() - started_perf) * 1000)) if started_perf else 0
    ended_utc = _utcnow()

    telemetry.llm.prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    telemetry.llm.completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    telemetry.llm.total_ms = total_ms

    pipeline = get_monitor_pipeline()
    if llm_span_id:
        pipeline.update_span(
            session.span_collector,
            llm_span_id,
            end_time=ended_utc,
            duration_ms=total_ms,
            status="ok",
            attributes={
                "llm.prompt_tokens": telemetry.llm.prompt_tokens,
                "llm.completion_tokens": telemetry.llm.completion_tokens,
                "llm.first_token_ms": telemetry.llm.first_token_ms,
                "llm.total_ms": total_ms,
            },
        )

    alerts = await get_monitor_alert_evaluator().evaluate_llm(
        trace_id=session.trace_id,
        session_telemetry=_ensure_session_telemetry(session),
        turn_telemetry=telemetry,
        token_budget=_ensure_session_telemetry(session).token_budget,
        projected_total_tokens=telemetry.context.token_usage,
    )
    if alerts:
        log.info("LLM alerts session={} turn={} count={}", session.session_id, turn.turn_id, len(alerts))


async def pre_tool_use(turn: Any, **kwargs):
    session = kwargs["session"]
    tool_call = kwargs.get("tool_call") or {}
    tool_name = str(tool_call.get("name", "") or "")
    tool_id = str(tool_call.get("id") or tool_name)

    tool_span_id = _new_span_id()
    started_utc = _utcnow()
    turn.active_span_refs.setdefault("tool", {})[tool_id] = {
        "tool_name": tool_name,
        "tool_span_id": tool_span_id,
        "started_at": time.perf_counter(),
        "started_at_utc": started_utc,
    }

    get_monitor_pipeline().add_span(
        session.span_collector,
        SpanRecord(
            span_id=tool_span_id,
            parent_span_id=turn.active_span_refs.get("turn_span_id"),
            trace_id=session.trace_id,
            session_id=session.session_id,
            app_id=session.app_id,
            user_id=session.user_id,
            turn_id=turn.turn_id,
            turn_number=turn.turn_number,
            operation_name=f"{OperationName.TOOL.value}.{tool_name}",
            start_time=started_utc,
            status="running",
            attributes={"tool.name": tool_name},
        ),
    )


async def post_tool_use(turn: Any, **kwargs):
    session = kwargs["session"]
    tool_call = kwargs.get("tool_call") or {}
    result = kwargs.get("result")
    tool_id = str(tool_call.get("id") or tool_call.get("name") or "")
    tool_state = turn.active_span_refs.setdefault("tool", {}).pop(tool_id, {})
    tool_name = str(tool_state.get("tool_name") or tool_call.get("name") or "unknown")
    started_perf = tool_state.get("started_at")
    started_utc = tool_state.get("started_at_utc", _utcnow())
    tool_span_id = tool_state.get("tool_span_id")
    latency_ms = max(0, int((time.perf_counter() - started_perf) * 1000)) if started_perf else 0
    status = _tool_status(result)
    ended_utc = _utcnow()

    telemetry = _ensure_turn_telemetry(turn, session)
    telemetry.tool.append(TurnToolMetrics(
        tool_name=tool_name,
        latency_ms=latency_ms,
        is_error=(status == TelemetryStatus.ERROR),
    ))

    pipeline = get_monitor_pipeline()
    if tool_span_id:
        pipeline.update_span(
            session.span_collector,
            tool_span_id,
            end_time=ended_utc,
            duration_ms=latency_ms,
            status="error" if status == TelemetryStatus.ERROR else "ok",
            attributes={
                "tool.name": tool_name,
                "tool.latency_ms": latency_ms,
            },
        )

    if tool_name in MEMORY_TOOL_NAMES:
        hits = _memory_hits(result)
        telemetry.memory.hits += hits
        telemetry.memory.latency_ms += latency_ms

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
    started_at_ts = float(turn.started_at or finished_at)
    telemetry.duration_ms = max(0, int((finished_at - started_at_ts) * 1000))
    telemetry.status = _session_status(turn.state)

    turn_span_id = turn.active_span_refs.get("turn_span_id")
    pipeline = get_monitor_pipeline()
    if turn_span_id:
        pipeline.update_span(
            session.span_collector,
            turn_span_id,
            end_time=telemetry.ended_at,
            duration_ms=telemetry.duration_ms,
            status="error" if telemetry.status == TelemetryStatus.ERROR else "ok",
            attributes={
                "turn.duration_ms": telemetry.duration_ms,
                "turn.prompt_tokens": telemetry.llm.prompt_tokens,
                "turn.completion_tokens": telemetry.llm.completion_tokens,
                "turn.tool_calls": len(telemetry.tool),
                "turn.memory_hits": telemetry.memory.hits,
            },
        )

    await pipeline.on_turn_end(
        collector=session.span_collector,
        session_telemetry=_ensure_session_telemetry(session),
        turn_telemetry=telemetry,
    )


async def on_error(turn: Any, **kwargs):
    session = kwargs["session"]
    telemetry = _ensure_turn_telemetry(turn, session)
    telemetry.status = TelemetryStatus.ERROR
    telemetry.llm.is_error = True


async def on_session_end(session: Any, **kwargs):
    telemetry = _ensure_session_telemetry(session)
    telemetry.ended_at = _utcnow()
    telemetry.status = _session_status(session.state)
    telemetry.end_reason = str(kwargs.get("end_reason", "completed") or "completed")

    root_span_id = getattr(session, "root_span_id", None)
    pipeline = get_monitor_pipeline()
    if root_span_id:
        pipeline.update_span(
            session.span_collector,
            root_span_id,
            end_time=telemetry.ended_at,
            duration_ms=_duration_ms(
                getattr(session, "root_span_started_at", telemetry.started_at or _utcnow()),
                telemetry.ended_at,
            ),
            status="error" if telemetry.status == TelemetryStatus.ERROR else "ok",
            attributes={
                "session.end_reason": telemetry.end_reason,
                "session.total_turns": telemetry.total_turns,
            },
        )

    await pipeline.on_session_end(
        collector=session.span_collector,
        session_telemetry=telemetry,
    )
