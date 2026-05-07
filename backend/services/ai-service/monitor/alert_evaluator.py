from __future__ import annotations

import json
from datetime import datetime
from threading import Lock
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.utils.config import load_config
from infra.mysql.session import session_maker
from infra.redis.redis_client import redis_client
from monitor.telemetry_schema import AlertLevel, MonitorAlertRecord, SessionTelemetry, TelemetryStatus, TurnTelemetry
from shared.config.log_config import log


_ALERT_EVALUATOR_SINGLETON: "MonitorAlertEvaluator | None" = None
_ALERT_EVALUATOR_LOCK = Lock()


class MonitorAlertEvaluator:
    def __init__(
        self,
        redis: Redis | None = None,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._redis = redis or redis_client
        self._db_session_factory = db_session_factory or session_maker
        self._redis_key_prefix = "monitor:alerts"
        self._window_ttl_seconds = 24 * 60 * 60

    async def evaluate_turn_start(
        self,
        *,
        trace_id: str,
        session_telemetry: SessionTelemetry,
        turn_telemetry: TurnTelemetry,
    ) -> list[MonitorAlertRecord]:
        rule = load_config().monitor.alerts.sessionMaxTurns
        if not rule.enabled:
            return []

        triggered = (
            session_telemetry.status == TelemetryStatus.RUNNING
            and turn_telemetry.turn_number > int(rule.thresholdTurns)
        )
        return await self._sync_alert_state(
            rule_name="sessionMaxTurns",
            level=AlertLevel(rule.level),
            trace_id=trace_id,
            session_id=session_telemetry.session_id,
            turn_id=turn_telemetry.turn_id,
            message=f"Session turn count exceeded threshold: {turn_telemetry.turn_number} > {rule.thresholdTurns}",
            observed_value=turn_telemetry.turn_number,
            threshold_value=rule.thresholdTurns,
            payload={
                "turn_number": turn_telemetry.turn_number,
                "session_status": session_telemetry.status.value,
            },
            triggered=triggered,
        )

    async def evaluate_llm(
        self,
        *,
        trace_id: str,
        session_telemetry: SessionTelemetry,
        turn_telemetry: TurnTelemetry,
        token_budget: int,
        projected_total_tokens: int,
    ) -> list[MonitorAlertRecord]:
        alerts = load_config().monitor.alerts
        records: list[MonitorAlertRecord] = []

        if alerts.llmAvgLatencyLast5.enabled:
            avg_latency_ms, sample_count = await self._push_latency_window(
                session_id=session_telemetry.session_id,
                window_size=int(alerts.llmAvgLatencyLast5.windowSize),
                latency_ms=int(turn_telemetry.llm.total_ms or 0),
            )
            avg_latency_seconds = avg_latency_ms / 1000 if sample_count else 0.0
            records.extend(
                await self._sync_alert_state(
                    rule_name="llmAvgLatencyLast5",
                    level=AlertLevel(alerts.llmAvgLatencyLast5.level),
                    trace_id=trace_id,
                    session_id=session_telemetry.session_id,
                    turn_id=turn_telemetry.turn_id,
                    message=(
                        f"Average LLM latency over last {alerts.llmAvgLatencyLast5.windowSize} calls exceeded threshold: "
                        f"{avg_latency_seconds:.2f}s >= {alerts.llmAvgLatencyLast5.thresholdSeconds}s"
                    ),
                    observed_value=round(avg_latency_seconds, 3),
                    threshold_value=alerts.llmAvgLatencyLast5.thresholdSeconds,
                    payload={
                        "window_size": int(alerts.llmAvgLatencyLast5.windowSize),
                        "sample_count": sample_count,
                        "avg_latency_ms": avg_latency_ms,
                    },
                    triggered=(
                        sample_count >= int(alerts.llmAvgLatencyLast5.windowSize)
                        and avg_latency_seconds >= float(alerts.llmAvgLatencyLast5.thresholdSeconds)
                    ),
                )
            )

        if alerts.llmSingleTimeout.enabled:
            llm_total_seconds = int(turn_telemetry.llm.total_ms or 0) / 1000
            records.extend(
                await self._sync_alert_state(
                    rule_name="llmSingleTimeout",
                    level=AlertLevel(alerts.llmSingleTimeout.level),
                    trace_id=trace_id,
                    session_id=session_telemetry.session_id,
                    turn_id=turn_telemetry.turn_id,
                    message=(
                        f"Single LLM call latency exceeded threshold: {llm_total_seconds:.2f}s >= "
                        f"{alerts.llmSingleTimeout.thresholdSeconds}s"
                    ),
                    observed_value=round(llm_total_seconds, 3),
                    threshold_value=alerts.llmSingleTimeout.thresholdSeconds,
                    payload={"llm_total_ms": int(turn_telemetry.llm.total_ms or 0)},
                    triggered=llm_total_seconds >= float(alerts.llmSingleTimeout.thresholdSeconds),
                )
            )

        if alerts.tokenQuotaUsage.enabled and token_budget > 0:
            usage_ratio = projected_total_tokens / token_budget
            records.extend(
                await self._sync_alert_state(
                    rule_name="tokenQuotaUsage",
                    level=AlertLevel(alerts.tokenQuotaUsage.level),
                    trace_id=trace_id,
                    session_id=session_telemetry.session_id,
                    turn_id=turn_telemetry.turn_id,
                    message=(
                        f"Token quota usage exceeded threshold: {usage_ratio:.3f} >= "
                        f"{alerts.tokenQuotaUsage.thresholdRatio:.3f}"
                    ),
                    observed_value=round(usage_ratio, 4),
                    threshold_value=alerts.tokenQuotaUsage.thresholdRatio,
                    payload={
                        "projected_total_tokens": projected_total_tokens,
                        "token_budget": token_budget,
                    },
                    triggered=usage_ratio >= float(alerts.tokenQuotaUsage.thresholdRatio),
                )
            )

        return records

    async def evaluate_tool(
        self,
        *,
        trace_id: str,
        session_telemetry: SessionTelemetry,
        turn_telemetry: TurnTelemetry,
        tool_name: str,
        tool_status: str,
    ) -> list[MonitorAlertRecord]:
        rule = load_config().monitor.alerts.toolConsecutiveFailures
        if not rule.enabled:
            return []

        consecutive_failures = await self._update_tool_failure_window(
            session_id=session_telemetry.session_id,
            failed=(tool_status == TelemetryStatus.ERROR.value),
        )
        return await self._sync_alert_state(
            rule_name="toolConsecutiveFailures",
            level=AlertLevel(rule.level),
            trace_id=trace_id,
            session_id=session_telemetry.session_id,
            turn_id=turn_telemetry.turn_id,
            message=(
                f"Consecutive tool failures exceeded threshold after {tool_name}: "
                f"{consecutive_failures} >= {rule.thresholdCount}"
            ),
            observed_value=consecutive_failures,
            threshold_value=rule.thresholdCount,
            payload={
                "tool_name": tool_name,
                "tool_status": tool_status,
                "turn_id": turn_telemetry.turn_id,
            },
            triggered=consecutive_failures >= int(rule.thresholdCount),
        )

    async def finalize_session(
        self,
        *,
        session_id: str,
        turn_id: str,
        rule_names: list[str] | None = None,
    ) -> list[MonitorAlertRecord]:
        target_rules = rule_names or [
            "sessionMaxTurns",
            "llmAvgLatencyLast5",
            "llmSingleTimeout",
            "tokenQuotaUsage",
            "toolConsecutiveFailures",
        ]
        resolved: list[MonitorAlertRecord] = []
        for rule_name in target_rules:
            resolved_record = await self._resolve_open_alert(
                rule_name=rule_name,
                session_id=session_id,
                turn_id=turn_id,
            )
            if resolved_record is not None:
                resolved.append(resolved_record)
        return resolved

    async def _push_latency_window(self, *, session_id: str, window_size: int, latency_ms: int) -> tuple[float, int]:
        storage_enabled = bool(load_config().monitor.storage.useRedisWindows)
        if not storage_enabled:
            return float(latency_ms), 1 if latency_ms else 0

        key = self._redis_key("llm_latency", session_id)
        try:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.lpush(key, int(latency_ms))
            pipeline.ltrim(key, 0, max(window_size - 1, 0))
            pipeline.expire(key, self._window_ttl_seconds)
            pipeline.lrange(key, 0, max(window_size - 1, 0))
            result = await pipeline.execute()
            window_values = [float(value) for value in result[-1]] if result and result[-1] else []
        except Exception as exc:
            log.warning("Failed to update LLM latency window for session {}: {}", session_id, exc)
            return float(latency_ms), 1 if latency_ms else 0

        if not window_values:
            return 0.0, 0
        return sum(window_values) / len(window_values), len(window_values)

    async def _update_tool_failure_window(self, *, session_id: str, failed: bool) -> int:
        storage_enabled = bool(load_config().monitor.storage.useRedisWindows)
        if not storage_enabled:
            return 1 if failed else 0

        key = self._redis_key("tool_failures", session_id)
        try:
            if failed:
                value = await self._redis.incr(key)
                await self._redis.expire(key, self._window_ttl_seconds)
                return int(value or 0)
            await self._redis.delete(key)
            return 0
        except Exception as exc:
            log.warning("Failed to update tool failure counter for session {}: {}", session_id, exc)
            return 1 if failed else 0

    async def _sync_alert_state(
        self,
        *,
        rule_name: str,
        level: AlertLevel,
        trace_id: str,
        session_id: str,
        turn_id: str,
        message: str,
        observed_value: float | int | str,
        threshold_value: float | int | str,
        payload: dict[str, Any],
        triggered: bool,
    ) -> list[MonitorAlertRecord]:
        if triggered:
            created = await self._create_open_alert(
                rule_name=rule_name,
                level=level,
                trace_id=trace_id,
                session_id=session_id,
                turn_id=turn_id,
                message=message,
                observed_value=observed_value,
                threshold_value=threshold_value,
                payload=payload,
            )
            return [created] if created is not None else []

        resolved = await self._resolve_open_alert(rule_name=rule_name, session_id=session_id, turn_id=turn_id)
        return [resolved] if resolved is not None else []

    async def _create_open_alert(
        self,
        *,
        rule_name: str,
        level: AlertLevel,
        trace_id: str,
        session_id: str,
        turn_id: str,
        message: str,
        observed_value: float | int | str,
        threshold_value: float | int | str,
        payload: dict[str, Any],
    ) -> MonitorAlertRecord | None:
        existing = await self._fetch_open_alert(rule_name=rule_name, session_id=session_id)
        if existing is not None:
            return None

        record = MonitorAlertRecord(
            rule_name=rule_name,
            level=level,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn_id,
            message=message,
            observed_value=observed_value,
            threshold_value=threshold_value,
            triggered_at=datetime.utcnow(),
            payload=payload,
        )

        try:
            async with self._db_session_factory() as session:
                await session.execute(
                    text(
                        """
                        INSERT INTO monitor_alerts
                        (rule_name, level, trace_id, session_id, turn_id, status, message, observed_value, threshold_value, triggered_at, payload)
                        VALUES
                        (:rule_name, :level, :trace_id, :session_id, :turn_id, :status, :message, :observed_value, :threshold_value, :triggered_at, :payload)
                        """
                    ),
                    {
                        "rule_name": record.rule_name,
                        "level": record.level.value,
                        "trace_id": record.trace_id,
                        "session_id": record.session_id,
                        "turn_id": record.turn_id,
                        "status": record.status,
                        "message": record.message,
                        "observed_value": "" if record.observed_value is None else str(record.observed_value),
                        "threshold_value": "" if record.threshold_value is None else str(record.threshold_value),
                        "triggered_at": record.triggered_at,
                        "payload": json.dumps(record.payload, ensure_ascii=False),
                    },
                )
                await session.commit()
        except Exception as exc:
            log.warning("Failed to persist monitor alert {} for session {}: {}", rule_name, session_id, exc)
            return None

        return record

    async def _resolve_open_alert(
        self,
        *,
        rule_name: str,
        session_id: str,
        turn_id: str,
    ) -> MonitorAlertRecord | None:
        existing = await self._fetch_open_alert(rule_name=rule_name, session_id=session_id)
        if existing is None:
            return None

        resolved_at = datetime.utcnow()
        try:
            async with self._db_session_factory() as session:
                await session.execute(
                    text(
                        """
                        UPDATE monitor_alerts
                        SET status = :status, resolved_at = :resolved_at, turn_id = :turn_id
                        WHERE id = :id
                        """
                    ),
                    {
                        "status": "resolved",
                        "resolved_at": resolved_at,
                        "turn_id": turn_id,
                        "id": existing["id"],
                    },
                )
                await session.commit()
        except Exception as exc:
            log.warning("Failed to resolve monitor alert {} for session {}: {}", rule_name, session_id, exc)
            return None

        return MonitorAlertRecord(
            rule_name=rule_name,
            level=AlertLevel(existing["level"]),
            trace_id=str(existing["trace_id"] or ""),
            session_id=session_id,
            turn_id=turn_id,
            message=str(existing["message"] or ""),
            observed_value=existing["observed_value"],
            threshold_value=existing["threshold_value"],
            triggered_at=existing["triggered_at"],
            resolved_at=resolved_at,
            status="resolved",
            payload=json.loads(existing["payload"]) if existing.get("payload") else {},
        )

    async def _fetch_open_alert(self, *, rule_name: str, session_id: str) -> dict[str, Any] | None:
        try:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT id, rule_name, level, trace_id, session_id, turn_id, message,
                               observed_value, threshold_value, triggered_at, payload
                        FROM monitor_alerts
                        WHERE rule_name = :rule_name AND session_id = :session_id AND status = 'open'
                        ORDER BY triggered_at DESC
                        LIMIT 1
                        """
                    ),
                    {"rule_name": rule_name, "session_id": session_id},
                )
                row = result.mappings().first()
        except Exception as exc:
            log.warning("Failed to query open monitor alert {} for session {}: {}", rule_name, session_id, exc)
            return None

        return dict(row) if row is not None else None

    def _redis_key(self, key_type: str, session_id: str) -> str:
        return f"{self._redis_key_prefix}:{key_type}:{session_id}"


def get_monitor_alert_evaluator() -> MonitorAlertEvaluator:
    global _ALERT_EVALUATOR_SINGLETON
    if _ALERT_EVALUATOR_SINGLETON is not None:
        return _ALERT_EVALUATOR_SINGLETON

    with _ALERT_EVALUATOR_LOCK:
        if _ALERT_EVALUATOR_SINGLETON is None:
            _ALERT_EVALUATOR_SINGLETON = MonitorAlertEvaluator()
    return _ALERT_EVALUATOR_SINGLETON