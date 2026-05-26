from __future__ import annotations

import secrets
import time
from datetime import datetime
from threading import Lock
from typing import Any

from agent.runtime_schema import RuntimeSessionState, ActivateTurn
from bot.agent.agent_schema import AgentState
from monitor.alert_evaluator import get_monitor_alert_evaluator
from monitor.metric_collector import MetricCollector
from monitor.monitor_store import MonitorStore, get_monitor_store
from monitor.prometheus_metrics import (
    record_context_metrics,
    record_error,
    record_llm_call,
    record_memory_hits,
    record_session_end,
    record_session_start,
    record_tool_call,
    record_turn_end,
)
from monitor.span_collector import SpanCollector
from monitor.telemetry_schema import (
    MonitorAlertRecord,
    OperationType,
    SessionTelemetry,
    SpanRecord,
    TelemetryStatus,
    TurnTelemetry,
)
from shared.config.log_config import log

MEMORY_TOOL_NAMES = {
    "memory_search",
    "memory_get",
    "write_short_term",
    "write_long_term",
    "write_identity_memory",
}

_PIPELINE_SINGLETON: "MonitorPipeline | None" = None
_PIPELINE_LOCK = Lock()


def _new_span_id() -> str:
    return secrets.token_hex(8)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _session_status(state: Any) -> AgentState:
    if isinstance(state, AgentState):
        return state
    normalized = str(getattr(state, "value", state) or "").lower()
    for agent_state in AgentState:
        if agent_state.value == normalized:
            return agent_state
    return AgentState.RUNNING


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


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


class MonitorPipeline:
    """
    Facade that coordinates SpanCollector, SessionTelemetry, and MonitorStore.
    Owns all monitoring logic; handlers simply delegate to these methods.
    """

    def __init__(self) -> None:
        self.store: MonitorStore = get_monitor_store()
        self._span_collectors: dict[str, SpanCollector] = {}
        self._metric_collectors: dict[str, MetricCollector] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_session_start(self, session: RuntimeSessionState) -> None:
        """
        new session record
        new sessin tele
        """
        span_collector = SpanCollector()
        self._span_collectors[session.session_id] = span_collector
        # self._span_collectors.get(session.session_id) = span_collector

        session_span = SpanRecord.new_session_span_recorder(session)
        span_collector.add(session_span)
        session.root_span_id = session_span.span_id
        session.root_span_started_at = session.started_at

        metric_collector = MetricCollector()
        self._metric_collectors[session.session_id] = metric_collector
        telemetry = SessionTelemetry.new_tel(session)
        metric_collector.add_session(telemetry)
        session.telemetry = telemetry
        record_session_start(telemetry)

    async def on_session_end(self, session: RuntimeSessionState, **kwargs) -> None:
        """
        update session tele
        update session record
        """
        telemetry = session.telemetry
        if telemetry is None:
            return
        telemetry.ended_at = _utcnow()
        telemetry.status = _session_status(session.state)
        telemetry.end_reason = str(kwargs.get("end_reason", "completed") or "completed")

        # collector = self._span_collectors.get(session.session_id)
        collector = self._span_collectors.get(session.session_id)
        root_span_id = getattr(session, "root_span_id", None)
        if root_span_id and collector:
            collector.update_end(
                root_span_id,
                end_time=telemetry.ended_at,
                duration_ms=_duration_ms(
                    getattr(session, "root_span_started_at", None) or telemetry.started_at or _utcnow(),
                    telemetry.ended_at,
                ),
                status="error" if telemetry.status == AgentState.FAILED else "ok",
                attributes={
                    "session.end_reason": telemetry.end_reason,
                    "session.total_turns": telemetry.turn_number,
                },
            )

        if collector:
            await self.store.insert_spans(collector.get_all())
            collector.clear()
        await self.store.upsert_session_metrics(telemetry)
        record_session_end(telemetry)
        self._span_collectors.pop(session.session_id, None)
        self._metric_collectors.pop(session.session_id, None)

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    async def on_turn_start(self, session: RuntimeSessionState, turn: ActivateTurn) -> None:
        """
        new turn tele
        new turn span

        """
        if session.telemetry is None:
            return

        turn_telemetry = TurnTelemetry.new_tel(session.telemetry)
        turn_telemetry.turn_number = turn.step_counter
        turn_telemetry.turn_id = session.request_id
        turn_telemetry.request_id = session.request_id

        metric_collector = self._metric_collectors.get(session.session_id)
        if metric_collector:
            metric_collector.add_turn(turn_telemetry)
        turn.telemetry = turn_telemetry

        turn_span_id = _new_span_id()
        turn.active_span_refs = {
            "turn_span_id": turn_span_id,
            "turn_started_at": _utcnow(),
        }

        collector = self._span_collectors.get(session.session_id)
        if collector:
            collector.add(SpanRecord(
                span_id=turn_span_id,
                parent_span_id=getattr(session, "root_span_id", None) or None,
                trace_id=session.trace_id,
                session_id=session.session_id,
                app_id=session.app_id,
                user_id=session.user_id,
                request_id=session.request_id,
                step_counter=turn.step_counter,
                operation_type=OperationType.TURN.value,
                start_time=turn.active_span_refs["turn_started_at"],
                status="running",
                attributes={"turn.id": session.request_id, "turn.number": turn.step_counter},
            ))

        if session.telemetry:
            alerts = await get_monitor_alert_evaluator().evaluate_turn_start(
                trace_id=session.trace_id,
                session_telemetry=session.telemetry,
                turn_telemetry=turn.telemetry,
            )
            if alerts:
                log.info("TurnStart alerts session={} turn={} count={}", session.session_id, session.request_id, len(alerts))

    async def on_turn_end(self, session: RuntimeSessionState, turn: ActivateTurn) -> None:
        telemetry = turn.telemetry
        if telemetry is None:
            return
        telemetry.ended_at = _utcnow()
        finished_at = float(turn.finished_at or time.time())
        started_at_ts = float(turn.started_at or finished_at)
        telemetry.duration_ms = max(0, int((finished_at - started_at_ts) * 1000))
        telemetry.status = _session_status(turn.state)

        turn_span_id = turn.active_span_refs.get("turn_span_id")
        collector = self._span_collectors.get(session.session_id)
        if turn_span_id and collector:
            collector.update_end(
                turn_span_id,
                end_time=telemetry.ended_at,
                duration_ms=telemetry.duration_ms,
                status="error" if telemetry.status == AgentState.FAILED else "ok",
                attributes={
                    "turn.duration_ms": telemetry.duration_ms,
                    "turn.prompt_tokens": telemetry.total_prompt_tokens,
                    "turn.completion_tokens": telemetry.total_completion_tokens,
                    "turn.tool_calls": telemetry.total_tool_calls,
                    "turn.memory_hits": telemetry.total_memory_hits,
                },
            )

        if collector:
            metrics = collector.derive_turn_metrics(telemetry)
            await self.store.replace_turn_metrics(metrics)
        if session.telemetry:
            session.telemetry.record_turn(telemetry)

        record_turn_end(session.telemetry, telemetry)

    def on_error(self, session: RuntimeSessionState, turn: ActivateTurn) -> None:
        if turn.telemetry is None:
            return
        turn.telemetry.status = AgentState.FAILED
        record_error(session.telemetry, turn.telemetry, scope="turn", error_type="pipeline_error")

    # ------------------------------------------------------------------
    # LLM lifecycle
    # ------------------------------------------------------------------

    def pre_llm_call(self, session: RuntimeSessionState, turn: ActivateTurn, **kwargs) -> None:
        """
        new llm record
        update turn tele

        """
        telemetry = turn.telemetry
        prompt_tokens = int(kwargs.get("prompt_tokens", 0) or 0)
        projected_total_tokens = int(kwargs.get("projected_total_tokens", 0) or 0)
        telemetry.token_count = prompt_tokens
        telemetry.token_usage = projected_total_tokens

        metric_collector = self._metric_collectors.get(session.session_id)
        if metric_collector:
            metric_collector.update_turn(telemetry)

        record_context_metrics(session.telemetry, telemetry)

        llm_span_id = _new_span_id()
        turn.active_span_refs["llm_span_id"] = llm_span_id
        turn.active_span_refs["llm_started_at"] = time.perf_counter()
        turn.active_span_refs["llm_started_at_utc"] = _utcnow()

        collector = self._span_collectors.get(session.session_id)
        if collector:
            collector.add(SpanRecord(
                span_id=llm_span_id,
                parent_span_id=turn.active_span_refs.get("turn_span_id"),
                trace_id=session.trace_id,
                session_id=session.session_id,
                app_id=session.app_id,
                user_id=session.user_id,
                request_id=session.request_id,
                step_counter=turn.step_counter,
                operation_type=OperationType.LLM.value,
                start_time=turn.active_span_refs["llm_started_at_utc"],
                status="running",
                attributes={"llm.prompt_tokens": prompt_tokens},
            ))

    async def post_llm_call(self, session: RuntimeSessionState, turn: ActivateTurn, **kwargs) -> None:
        """
        update turn tele
        update llm span record

        """
        telemetry = turn.telemetry
        usage = dict(kwargs.get("usage") or {})
        started_perf = turn.active_span_refs.pop("llm_started_at", None)
        turn.active_span_refs.pop("llm_started_at_utc", None)
        llm_span_id = turn.active_span_refs.pop("llm_span_id", None)
        total_ms = max(0, int((time.perf_counter() - started_perf) * 1000)) if started_perf else 0
        ended_utc = _utcnow()

        telemetry.total_prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        telemetry.total_completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        telemetry.total_tokens = telemetry.total_prompt_tokens + telemetry.total_completion_tokens
        first_token_ms = int(usage.get("first_token_ms", 0) or 0)

        metric_collector = self._metric_collectors.get(session.session_id)
        if metric_collector:
            metric_collector.update_turn(telemetry)

        collector = self._span_collectors.get(session.session_id)
        if llm_span_id and collector:
            collector.update_end(
                llm_span_id,
                end_time=ended_utc,
                duration_ms=total_ms,
                status="ok",
                attributes={
                    "llm.prompt_tokens": telemetry.total_prompt_tokens,
                    "llm.completion_tokens": telemetry.total_completion_tokens,
                    "llm.first_token_ms": first_token_ms,
                    "llm.total_ms": total_ms,
                },
            )

        record_llm_call(session.telemetry, telemetry, is_error=False, total_ms=total_ms, first_token_ms=first_token_ms)

        if session.telemetry:
            alerts = await get_monitor_alert_evaluator().evaluate_llm(
                trace_id=session.trace_id,
                session_telemetry=session.telemetry,
                turn_telemetry=telemetry,
                token_budget=session.telemetry.token_budget,
                projected_total_tokens=telemetry.token_usage,
                llm_total_ms=total_ms,
            )
            if alerts:
                log.info("LLM alerts session={} turn={} count={}", session.session_id, session.request_id, len(alerts))

    # ------------------------------------------------------------------
    # Tool lifecycle
    # ------------------------------------------------------------------

    def pre_tool_use(self, session: RuntimeSessionState, turn: ActivateTurn, tool_call: dict) -> None:
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

        collector = self._span_collectors.get(session.session_id)
        if collector:
            collector.add(SpanRecord(
                span_id=tool_span_id,
                parent_span_id=turn.active_span_refs.get("turn_span_id"),
                trace_id=session.trace_id,
                session_id=session.session_id,
                app_id=session.app_id,
                user_id=session.user_id,
                request_id=session.request_id,
                step_counter=turn.step_counter,
                operation_type=f"{OperationType.TOOL.value}.{tool_name}",
                start_time=started_utc,
                status="running",
                attributes={"tool.name": tool_name},
            ))

    async def post_tool_use(
        self,
        session: RuntimeSessionState,
        turn: ActivateTurn,
        tool_call: dict,
        result: Any,
    ) -> None:
        tool_id = str(tool_call.get("id") or tool_call.get("name") or "")
        tool_state = turn.active_span_refs.setdefault("tool", {}).pop(tool_id, {})
        tool_name = str(tool_state.get("tool_name") or tool_call.get("name") or "unknown")
        started_perf = tool_state.get("started_at")
        tool_span_id = tool_state.get("tool_span_id")
        latency_ms = max(0, int((time.perf_counter() - started_perf) * 1000)) if started_perf else 0
        status = _tool_status(result)
        ended_utc = _utcnow()

        telemetry = turn.telemetry
        telemetry.total_tool_calls += 1
        if status == TelemetryStatus.ERROR:
            telemetry.total_tool_call_errors += 1

        collector = self._span_collectors.get(session.session_id)
        if tool_span_id and collector:
            collector.update_end(
                tool_span_id,
                end_time=ended_utc,
                duration_ms=latency_ms,
                status="error" if status == TelemetryStatus.ERROR else "ok",
                attributes={"tool.name": tool_name, "tool.latency_ms": latency_ms},
            )

        if tool_name in MEMORY_TOOL_NAMES:
            hits = _memory_hits(result)
            telemetry.total_memory_hits += hits
            record_memory_hits(session.telemetry, telemetry, hits)

        record_tool_call(
            session.telemetry, telemetry,
            tool_name=tool_name,
            is_error=(status == TelemetryStatus.ERROR),
            latency_ms=latency_ms,
        )

        if session.telemetry:
            alerts = await get_monitor_alert_evaluator().evaluate_tool(
                trace_id=session.trace_id,
                session_telemetry=session.telemetry,
                turn_telemetry=telemetry,
                tool_name=tool_name,
                tool_status=status.value,
            )
            if alerts:
                log.info("Tool alerts session={} turn={} tool={} count={}", session.session_id, session.request_id, tool_name, len(alerts))

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    async def persist_alert(self, record: MonitorAlertRecord) -> None:
        await self.store.upsert_alert(record)


def get_monitor_pipeline() -> MonitorPipeline:
    global _PIPELINE_SINGLETON
    if _PIPELINE_SINGLETON is not None:
        return _PIPELINE_SINGLETON
    with _PIPELINE_LOCK:
        if _PIPELINE_SINGLETON is None:
            _PIPELINE_SINGLETON = MonitorPipeline()
    return _PIPELINE_SINGLETON
