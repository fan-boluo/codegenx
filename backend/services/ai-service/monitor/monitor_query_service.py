from __future__ import annotations

import json
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from shared.config.log_config import log
from shared.constants import DEFAULT_PAGE_NUM, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from shared.schema.common import PageData
from shared.schema.monitor import (
    MonitorAlertQueryRequest,
    MonitorAlertRecordVO,
    MonitorOverviewStats,
    MonitorRuleCount,
    MonitorSessionDetail,
    MonitorSessionQueryRequest,
    MonitorSessionSummary,
    MonitorStatusCount,
    MonitorToolCallDetail,
    MonitorTurnSummary,
)
from utils.config import load_config


_MONITOR_QUERY_SERVICE_SINGLETON: "MonitorQueryService | None" = None
_MONITOR_QUERY_SERVICE_LOCK = Lock()


class MonitorQueryService:
    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._db_session_factory = db_session_factory or session_maker

    async def get_overview(self) -> MonitorOverviewStats:
        async with self._db_session_factory() as session:
            sessions_row = (
                await session.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS total_sessions,
                            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_sessions,
                            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_sessions,
                            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_sessions
                        FROM session_metrics
                        """
                    )
                )
            ).mappings().one()
            turns_row = (
                await session.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS total_turns,
                            COALESCE(AVG(duration_ms), 0) AS avg_turn_duration_ms,
                            COALESCE(AVG(llm_latency_ms), 0) AS avg_llm_latency_ms,
                            COALESCE(AVG(first_token_ms), 0) AS avg_first_token_ms,
                            COALESCE(AVG(token_count), 0) AS avg_context_tokens,
                            COALESCE(AVG(token_usage), 0) AS avg_context_token_usage,
                            COALESCE(SUM(tool_calls_count), 0) AS total_tool_calls,
                            COALESCE(SUM(memory_hits), 0) AS total_memory_hits
                        FROM turn_metrics
                        """
                    )
                )
            ).mappings().one()
            open_alerts = (
                await session.execute(
                    text("SELECT COUNT(*) FROM monitor_alerts WHERE status = 'open'")
                )
            ).scalar_one()
            status_rows = (
                await session.execute(
                    text("SELECT status, COUNT(*) AS count FROM session_metrics GROUP BY status ORDER BY count DESC")
                )
            ).mappings().all()
            alert_rows = (
                await session.execute(
                    text("SELECT rule_name, COUNT(*) AS count FROM monitor_alerts GROUP BY rule_name ORDER BY count DESC")
                )
            ).mappings().all()

        return MonitorOverviewStats(
            totalSessions=int(sessions_row["total_sessions"] or 0),
            runningSessions=int(sessions_row["running_sessions"] or 0),
            successSessions=int(sessions_row["success_sessions"] or 0),
            errorSessions=int(sessions_row["error_sessions"] or 0),
            totalTurns=int(turns_row["total_turns"] or 0),
            avgTurnDurationMs=float(turns_row["avg_turn_duration_ms"] or 0),
            avgLlmLatencyMs=float(turns_row["avg_llm_latency_ms"] or 0),
            avgFirstTokenMs=float(turns_row["avg_first_token_ms"] or 0),
            avgContextTokens=float(turns_row["avg_context_tokens"] or 0),
            avgContextTokenUsage=float(turns_row["avg_context_token_usage"] or 0),
            totalToolCalls=int(turns_row["total_tool_calls"] or 0),
            totalMemoryHits=int(turns_row["total_memory_hits"] or 0),
            openAlerts=int(open_alerts or 0),
            statusBreakdown=[MonitorStatusCount(status=row["status"], count=int(row["count"] or 0)) for row in status_rows],
            alertBreakdown=[MonitorRuleCount(ruleName=row["rule_name"], count=int(row["count"] or 0)) for row in alert_rows],
        )

    async def list_sessions(self, query: MonitorSessionQueryRequest) -> PageData[MonitorSessionSummary]:
        page_num, page_size = self._normalize_page(query.page_num, query.page_size)
        where_sql, params = self._build_session_filters(query)
        offset = (page_num - 1) * page_size

        async with self._db_session_factory() as session:
            total_row = int(
                (
                    await session.execute(
                        text(f"SELECT COUNT(*) FROM session_metrics {where_sql}"),
                        params,
                    )
                ).scalar_one()
                or 0
            )
            rows = (
                await session.execute(
                    text(
                        f"""
                        SELECT session_id, trace_id, app_id, user_id, model, status, turn_number,
                               total_prompt_tokens, total_completion_tokens, total_tokens,
                               max_duration_ms, min_duration_ms, total_tool_calls,
                               total_tool_call_errors, recovery_count,
                               last_recovery_kind, total_memory_hits, end_reason,
                               started_at, ended_at, duration_ms, updated_at
                        FROM session_metrics
                        {where_sql}
                        ORDER BY updated_at DESC, started_at DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {**params, "limit": page_size, "offset": offset},
                )
            ).mappings().all()

        records = [self._map_session_row(dict(row)) for row in rows]
        total_page = (total_row + page_size - 1) // page_size if total_row else 0
        return PageData[MonitorSessionSummary](
            records=records,
            pageNumber=page_num,
            pageSize=page_size,
            totalPage=total_page,
            totalRow=total_row,
        )

    async def get_session_detail(self, session_id: str) -> MonitorSessionDetail | None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None

        async with self._db_session_factory() as session:
            session_row = (
                await session.execute(
                    text(
                        """
                        SELECT session_id, trace_id, app_id, user_id, model, status, turn_number,
                               total_prompt_tokens, total_completion_tokens, total_tokens,
                               max_duration_ms, min_duration_ms, total_tool_calls,
                               total_tool_call_errors, recovery_count,
                               last_recovery_kind, total_memory_hits, end_reason,
                               started_at, ended_at, duration_ms, updated_at
                        FROM session_metrics
                        WHERE session_id = :session_id
                        """
                    ),
                    {"session_id": normalized_session_id},
                )
            ).mappings().first()
            if session_row is None:
                return None

            turn_rows = (
                await session.execute(
                    text(
                        """
                        SELECT trace_id, session_id, request_id, turn_id, turn_number, status, prompt_tokens,
                               completion_tokens, llm_latency_ms, first_token_ms, recovery_count,
                               last_recovery_kind, tool_calls_count, memory_hits,
                               token_count, token_usage, total_tool_call_errors,
                               started_at, ended_at, duration_ms, created_at
                        FROM turn_metrics
                        WHERE session_id = :session_id
                        ORDER BY turn_number DESC, created_at DESC
                        LIMIT 100
                        """
                    ),
                    {"session_id": normalized_session_id},
                )
            ).mappings().all()
            alert_rows = (
                await session.execute(
                    text(
                        """
                        SELECT id, rule_name, level, trace_id, session_id, turn_id, status, message,
                               observed_value, threshold_value, triggered_at, resolved_at, payload
                        FROM monitor_alerts
                        WHERE session_id = :session_id
                        ORDER BY triggered_at DESC, id DESC
                        LIMIT 100
                        """
                    ),
                    {"session_id": normalized_session_id},
                )
            ).mappings().all()

        return MonitorSessionDetail(
            session=self._map_session_row(dict(session_row)),
            turns=[self._map_turn_row(dict(row)) for row in turn_rows],
            alerts=[self._map_alert_row(dict(row)) for row in alert_rows],
        )

    async def get_turn_detail(self, session_id: str, turn_id: str) -> MonitorTurnSummary | None:
        async with self._db_session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT trace_id, session_id, request_id, turn_id, turn_number, status, prompt_tokens,
                               completion_tokens, llm_latency_ms, first_token_ms, recovery_count,
                               last_recovery_kind, tool_calls_count, memory_hits,
                               token_count, token_usage, total_tool_call_errors,
                               started_at, ended_at, duration_ms, created_at
                        FROM turn_metrics
                        WHERE session_id = :session_id AND turn_id = :turn_id
                        LIMIT 1
                        """
                    ),
                    {"session_id": session_id, "turn_id": turn_id},
                )
            ).mappings().first()
        return self._map_turn_row(dict(row)) if row is not None else None

    async def list_alerts(self, query: MonitorAlertQueryRequest) -> PageData[MonitorAlertRecordVO]:
        page_num, page_size = self._normalize_page(query.page_num, query.page_size)
        where_sql, params = self._build_alert_filters(query)
        offset = (page_num - 1) * page_size

        async with self._db_session_factory() as session:
            total_row = int(
                (
                    await session.execute(
                        text(f"SELECT COUNT(*) FROM monitor_alerts {where_sql}"),
                        params,
                    )
                ).scalar_one()
                or 0
            )
            rows = (
                await session.execute(
                    text(
                        f"""
                        SELECT id, rule_name, level, trace_id, session_id, turn_id, status, message,
                               observed_value, threshold_value, triggered_at, resolved_at, payload
                        FROM monitor_alerts
                        {where_sql}
                        ORDER BY triggered_at DESC, id DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {**params, "limit": page_size, "offset": offset},
                )
            ).mappings().all()

        records = [self._map_alert_row(dict(row)) for row in rows]
        total_page = (total_row + page_size - 1) // page_size if total_row else 0
        return PageData[MonitorAlertRecordVO](
            records=records,
            pageNumber=page_num,
            pageSize=page_size,
            totalPage=total_page,
            totalRow=total_row,
        )

    async def get_monitor_config(self) -> dict[str, Any]:
        return load_config().monitor.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _normalize_page(page_num: int, page_size: int) -> tuple[int, int]:
        normalized_page_num = max(int(page_num or DEFAULT_PAGE_NUM), 1)
        normalized_page_size = min(max(int(page_size or DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
        return normalized_page_num, normalized_page_size

    @staticmethod
    def _build_session_filters(query: MonitorSessionQueryRequest) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if query.status:
            clauses.append("status = :status")
            params["status"] = query.status
        if query.app_id:
            clauses.append("app_id = :app_id")
            params["app_id"] = query.app_id
        if query.user_id:
            clauses.append("user_id = :user_id")
            params["user_id"] = query.user_id
        if query.session_id:
            clauses.append("session_id LIKE :session_id")
            params["session_id"] = f"%{query.session_id}%"
        if query.trace_id:
            clauses.append("trace_id LIKE :trace_id")
            params["trace_id"] = f"%{query.trace_id}%"
        return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)

    @staticmethod
    def _build_alert_filters(query: MonitorAlertQueryRequest) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if query.status:
            clauses.append("status = :status")
            params["status"] = query.status
        if query.level:
            clauses.append("level = :level")
            params["level"] = query.level
        if query.rule_name:
            clauses.append("rule_name = :rule_name")
            params["rule_name"] = query.rule_name
        if query.session_id:
            clauses.append("session_id LIKE :session_id")
            params["session_id"] = f"%{query.session_id}%"
        return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)

    @staticmethod
    def _map_session_row(row: dict[str, Any]) -> MonitorSessionSummary:
        total_turns = int(row.get("turn_number") or 0)
        return MonitorSessionSummary(
            sessionId=str(row.get("session_id") or ""),
            traceId=str(row.get("trace_id") or ""),
            appId=str(row.get("app_id") or "main"),
            userId=str(row.get("user_id") or ""),
            model=str(row.get("model") or ""),
            status=str(row.get("status") or "running"),
            totalTurns=total_turns,
            totalPromptTokens=int(row.get("total_prompt_tokens") or 0),
            totalCompletionTokens=int(row.get("total_completion_tokens") or 0),
            tokenBudget=0,
            avgLlmLatencyMs=0.0,
            avgFirstTokenMs=0.0,
            maxLlmLatencyMs=int(row.get("max_duration_ms") or 0),
            minLlmLatencyMs=int(row.get("min_duration_ms") or 0),
            totalToolCalls=int(row.get("total_tool_calls") or 0),
            totalErrors=int(row.get("total_tool_call_errors") or 0),
            recoveryCount=int(row.get("recovery_count") or 0),
            lastRecoveryKind=str(row.get("last_recovery_kind") or ""),
            avgMemoryHits=0.0,
            totalMemoryHits=int(row.get("total_memory_hits") or 0),
            endReason=str(row.get("end_reason") or ""),
            startedAt=row.get("started_at"),
            endedAt=row.get("ended_at"),
            durationMs=int(row.get("duration_ms") or 0),
            updatedAt=row.get("updated_at"),
        )

    @staticmethod
    def _map_turn_row(row: dict[str, Any]) -> MonitorTurnSummary:
        return MonitorTurnSummary(
            traceId=str(row.get("trace_id") or ""),
            sessionId=str(row.get("session_id") or ""),
            requestId=str(row.get("request_id") or ""),
            turnId=str(row.get("turn_id") or ""),
            turnNumber=int(row.get("turn_number") or 0),
            status=str(row.get("status") or "running"),
            promptTokens=int(row.get("prompt_tokens") or 0),
            completionTokens=int(row.get("completion_tokens") or 0),
            llmLatencyMs=int(row.get("llm_latency_ms") or 0),
            firstTokenMs=int(row.get("first_token_ms") or 0),
            llmRecoveryCount=int(row.get("recovery_count") or 0),
            llmRecoveryKind=str(row.get("last_recovery_kind") or ""),
            toolCallsCount=int(row.get("tool_calls_count") or 0),
            memoryHits=int(row.get("memory_hits") or 0),
            contextTokens=int(row.get("token_count") or 0),
            contextTokenUsage=int(row.get("token_usage") or 0),
            errorCount=int(row.get("total_tool_call_errors") or 0),
            startedAt=row.get("started_at"),
            endedAt=row.get("ended_at"),
            durationMs=int(row.get("duration_ms") or 0),
            createdAt=row.get("created_at"),
        )

    @staticmethod
    def _map_alert_row(row: dict[str, Any]) -> MonitorAlertRecordVO:
        payload = row.get("payload")
        normalized_payload: dict[str, Any] = {}
        if isinstance(payload, str) and payload:
            try:
                normalized_payload = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("Failed to parse monitor alert payload for row {}", row.get("id"))
        elif isinstance(payload, dict):
            normalized_payload = payload

        return MonitorAlertRecordVO(
            id=row.get("id"),
            ruleName=str(row.get("rule_name") or ""),
            level=str(row.get("level") or "WARN"),
            traceId=str(row.get("trace_id") or ""),
            sessionId=str(row.get("session_id") or ""),
            turnId=str(row.get("turn_id") or ""),
            status=str(row.get("status") or "open"),
            message=str(row.get("message") or ""),
            observedValue=str(row.get("observed_value") or ""),
            thresholdValue=str(row.get("threshold_value") or ""),
            triggeredAt=row.get("triggered_at"),
            resolvedAt=row.get("resolved_at"),
            payload=normalized_payload,
        )

    @staticmethod
    def _parse_tool_calls_detail(raw_value: Any) -> list[MonitorToolCallDetail]:
        if raw_value in (None, ""):
            return []
        parsed: Any = raw_value
        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                return []
        if not isinstance(parsed, list):
            return []
        return [
            MonitorToolCallDetail.model_validate(item)
            for item in parsed
            if isinstance(item, dict)
        ]


def get_monitor_query_service() -> MonitorQueryService:
    global _MONITOR_QUERY_SERVICE_SINGLETON
    if _MONITOR_QUERY_SERVICE_SINGLETON is not None:
        return _MONITOR_QUERY_SERVICE_SINGLETON

    with _MONITOR_QUERY_SERVICE_LOCK:
        if _MONITOR_QUERY_SERVICE_SINGLETON is None:
            _MONITOR_QUERY_SERVICE_SINGLETON = MonitorQueryService()
    return _MONITOR_QUERY_SERVICE_SINGLETON
