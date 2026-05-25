from __future__ import annotations

import asyncio
from datetime import datetime
from threading import Lock
from typing import Any

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from monitor.orm_models import MonitorAlertModel, SessionMetricsModel, SpanModel, TurnMetricsModel
from monitor.telemetry_schema import MonitorAlertRecord, SessionTelemetry, SpanRecord
from shared.config.log_config import log


_MONITOR_STORE_SINGLETON: "MonitorStore | None" = None
_MONITOR_STORE_LOCK = Lock()


class MonitorStore:
    """ORM-backed persistence for monitor data."""

    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._db = db_session_factory or session_maker
        self._max_retries = 2
        self._retry_backoff = 0.2
        self._lock = Lock()
        self._state: dict[str, Any] = {
            "degraded": False,
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_retries": 0,
            "last_action": "",
            "last_error": "",
            "last_error_at": None,
            "last_success_at": None,
        }

    # ------------------------------------------------------------------
    # spans
    # ------------------------------------------------------------------

    async def insert_spans(self, records: list[SpanRecord]) -> bool:
        if not records:
            return True
        rows = [
            {
                "app_id": r.app_id or "main",
                "user_id": r.user_id or "",
                "trace_id": r.trace_id,
                "span_id": r.span_id,
                "parent_span_id": r.parent_span_id,
                "session_id": r.session_id,
                "request_id": r.request_id or "",
                "step_counter": r.step_counter or 0,
                "operation_type": r.operation_type,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "duration_ms": r.duration_ms,
                "status": r.status or "running",
                "attributes": r.attributes or {},
            }
            for r in records
        ]

        async def op(session: AsyncSession) -> None:
            await session.execute(mysql_insert(SpanModel), rows)

        return await self._run(op, action=f"insert_spans count={len(records)}")

    # ------------------------------------------------------------------
    # turn_metrics
    # ------------------------------------------------------------------

    async def replace_turn_metrics(self, metrics: dict[str, Any]) -> bool:
        """Delete + insert one turn metrics row (idempotent)."""
        async def op(session: AsyncSession) -> None:
            await session.execute(
                TurnMetricsModel.__table__.delete().where(
                    TurnMetricsModel.session_id == metrics["session_id"],
                    TurnMetricsModel.turn_id == metrics["turn_id"],
                )
            )
            await session.execute(
                mysql_insert(TurnMetricsModel),
                {
                    "trace_id": metrics["trace_id"],
                    "session_id": metrics["session_id"],
                    "request_id": metrics.get("request_id") or "",
                    "turn_id": metrics["turn_id"],
                    "turn_number": metrics["turn_number"],
                    "status": metrics["status"],
                    "prompt_tokens": metrics.get("prompt_tokens"),
                    "completion_tokens": metrics.get("completion_tokens"),
                    "llm_latency_ms": metrics.get("llm_latency_ms"),
                    "first_token_ms": metrics.get("first_token_ms"),
                    "llm_recovery_count": metrics.get("llm_recovery_count", 0),
                    "llm_recovery_kind": metrics.get("llm_recovery_kind", ""),
                    "tool_calls_count": metrics.get("tool_calls_count", 0),
                    "tool_calls_detail": metrics.get("tool_calls_detail") or [],
                    "memory_hits": metrics.get("memory_hits", 0),
                    "memory_retrieval_ms": metrics.get("memory_retrieval_ms"),
                    "context_tokens": metrics.get("context_tokens"),
                    "context_token_usage": metrics.get("context_token_usage"),
                    "error_count": metrics.get("error_count", 0),
                    "started_at": metrics.get("started_at") or datetime.utcnow(),
                    "ended_at": metrics.get("ended_at"),
                    "duration_ms": metrics.get("duration_ms", 0),
                    "created_at": datetime.utcnow(),
                },
            )

        return await self._run(op, action=f"replace_turn_metrics {metrics.get('turn_id')}")

    # ------------------------------------------------------------------
    # session_metrics
    # ------------------------------------------------------------------

    async def upsert_session_metrics(self, telemetry: SessionTelemetry) -> bool:
        min_ms = (
            0
            if telemetry.min_llm_latency_ms in (0, 999999)
            else telemetry.min_llm_latency_ms
        )
        avg_memory_hits = (
            float(telemetry.total_memory_hits) / float(telemetry.total_turns)
            if telemetry.total_turns > 0
            else 0.0
        )
        row = {
            "session_id": telemetry.session_id,
            "trace_id": telemetry.trace_id,
            "app_id": telemetry.app_id or "main",
            "user_id": telemetry.user_id or "",
            "model": telemetry.model or "unknown",
            "status": telemetry.status.value,
            "total_turns": telemetry.total_turns,
            "total_prompt_tokens": telemetry.total_prompt_tokens,
            "total_completion_tokens": telemetry.total_completion_tokens,
            "token_budget": telemetry.token_budget,
            "sum_llm_latency_ms": telemetry.sum_llm_latency_ms,
            "sum_first_token_ms": telemetry.sum_first_token_ms,
            "max_llm_latency_ms": telemetry.max_llm_latency_ms,
            "min_llm_latency_ms": min_ms,
            "total_tool_calls": telemetry.total_tool_calls,
            "total_errors": telemetry.total_errors,
            "recovery_count": telemetry.recovery_count,
            "last_recovery_kind": telemetry.last_recovery_kind,
            "avg_memory_hits": avg_memory_hits,
            "total_memory_hits": telemetry.total_memory_hits,
            "end_reason": telemetry.end_reason or "",
            "started_at": telemetry.started_at or datetime.utcnow(),
            "ended_at": telemetry.ended_at,
            "duration_ms": self._duration_ms(telemetry.started_at, telemetry.ended_at),
            "updated_at": datetime.utcnow(),
        }
        stmt = mysql_insert(SessionMetricsModel).values(**row)
        update_cols = {c: stmt.inserted[c] for c in row if c != "session_id"}
        stmt = stmt.on_duplicate_key_update(**update_cols)

        async def op(session: AsyncSession) -> None:
            await session.execute(stmt)

        return await self._run(op, action=f"upsert_session_metrics {telemetry.session_id}")

    # ------------------------------------------------------------------
    # monitor_alerts
    # ------------------------------------------------------------------

    async def upsert_alert(self, record: MonitorAlertRecord) -> bool:
        row = {
            "rule_name": record.rule_name,
            "level": record.level.value if hasattr(record.level, "value") else str(record.level),
            "trace_id": record.trace_id or "",
            "session_id": record.session_id,
            "turn_id": record.turn_id or "",
            "status": record.status or "open",
            "message": record.message or "",
            "observed_value": str(record.observed_value) if record.observed_value is not None else "",
            "threshold_value": str(record.threshold_value) if record.threshold_value is not None else "",
            "triggered_at": record.triggered_at or datetime.utcnow(),
            "resolved_at": record.resolved_at,
            "payload": record.payload or {},
        }
        stmt = mysql_insert(MonitorAlertModel).values(**row)
        stmt = stmt.on_duplicate_key_update(
            status=stmt.inserted.status,
            resolved_at=stmt.inserted.resolved_at,
            message=stmt.inserted.message,
            observed_value=stmt.inserted.observed_value,
        )

        async def op(session: AsyncSession) -> None:
            await session.execute(stmt)

        return await self._run(op, action=f"upsert_alert {record.rule_name}/{record.session_id}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self, operation, *, action: str) -> bool:
        total = self._max_retries + 1
        for attempt in range(1, total + 1):
            async with self._db() as session:
                try:
                    await operation(session)
                    await session.commit()
                    self._mark_success(action=action, attempts=attempt)
                    return True
                except Exception as exc:
                    await session.rollback()
                    self._mark_failure(action=action, exc=exc, exhausted=(attempt == total))
                    if attempt < total:
                        log.warning("MonitorStore retry {}/{} '{}': {}", attempt, total, action, exc)
                        await asyncio.sleep(self._retry_backoff * attempt)
                        continue
                    log.warning("MonitorStore failed '{}' after {} attempts: {}", action, total, exc)
                    return False
        return False

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _mark_success(self, *, action: str, attempts: int) -> None:
        with self._lock:
            self._state["degraded"] = False
            self._state["consecutive_failures"] = 0
            self._state["last_action"] = action
            self._state["last_success_at"] = datetime.utcnow()
            if attempts > 1:
                self._state["total_retries"] += attempts - 1

    def _mark_failure(self, *, action: str, exc: Exception, exhausted: bool) -> None:
        with self._lock:
            self._state["total_failures"] += 1
            self._state["last_action"] = action
            self._state["last_error"] = str(exc)
            self._state["last_error_at"] = datetime.utcnow()
            if exhausted:
                self._state["degraded"] = True
                self._state["consecutive_failures"] += 1

    @staticmethod
    def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> int:
        if started_at is None or ended_at is None:
            return 0
        return max(0, int((ended_at - started_at).total_seconds() * 1000))


def get_monitor_store() -> MonitorStore:
    global _MONITOR_STORE_SINGLETON
    if _MONITOR_STORE_SINGLETON is not None:
        return _MONITOR_STORE_SINGLETON

    with _MONITOR_STORE_LOCK:
        if _MONITOR_STORE_SINGLETON is None:
            _MONITOR_STORE_SINGLETON = MonitorStore()
    return _MONITOR_STORE_SINGLETON