from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from monitor.health_checker import HealthChecker, get_health_checker
from monitor.monitor_query_service import MonitorQueryService, get_monitor_query_service
from shared.schema.monitor import MonitorCleanupSummary, MonitorCleanupTableResult


_MAINTENANCE_SERVICE_SINGLETON: "MonitorMaintenanceService | None" = None
_MAINTENANCE_SERVICE_LOCK = Lock()


class MonitorMaintenanceService:
    def __init__(
        self,
        *,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        health_checker: HealthChecker | None = None,
        query_service: MonitorQueryService | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory or session_maker
        self._health_checker = health_checker or get_health_checker()
        self._query_service = query_service or get_monitor_query_service()
        self._retention_targets = [
            ("spans", "start_time"),
            ("turn_metrics", "created_at"),
            ("request_metrics", "updated_at"),
            ("session_metrics", "updated_at"),
            ("monitor_alerts", "triggered_at"),
        ]

    async def cleanup_history(self, *, retention_days: int = 7, dry_run: bool = False) -> MonitorCleanupSummary:
        cutoff_at = datetime.utcnow() - timedelta(days=max(retention_days, 1))
        table_results: list[MonitorCleanupTableResult] = []
        total_affected = 0
        overall_status = "success"

        async with self._db_session_factory() as session:
            for table_name, time_column in self._retention_targets:
                try:
                    count_result = await session.execute(
                        text(f"SELECT COUNT(*) FROM {table_name} WHERE {time_column} IS NOT NULL AND {time_column} < :cutoff_at"),
                        {"cutoff_at": cutoff_at},
                    )
                    affected_rows = int(count_result.scalar() or 0)
                    if not dry_run and affected_rows > 0:
                        await session.execute(
                            text(f"DELETE FROM {table_name} WHERE {time_column} IS NOT NULL AND {time_column} < :cutoff_at"),
                            {"cutoff_at": cutoff_at},
                        )
                    total_affected += affected_rows
                    table_results.append(
                        MonitorCleanupTableResult(
                            tableName=table_name,
                            status="success",
                            affectedRows=affected_rows,
                            cutoffAt=cutoff_at,
                        )
                    )
                except Exception as exc:
                    overall_status = "partial"
                    table_results.append(
                        MonitorCleanupTableResult(
                            tableName=table_name,
                            status="error",
                            affectedRows=0,
                            cutoffAt=cutoff_at,
                            errorMessage=str(exc),
                        )
                    )

            if dry_run or overall_status == "partial":
                await session.rollback()
            else:
                await session.commit()

        return MonitorCleanupSummary(
            retentionDays=max(retention_days, 1),
            dryRun=dry_run,
            status=overall_status,
            deletedRows=total_affected,
            executedAt=datetime.utcnow(),
            tableResults=table_results,
        )

    async def render_metrics_text(self) -> str:
        overview = await self._query_service.get_overview()
        health = await self._health_checker.get_system_health()
        component_lines = []
        for component in health.components:
            status_value = 1 if component.status == "up" else 0
            degraded_value = 1 if component.status == "degraded" else 0
            component_lines.append(
                f'codegenx_monitor_component_up{{component="{component.name}"}} {status_value}'
            )
            component_lines.append(
                f'codegenx_monitor_component_degraded{{component="{component.name}"}} {degraded_value}'
            )

        lines = [
            "# HELP codegenx_monitor_sessions_total Total monitor sessions persisted in MySQL",
            "# TYPE codegenx_monitor_sessions_total gauge",
            f"codegenx_monitor_sessions_total {overview.total_sessions}",
            "# HELP codegenx_monitor_turns_total Total monitor turns persisted in MySQL",
            "# TYPE codegenx_monitor_turns_total gauge",
            f"codegenx_monitor_turns_total {overview.total_turns}",
            "# HELP codegenx_monitor_alerts_open Current number of open monitor alerts",
            "# TYPE codegenx_monitor_alerts_open gauge",
            f"codegenx_monitor_alerts_open {overview.open_alerts}",
            "# HELP codegenx_monitor_llm_latency_avg_ms Average LLM latency in milliseconds",
            "# TYPE codegenx_monitor_llm_latency_avg_ms gauge",
            f"codegenx_monitor_llm_latency_avg_ms {overview.avg_llm_latency_ms}",
            "# HELP codegenx_monitor_tool_calls_total Total tool calls across persisted sessions",
            "# TYPE codegenx_monitor_tool_calls_total gauge",
            f"codegenx_monitor_tool_calls_total {overview.total_tool_calls}",
            "# HELP codegenx_monitor_memory_hits_total Total memory hits across persisted sessions",
            "# TYPE codegenx_monitor_memory_hits_total gauge",
            f"codegenx_monitor_memory_hits_total {overview.total_memory_hits}",
            "# HELP codegenx_monitor_health_overall Monitor subsystem overall health state",
            "# TYPE codegenx_monitor_health_overall gauge",
            f"codegenx_monitor_health_overall {1 if health.overall_status == 'up' else 0}",
        ]
        lines.extend(component_lines)
        return "\n".join(lines) + "\n"


def get_monitor_maintenance_service() -> MonitorMaintenanceService:
    global _MAINTENANCE_SERVICE_SINGLETON
    if _MAINTENANCE_SERVICE_SINGLETON is not None:
        return _MAINTENANCE_SERVICE_SINGLETON

    with _MAINTENANCE_SERVICE_LOCK:
        if _MAINTENANCE_SERVICE_SINGLETON is None:
            _MAINTENANCE_SERVICE_SINGLETON = MonitorMaintenanceService()
    return _MAINTENANCE_SERVICE_SINGLETON