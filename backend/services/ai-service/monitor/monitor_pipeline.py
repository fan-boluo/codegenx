from __future__ import annotations

import secrets
import time
from datetime import datetime
from threading import Lock
from typing import Any

from agent.runtime_schema import RuntimeSessionState, ActivateTurn
from bot.agent.agent_schema import AgentState
from compact import AUTOCOMPACT_THRESHOLD
from monitor.alert_evaluator import get_alert_streak_tracker
from monitor.metric_collector import MetricCollector
from monitor.monitor_store import MonitorStore, get_monitor_store
from monitor.prometheus_metrics import (
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
    OperationType,
    SessionTelemetry,
    SpanRecord,
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


def _tool_status(result: Any) -> AgentState:
    if isinstance(result, dict):
        if result.get("error"):
            return AgentState.ERROR
        if result.get("success") is False:
            return AgentState.ERROR
    return AgentState.SUCCESS


def _memory_hits(result: Any) -> int:
    if isinstance(result, dict):
        details = result.get("details")
        if isinstance(details, dict) and isinstance(details.get("results"), list):
            return len(details["results"])
        data = result.get("data")
        if isinstance(data, list):
            return len(data)
    return 0


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
    # Telemetry lookup helpers
    # ------------------------------------------------------------------

    def _get_session_telemetry(self, session_id: str) -> SessionTelemetry | None:
        mc = self._metric_collectors.get(session_id)
        return mc.get_session_telemetry() if mc else None

    def _get_turn_telemetry(self, session_id: str, span_id: str) -> TurnTelemetry | None:
        mc = self._metric_collectors.get(session_id)
        return mc.get_turn_telemetry(span_id) if mc else None

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
        metric_collector.set_session_telemetry(telemetry)
        record_session_start(telemetry)

    async def on_session_end(self, session: RuntimeSessionState, **kwargs) -> None:
        """
        update session tele
        update session record
        """
        telemetry = self._get_session_telemetry(session.session_id)
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
        get_alert_streak_tracker().cleanup_session(session.session_id)
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
        session_telemetry = self._get_session_telemetry(session.session_id)
        if session_telemetry is None:
            return

        turn_telemetry = TurnTelemetry.new_tel(session_telemetry)
        turn_telemetry.turn_number = turn.step_counter
        turn_telemetry.turn_id = session.request_id

        turn_span_id = _new_span_id()
        turn_started_utc = _utcnow()
        turn.add_turn_span_id("turn_span_id", turn_span_id)

        metric_collector = self._metric_collectors.get(session.session_id)
        if metric_collector:
            metric_collector.add_turn_telemetry(turn_span_id, turn_telemetry)

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
                start_time=turn_started_utc,
                status="running",
                attributes={"turn.id": session.request_id, "turn.number": turn.step_counter},
            ))

    async def on_turn_end(self, session: RuntimeSessionState, turn: ActivateTurn) -> None:
        turn_span_id = turn.pop_turn_span("turn_span_id")
        telemetry = self._get_turn_telemetry(session.session_id, turn_span_id) if turn_span_id else None
        if telemetry is None:
            return
        telemetry.ended_at = _utcnow()
        finished_at = float(turn.finished_at or time.time())
        started_at_ts = float(turn.started_at or finished_at)
        telemetry.duration_ms = max(0, int((finished_at - started_at_ts) * 1000))
        telemetry.status = _session_status(turn.state)
        telemetry.llm_recovery_count = getattr(turn, "llm_recovery_count", 0) or 0
        telemetry.last_recovery_kind = getattr(turn, "last_recovery_kind", "") or ""

        collector = self._span_collectors.get(session.session_id)
        if turn_span_id and collector:
            collector.update_end(
                turn_span_id,
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
        session_telemetry = self._get_session_telemetry(session.session_id)
        if session_telemetry:
            session_telemetry.record_turn(telemetry)

        record_turn_end(session_telemetry, telemetry)

    def on_error(self, session: RuntimeSessionState, turn: ActivateTurn) -> None:
        turn_span_id = turn.active_span_refs.get("turn_span_id")
        if not turn_span_id:
            return
        turn_telemetry = self._get_turn_telemetry(session.session_id, turn_span_id)
        if turn_telemetry is None:
            return
        turn_telemetry.status = AgentState.FAILED
        session_telemetry = self._get_session_telemetry(session.session_id)
        record_error(session_telemetry, turn_telemetry, scope="turn", error_type="pipeline_error")

    # ------------------------------------------------------------------
    # LLM lifecycle
    # ------------------------------------------------------------------

    def pre_llm_call(self, session: RuntimeSessionState, turn: ActivateTurn, **kwargs) -> None:
        """
        new llm record
        update turn tele

        """
        turn_span_id = turn.active_span_refs.get("turn_span_id")
        telemetry = self._get_turn_telemetry(session.session_id, turn_span_id) if turn_span_id else None
        if telemetry is None:
            return
        prompt_tokens = int(kwargs.get("prompt_tokens", 0) or 0)
        projected_total_tokens = int(kwargs.get("projected_total_tokens", 0) or 0)
        telemetry.token_count = prompt_tokens
        telemetry.token_usage = projected_total_tokens / AUTOCOMPACT_THRESHOLD if AUTOCOMPACT_THRESHOLD > 0 else 0.0
        # 从 turn 读取上一步的压缩标记
        telemetry.context_is_compress = getattr(turn, "last_step_compacted", False)
        # 重置标记，避免跨 step 污染
        turn.last_step_compacted = False

        metric_collector = self._metric_collectors.get(session.session_id)
        if metric_collector and turn_span_id:
            metric_collector.update_turn(turn_span_id, telemetry)

        llm_span_id = _new_span_id()
        started_utc = _utcnow()
        turn.add_turn_span_id("llm_span_id", llm_span_id)

        collector = self._span_collectors.get(session.session_id)
        if collector:
            collector.add(SpanRecord(
                span_id=llm_span_id,
                parent_span_id=turn_span_id,
                trace_id=session.trace_id,
                session_id=session.session_id,
                app_id=session.app_id,
                user_id=session.user_id,
                request_id=session.request_id,
                step_counter=turn.step_counter,
                operation_type=OperationType.LLM.value,
                start_time=started_utc,
                status="running",
                attributes={"llm.prompt_tokens": prompt_tokens},
            ))

    async def post_llm_call(self, session: RuntimeSessionState, turn: ActivateTurn, **kwargs) -> None:
        """
        update turn tele
        update llm span record

        """
        turn_span_id = turn.active_span_refs.get("turn_span_id")
        telemetry = self._get_turn_telemetry(session.session_id, turn_span_id) if turn_span_id else None
        usage = dict(kwargs.get("usage") or {})
        llm_span_id = turn.pop_turn_span("llm_span_id")

        if telemetry is not None:
            telemetry.total_prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            telemetry.total_completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            telemetry.total_tokens = telemetry.total_prompt_tokens + telemetry.total_completion_tokens
        first_token_ms = int(usage.get("first_token_ms", 0) or 0)

        metric_collector = self._metric_collectors.get(session.session_id)
        if metric_collector and turn_span_id and telemetry is not None:
            metric_collector.update_turn(turn_span_id, telemetry)

        collector = self._span_collectors.get(session.session_id)
        total_ms = 0
        if llm_span_id and collector:
            span = collector.update_end(
                llm_span_id,
                status="ok",
                attributes={
                    "llm.prompt_tokens": telemetry.total_prompt_tokens if telemetry else 0,
                    "llm.completion_tokens": telemetry.total_completion_tokens if telemetry else 0,
                    "llm.first_token_ms": first_token_ms,
                    "llm.recovery_count": telemetry.llm_recovery_count if telemetry else 0,
                    "llm.recovery_kind": telemetry.last_recovery_kind if telemetry else "",
                    "llm.is_error": False,
                },
            )
            if span:
                total_ms = span.duration_ms or 0

        session_telemetry = self._get_session_telemetry(session.session_id)
        record_llm_call(session_telemetry, telemetry, is_error=False, total_ms=total_ms, first_token_ms=first_token_ms)

        if session_telemetry:
            tracker = get_alert_streak_tracker()
            model = str(getattr(telemetry, "model", "") or getattr(session_telemetry, "model", "") or "unknown")
            tracker.track_llm_outcome(
                session_id=session.session_id,
                model=model,
                recovery_count=telemetry.llm_recovery_count if telemetry else 0,
                latency_s=total_ms / 1000,
            )
            tracker.track_context_breach(
                session_id=session.session_id,
                token_usage=telemetry.token_usage if telemetry else 0,
                is_compress=telemetry.context_is_compress if telemetry else False,
            )

    # ------------------------------------------------------------------
    # Tool lifecycle
    # ------------------------------------------------------------------

    def pre_tool_use(self, session: RuntimeSessionState, turn: ActivateTurn, tool_call: dict) -> None:
        tool_name = str(tool_call.get("name", "") or "")
        tool_id = str(tool_call.get("id") or tool_name)

        tool_span_id = _new_span_id()
        started_utc = _utcnow()
        turn.active_span_refs.setdefault("tool", {})[tool_id] = tool_span_id

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
        tool_span_id = turn.active_span_refs.get("tool", {}).pop(tool_id, None)
        tool_name = str(tool_call.get("name") or "unknown")
        status = _tool_status(result)

        turn_span_id = turn.active_span_refs.get("turn_span_id")
        telemetry = self._get_turn_telemetry(session.session_id, turn_span_id) if turn_span_id else None
        if telemetry is not None:
            telemetry.total_tool_calls += 1
            if status == AgentState.ERROR:
                telemetry.total_tool_call_errors += 1

        collector = self._span_collectors.get(session.session_id)
        latency_ms = 0
        if tool_span_id and collector:
            span = collector.update_end(
                tool_span_id,
                status="error" if status == AgentState.ERROR else "ok",
                attributes={"tool.name": tool_name},
            )
            if span:
                latency_ms = span.duration_ms or 0

        if tool_name in MEMORY_TOOL_NAMES:
            hits = _memory_hits(result)
            if telemetry is not None:
                telemetry.total_memory_hits += hits
            session_telemetry = self._get_session_telemetry(session.session_id)
            record_memory_hits(session_telemetry, telemetry, hits)

        session_telemetry = self._get_session_telemetry(session.session_id)
        record_tool_call(
            session_telemetry, telemetry,
            tool_name=tool_name,
            is_error=(status == AgentState.ERROR),
            latency_ms=latency_ms,
        )

        if session_telemetry:
            tracker = get_alert_streak_tracker()
            tracker.track_tool_outcome(
                session_id=session.session_id,
                tool_name=tool_name,
                is_error=(status == AgentState.ERROR),
            )

    # ------------------------------------------------------------------
    # Alerts (delegated to Prometheus alert rules)
    # ------------------------------------------------------------------


def get_monitor_pipeline() -> MonitorPipeline:
    global _PIPELINE_SINGLETON
    if _PIPELINE_SINGLETON is not None:
        return _PIPELINE_SINGLETON
    with _PIPELINE_LOCK:
        if _PIPELINE_SINGLETON is None:
            _PIPELINE_SINGLETON = MonitorPipeline()
    return _PIPELINE_SINGLETON
