from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from threading import Lock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from monitor.alert_evaluator import get_alert_streak_tracker
from monitor.monitor_query_service import MonitorQueryService, get_monitor_query_service
from shared.config.log_config import log
from shared.schema.monitor import MonitorCleanupSummary, MonitorCleanupTableResult


_MAINTENANCE_SERVICE_SINGLETON: "MonitorMaintenanceService | None" = None
_MAINTENANCE_SERVICE_LOCK = Lock()

# ── periodic task holders ──────────────────────────────────────────────────
_CLEANUP_TASK: asyncio.Task | None = None
_CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes


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

    # ── periodic maintenance entry-point ──────────────────────────────────

    async def start_periodic_maintenance(self, interval_seconds: int = _CLEANUP_INTERVAL_SECONDS) -> None:
        """Launch the background task that runs cleanup_history + alert streak cleanup on a timer."""
        global _CLEANUP_TASK
        if _CLEANUP_TASK is not None and not _CLEANUP_TASK.done():
            return  # already running

        _CLEANUP_TASK = asyncio.create_task(
            _periodic_maintenance_loop(interval_seconds=interval_seconds),
            name="monitor-periodic-maintenance",
        )
        log.info("Monitor periodic maintenance started (interval={}s)", interval_seconds)

    async def stop_periodic_maintenance(self) -> None:
        global _CLEANUP_TASK
        if _CLEANUP_TASK is not None and not _CLEANUP_TASK.done():
            _CLEANUP_TASK.cancel()
            with __import__("contextlib", fromlist=["suppress"]).suppress(asyncio.CancelledError):
                await _CLEANUP_TASK
            _CLEANUP_TASK = None
            log.info("Monitor periodic maintenance stopped")

    # ── DB cleanup ────────────────────────────────────────────────────────

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


# ── internal loop ──────────────────────────────────────────────────────────

async def _periodic_maintenance_loop(*, interval_seconds: int) -> None:
    """Run DB history cleanup + alert streak stale-entry cleanup on a timer."""
    service = get_monitor_maintenance_service()
    tracker = get_alert_streak_tracker()

    while True:
        try:
            await asyncio.sleep(interval_seconds)

            # 1) DB history retention cleanup
            result = await service.cleanup_history(retention_days=7, dry_run=False)
            log.info(
                "Periodic maintenance: DB cleanup status={} deletedRows={}",
                result.status, result.deletedRows,
            )

            # 2) Alert streak stale-entry cleanup
            #    In a full implementation, the set of active session IDs would
            #    be obtained from the runtime session registry.  For now this
            #    cleans all entries whose session has been removed from the
            #    pipeline (MetricCollector dicts).
            #    The per-session cleanup on session_end already handles the
            #    normal case; this catches leaked entries.
            before = tracker.tracked_session_count
            if before > 0:
                from monitor.monitor_pipeline import get_monitor_pipeline
                pipeline = get_monitor_pipeline()
                active_ids = set(pipeline._metric_collectors.keys())
                removed = tracker.cleanup_stale_sessions(active_ids)
                if removed:
                    log.info("Periodic maintenance: alert streak cleanup removed={} before={} after={}",
                             removed, before, tracker.tracked_session_count)

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Periodic maintenance iteration failed")
