from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.mysql.session import session_maker
from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.system_app import SystemApp
from codegenx.ai_service.monitor.alert_evaluator import get_alert_streak_tracker
from codegenx.ai_service.monitor.monitor_query_service import MonitorQueryService, get_monitor_query_service
from shared import log
from codegenx.ai_service.monitor.schema.monitor import MonitorCleanupSummary, MonitorCleanupTableResult

CHAT_HISTORY_RETENTION_DAYS = 3
_CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS = 86400  # 24 hours


# ── periodic task holders ──────────────────────────────────────────────────
_CLEANUP_TASK: asyncio.Task | None = None
_CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes


class MonitorMaintenanceService(BaseComponent):
    """监控周期维护（DB 历史清理/告警状态回收），后台任务生命周期由组件钩子管理。"""

    name = ComponentType.MONITOR_MAINTENANCE

    def __init__(
        self,
        system_app: SystemApp | None = None,
        *,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        query_service: MonitorQueryService | None = None,
    ) -> None:
        BaseComponent.__init__(self, system_app)
        self._db_session_factory = db_session_factory or session_maker
        self._query_service = query_service or get_monitor_query_service()
        self._retention_targets = [
            ("spans", "start_time"),
            ("turn_metrics", "created_at"),
            ("session_metrics", "updated_at"),
            ("monitor_alerts", "triggered_at"),
        ]

    def init_app(self, system_app: SystemApp) -> None:
        self.system_app = system_app

    async def async_before_start(self) -> None:
        """启动周期维护后台任务。"""
        await self.start_periodic_maintenance()

    async def async_before_stop(self) -> None:
        """停止周期维护后台任务。"""
        await self.stop_periodic_maintenance()

    # ── chat message DB cleanup ───────────────────────────────────────────

    async def cleanup_chat_messages(self, retention_days: int = CHAT_HISTORY_RETENTION_DAYS) -> int:
        """Delete chat_message rows older than retention_days. Returns deleted rows."""
        from codegenx.ai_service.chat_message import get_chat_message_store

        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
        try:
            deleted = await get_chat_message_store().delete_before(cutoff)
        except Exception as exc:
            log.warning("cleanup_chat_messages failed: {}", exc)
            return 0
        if deleted:
            log.info("cleanup_chat_messages: removed {} expired rows (retention={}d)", deleted, retention_days)
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
    """取监控周期维护组件（经全局容器查表；容器未初始化时 fail fast）。"""
    from codegenx.ai_service.system_app import get_app

    return MonitorMaintenanceService.get_instance(get_app())


def initialize_monitor_maintenance(system_app: SystemApp) -> MonitorMaintenanceService:
    """注册监控周期维护组件（system_app.initialize_components 调用）。"""
    return system_app.register(MonitorMaintenanceService)


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

            # 1) Chat message cleanup (once per day)
            if _last_chat_history_cleanup is None or \
               (now - _last_chat_history_cleanup).total_seconds() >= _CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS:
                chat_deleted = await service.cleanup_chat_messages()
                _last_chat_history_cleanup = now
            else:
                chat_deleted = 0

            # 2) DB history retention cleanup
            result = await service.cleanup_history(retention_days=7, dry_run=False)
            if result.status == "success":
                log.info(
                    "Periodic maintenance: chat_messages_deleted={} DB_cleanup_status={} DB_deletedRows={}",
                    chat_deleted, result.status, result.deleted_rows,
                )
            else:
                # F-3 修复：partial 时必须带失败表名与原因，否则只看到 partial 无从排查
                failed = [
                    "{}: {}".format(r.table_name, r.error_message)
                    for r in (result.table_results or [])
                    if r.status == "error"
                ]
                log.warning(
                    "Periodic maintenance: chat_messages_deleted={} DB_cleanup_status={} DB_deletedRows={} failed_tables=[{}]",
                    chat_deleted, result.status, result.deleted_rows, "; ".join(failed),
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
                from codegenx.ai_service.monitor.monitor_pipeline import get_monitor_pipeline
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
