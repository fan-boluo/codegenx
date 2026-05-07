from __future__ import annotations

import asyncio
import json
from datetime import datetime
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from monitor.telemetry_schema import SessionTelemetry, TelemetryStatus, TurnTelemetry
from shared.config.log_config import log


_MONITOR_STORE_SINGLETON: "MonitorStore | None" = None
_MONITOR_STORE_LOCK = Lock()


class MonitorStore:
    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._db_session_factory = db_session_factory or session_maker
        self._max_retries = 2
        self._retry_backoff_seconds = 0.2
        self._runtime_lock = Lock()
        self._runtime_state: dict[str, Any] = {
            "degraded": False,
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_retries": 0,
            "last_action": "",
            "last_error": "",
            "last_error_at": None,
            "last_success_at": None,
        }

    async def upsert_session_metrics(
        self,
        session_telemetry: SessionTelemetry,
        *,
        model: str,
        token_budget: int,
        sum_llm_latency_ms: int,
        sum_first_token_ms: int,
        max_llm_latency_ms: int,
        min_llm_latency_ms: int,
        total_errors: int,
        last_recovery_kind: str,
        end_reason: str,
    ) -> bool:
        normalized_min_latency = 0 if min_llm_latency_ms in (0, 999999) else min_llm_latency_ms
        avg_memory_hits = (
            float(session_telemetry.total_memory_hits) / float(session_telemetry.total_turns)
            if session_telemetry.total_turns > 0
            else 0.0
        )

        payload = {
            "session_id": session_telemetry.session_id,
            "trace_id": session_telemetry.trace_id,
            "app_id": session_telemetry.app_id,
            "user_id": session_telemetry.user_id,
            "model": model,
            "status": session_telemetry.status.value,
            "total_turns": session_telemetry.total_turns,
            "total_prompt_tokens": session_telemetry.total_prompt_tokens,
            "total_completion_tokens": session_telemetry.total_completion_tokens,
            "token_budget": token_budget,
            "sum_llm_latency_ms": sum_llm_latency_ms,
            "sum_first_token_ms": sum_first_token_ms,
            "max_llm_latency_ms": max_llm_latency_ms,
            "min_llm_latency_ms": normalized_min_latency,
            "total_tool_calls": session_telemetry.total_tool_calls,
            "total_errors": total_errors,
            "recovery_count": session_telemetry.recovery_count,
            "last_recovery_kind": last_recovery_kind,
            "avg_memory_hits": avg_memory_hits,
            "total_memory_hits": session_telemetry.total_memory_hits,
            "end_reason": end_reason,
            "started_at": session_telemetry.started_at or datetime.utcnow(),
            "ended_at": session_telemetry.ended_at,
            "duration_ms": self._duration_ms(session_telemetry.started_at, session_telemetry.ended_at),
        }

        statement = text(
            """
            INSERT INTO session_metrics (
                session_id, trace_id, app_id, user_id, model, status, total_turns,
                total_prompt_tokens, total_completion_tokens, token_budget, sum_llm_latency_ms,
                sum_first_token_ms, max_llm_latency_ms, min_llm_latency_ms, total_tool_calls,
                total_errors, recovery_count, last_recovery_kind, avg_memory_hits,
                total_memory_hits, end_reason, started_at, ended_at, duration_ms
            ) VALUES (
                :session_id, :trace_id, :app_id, :user_id, :model, :status, :total_turns,
                :total_prompt_tokens, :total_completion_tokens, :token_budget, :sum_llm_latency_ms,
                :sum_first_token_ms, :max_llm_latency_ms, :min_llm_latency_ms, :total_tool_calls,
                :total_errors, :recovery_count, :last_recovery_kind, :avg_memory_hits,
                :total_memory_hits, :end_reason, :started_at, :ended_at, :duration_ms
            )
            ON DUPLICATE KEY UPDATE
                trace_id = VALUES(trace_id),
                app_id = VALUES(app_id),
                user_id = VALUES(user_id),
                model = VALUES(model),
                status = VALUES(status),
                total_turns = VALUES(total_turns),
                total_prompt_tokens = VALUES(total_prompt_tokens),
                total_completion_tokens = VALUES(total_completion_tokens),
                token_budget = VALUES(token_budget),
                sum_llm_latency_ms = VALUES(sum_llm_latency_ms),
                sum_first_token_ms = VALUES(sum_first_token_ms),
                max_llm_latency_ms = VALUES(max_llm_latency_ms),
                min_llm_latency_ms = VALUES(min_llm_latency_ms),
                total_tool_calls = VALUES(total_tool_calls),
                total_errors = VALUES(total_errors),
                recovery_count = VALUES(recovery_count),
                last_recovery_kind = VALUES(last_recovery_kind),
                avg_memory_hits = VALUES(avg_memory_hits),
                total_memory_hits = VALUES(total_memory_hits),
                end_reason = VALUES(end_reason),
                started_at = VALUES(started_at),
                ended_at = VALUES(ended_at),
                duration_ms = VALUES(duration_ms)
            """
        )
        return await self._execute(statement, payload, action=f"upsert session_metrics {session_telemetry.session_id}")

    async def replace_turn_metrics(self, turn_telemetry: TurnTelemetry) -> bool:
        tool_calls_detail = [
            {
                "name": item.tool_name,
                "latencyMs": int(item.latency_ms or 0),
                "status": item.status.value,
                "callCount": int(item.call_count or 0),
            }
            for item in turn_telemetry.tool
        ]
        payload = {
            "trace_id": turn_telemetry.trace_id,
            "session_id": turn_telemetry.session_id,
            "turn_id": turn_telemetry.turn_id,
            "turn_number": turn_telemetry.turn_number,
            "status": turn_telemetry.status.value,
            "prompt_tokens": turn_telemetry.llm.prompt_tokens,
            "completion_tokens": turn_telemetry.llm.completion_tokens,
            "llm_latency_ms": turn_telemetry.llm.total_ms,
            "first_token_ms": turn_telemetry.llm.first_token_ms,
            "llm_recovery_count": turn_telemetry.llm.recovery_count,
            "llm_recovery_kind": turn_telemetry.llm.recovery_kind,
            "tool_calls_count": len(turn_telemetry.tool),
            "tool_calls_detail": json.dumps(tool_calls_detail, ensure_ascii=False),
            "memory_hits": turn_telemetry.memory.hits,
            "memory_retrieval_ms": turn_telemetry.memory.latency_ms,
            "context_tokens": turn_telemetry.context.token_count,
            "context_token_usage": turn_telemetry.context.token_usage,
            "error_count": 1 if turn_telemetry.status == TelemetryStatus.ERROR else 0,
            "started_at": turn_telemetry.started_at or datetime.utcnow(),
            "ended_at": turn_telemetry.ended_at,
            "duration_ms": turn_telemetry.duration_ms,
        }

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                text("DELETE FROM turn_metrics WHERE session_id = :session_id AND turn_id = :turn_id"),
                {"session_id": turn_telemetry.session_id, "turn_id": turn_telemetry.turn_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO turn_metrics (
                        trace_id, session_id, turn_id, turn_number, status, prompt_tokens,
                        completion_tokens, llm_latency_ms, first_token_ms, llm_recovery_count,
                        llm_recovery_kind, tool_calls_count, tool_calls_detail, memory_hits,
                        memory_retrieval_ms, context_tokens, context_token_usage, error_count,
                        started_at, ended_at, duration_ms
                    ) VALUES (
                        :trace_id, :session_id, :turn_id, :turn_number, :status, :prompt_tokens,
                        :completion_tokens, :llm_latency_ms, :first_token_ms, :llm_recovery_count,
                        :llm_recovery_kind, :tool_calls_count, CAST(:tool_calls_detail AS JSON), :memory_hits,
                        :memory_retrieval_ms, :context_tokens, :context_token_usage, :error_count,
                        :started_at, :ended_at, :duration_ms
                    )
                    """
                ),
                payload,
            )

        return await self._execute_in_session(
            operation,
            action=f"replace turn_metrics {turn_telemetry.session_id}/{turn_telemetry.turn_id}",
        )

    async def replace_span(
        self,
        *,
        app_id: str,
        user_id: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        session_id: str,
        turn_id: str,
        turn_number: int,
        operation_name: str,
        start_time: datetime,
        end_time: datetime | None,
        duration_ms: int | None,
        status: str,
        attributes: dict[str, Any] | None = None,
    ) -> bool:
        payload = {
            "trace_id": trace_id,
            "span_id": span_id,
            "app_id": app_id or "main",
            "user_id": user_id or "",
            "parent_span_id": parent_span_id,
            "session_id": session_id,
            "turn_id": turn_id or "",
            "turn_number": int(turn_number or 0),
            "operation_name": operation_name,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": int(duration_ms or 0) if end_time is not None else None,
            "status": status,
            "attributes": json.dumps(attributes or {}, ensure_ascii=False),
        }

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                text("DELETE FROM spans WHERE trace_id = :trace_id AND span_id = :span_id"),
                {"trace_id": trace_id, "span_id": span_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO spans (
                        app_id, user_id, trace_id, span_id, parent_span_id, session_id,
                        turn_id, turn_number, operation_name, start_time, end_time,
                        duration_ms, status, attributes
                    ) VALUES (
                        :app_id, :user_id, :trace_id, :span_id, :parent_span_id, :session_id,
                        :turn_id, :turn_number, :operation_name, :start_time, :end_time,
                        :duration_ms, :status, CAST(:attributes AS JSON)
                    )
                    """
                ),
                payload,
            )

        return await self._execute_in_session(
            operation,
            action=f"replace spans {session_id}/{span_id}",
        )

    async def _execute(self, statement, payload: dict[str, Any], *, action: str) -> bool:
        async def operation(session: AsyncSession) -> None:
            await session.execute(statement, payload)

        return await self._execute_in_session(operation, action=action)

    async def _execute_in_session(self, operation, *, action: str) -> bool:
        total_attempts = self._max_retries + 1
        for attempt in range(1, total_attempts + 1):
            async with self._db_session_factory() as session:
                try:
                    await operation(session)
                    await session.commit()
                    self._mark_success(action=action, attempts=attempt)
                    return True
                except Exception as exc:
                    await session.rollback()
                    self._mark_failure(action=action, exc=exc, exhausted=(attempt == total_attempts))
                    if attempt < total_attempts:
                        log.warning("Monitor store retry {} attempt {}/{} failed: {}", action, attempt, total_attempts, exc)
                        await asyncio.sleep(self._retry_backoff_seconds * attempt)
                        continue
                    log.warning("Monitor store failed to {} after {} attempts: {}", action, total_attempts, exc)
                    return False
        return False

    def get_runtime_status(self) -> dict[str, Any]:
        with self._runtime_lock:
            return dict(self._runtime_state)

    def _mark_success(self, *, action: str, attempts: int) -> None:
        now = datetime.utcnow()
        with self._runtime_lock:
            self._runtime_state["degraded"] = False
            self._runtime_state["consecutive_failures"] = 0
            self._runtime_state["last_action"] = action
            self._runtime_state["last_success_at"] = now
            if attempts > 1:
                self._runtime_state["total_retries"] += attempts - 1

    def _mark_failure(self, *, action: str, exc: Exception, exhausted: bool) -> None:
        now = datetime.utcnow()
        with self._runtime_lock:
            self._runtime_state["total_failures"] += 1
            self._runtime_state["last_action"] = action
            self._runtime_state["last_error"] = str(exc)
            self._runtime_state["last_error_at"] = now
            if exhausted:
                self._runtime_state["degraded"] = True
                self._runtime_state["consecutive_failures"] += 1

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