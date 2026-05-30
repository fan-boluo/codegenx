from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.mysql.session import session_maker
from monitor.alert_evaluator import get_alert_streak_tracker
from monitor.monitor_query_service import MonitorQueryService, get_monitor_query_service
from shared.config.log_config import log
from shared.constants import get_session_dir
from shared.schema.monitor import MonitorCleanupSummary, MonitorCleanupTableResult

CHAT_HISTORY_FILE_GLOB = "chat_history_*.jsonl"
CHAT_HISTORY_RETENTION_DAYS = 3
_CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS = 86400  # 24 hours


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

    # ── chat history file cleanup ─────────────────────────────────────────

    def cleanup_chat_history_files(self, retention_days: int = CHAT_HISTORY_RETENTION_DAYS) -> int:
        """Delete chat_history_*.jsonl files whose mtime exceeds retention_days.
        Returns the number of deleted files."""
        now = datetime.now(UTC).replace(tzinfo=None)
        cutoff = now - timedelta(days=retention_days)
        runtime_root = get_session_dir("main").parent.parent
        if not runtime_root.exists():
            return 0

        deleted = 0
        history_glob = f"*/session/{CHAT_HISTORY_FILE_GLOB}"
        for history_file in runtime_root.glob(history_glob):
            try:
                mtime = datetime.fromtimestamp(history_file.stat().st_mtime, tz=UTC).replace(tzinfo=None)
                if mtime < cutoff:
                    history_file.unlink()
                    deleted += 1
            except Exception:
                log.warning("cleanup_chat_history_files: skip file={}", history_file)

        if deleted:
            log.info("cleanup_chat_history_files: removed {} expired history files (retention={}d)", deleted, retention_days)
        return deleted

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
    _last_chat_history_cleanup: datetime | None = None

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            now = datetime.now(UTC)

            # 1) Chat history file cleanup (once per day)
            if _last_chat_history_cleanup is None or \
               (now - _last_chat_history_cleanup).total_seconds() >= _CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS:
                file_deleted = service.cleanup_chat_history_files()
                _last_chat_history_cleanup = now
            else:
                file_deleted = 0

            # 2) DB history retention cleanup
            result = await service.cleanup_history(retention_days=7, dry_run=False)
            log.info(
                "Periodic maintenance: chat_history_files_deleted={} DB_cleanup_status={} DB_deletedRows={}",
                file_deleted, result.status, result.deleted_rows,
            )

            # 3) Alert streak stale-entry cleanup
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
