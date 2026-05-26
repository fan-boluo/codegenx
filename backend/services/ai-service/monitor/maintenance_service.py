from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from monitor.monitor_query_service import MonitorQueryService, get_monitor_query_service
from shared.schema.monitor import MonitorCleanupSummary, MonitorCleanupTableResult


_MAINTENANCE_SERVICE_SINGLETON: "MonitorMaintenanceService | None" = None
_MAINTENANCE_SERVICE_LOCK = Lock()


class MonitorMaintenanceService:
    def __init__(
        self,
        *,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        query_service: MonitorQueryService | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory or session_maker
        self._query_service = query_service or get_monitor_query_service()
        self._retention_targets = [
            ("spans", "start_time"),
            ("turn_metrics", "created_at"),
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


def get_monitor_maintenance_service() -> MonitorMaintenanceService:
    global _MAINTENANCE_SERVICE_SINGLETON
    if _MAINTENANCE_SERVICE_SINGLETON is not None:
        return _MAINTENANCE_SERVICE_SINGLETON

    with _MAINTENANCE_SERVICE_LOCK:
        if _MAINTENANCE_SERVICE_SINGLETON is None:
            _MAINTENANCE_SERVICE_SINGLETON = MonitorMaintenanceService()
    return _MAINTENANCE_SERVICE_SINGLETON
