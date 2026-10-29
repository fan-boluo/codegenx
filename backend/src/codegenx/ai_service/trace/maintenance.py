"""trace 周期维护：PG telemetry 保留期清理 + MySQL chat_message 历史清理。

- telemetry.traces / trace_spans 按 started_at 保留期删除（先子后父）
- chat_message 清理沿用原逻辑（MySQL，主业务表，与监控双轨清理无关）
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from shared import log

from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.system_app import SystemApp
from codegenx.ai_service.trace.schema.admin import MonitorCleanupSummary, MonitorCleanupTableResult
from db.postgres.postgres_client import get_postgres_pool

CHAT_HISTORY_RETENTION_DAYS = 3
_CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS = 86400  # 24 hours

# ── periodic task holders ──────────────────────────────────────────────────
_CLEANUP_TASK: asyncio.Task | None = None
_CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes


class MonitorMaintenanceService(BaseComponent):
    """trace 周期维护（telemetry 保留期 + chat 历史清理），后台任务生命周期由组件钩子管理。"""

    name = ComponentType.TRACE_MAINTENANCE

    def __init__(self, system_app: SystemApp | None = None) -> None:
        BaseComponent.__init__(self, system_app)

    def init_app(self, system_app: SystemApp) -> None:
        self.system_app = system_app

    async def async_before_start(self) -> None:
        await self.start_periodic_maintenance()

    async def async_before_stop(self) -> None:
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
        """Launch the background task that runs retention cleanup on a timer."""
        global _CLEANUP_TASK
        if _CLEANUP_TASK is not None and not _CLEANUP_TASK.done():
            return  # already running

        _CLEANUP_TASK = asyncio.create_task(
            _periodic_maintenance_loop(interval_seconds=interval_seconds),
            name="trace-periodic-maintenance",
        )
        log.info("Trace periodic maintenance started (interval={}s)", interval_seconds)

    async def stop_periodic_maintenance(self) -> None:
        global _CLEANUP_TASK
        if _CLEANUP_TASK is not None and not _CLEANUP_TASK.done():
            _CLEANUP_TASK.cancel()
            with __import__("contextlib", fromlist=["suppress"]).suppress(asyncio.CancelledError):
                await _CLEANUP_TASK
            _CLEANUP_TASK = None
            log.info("Trace periodic maintenance stopped")

    # ── telemetry retention cleanup ───────────────────────────────────────

    async def cleanup_history(self, *, retention_days: int = 7, dry_run: bool = False) -> MonitorCleanupSummary:
        """按保留期清理 telemetry（先 spans 后 traces，同一 trace 随主行删除）。"""
        cutoff_at = datetime.now(UTC) - timedelta(days=max(retention_days, 1))
        table_results: list[MonitorCleanupTableResult] = []
        total_affected = 0
        overall_status = "success"

        pool = await get_postgres_pool()
        async with pool.acquire() as conn:
            # 1) 过期 traces 的 spans（子表）
            spans_deleted = traces_deleted = 0
            try:
                expired = await conn.fetchval(
                    "SELECT COUNT(*) FROM telemetry.traces WHERE started_at < $1", cutoff_at
                )
                if not dry_run and expired:
                    spans_deleted = await conn.execute(
                        "DELETE FROM telemetry.trace_spans WHERE trace_id IN "
                        "(SELECT trace_id FROM telemetry.traces WHERE started_at < $1)",
                        cutoff_at,
                    )
                    traces_deleted = await conn.execute(
                        "DELETE FROM telemetry.traces WHERE started_at < $1", cutoff_at
                    )
                total_affected += int(expired or 0)
                table_results.append(MonitorCleanupTableResult(
                    tableName="telemetry.traces",
                    status="success",
                    affectedRows=int(expired or 0),
                    cutoffAt=cutoff_at,
                ))
                if spans_deleted or traces_deleted:
                    log.info("telemetry cleanup: {} {}",
                             spans_deleted.split()[-1] if spans_deleted else 0,
                             traces_deleted.split()[-1] if traces_deleted else 0)
            except Exception as exc:
                overall_status = "partial"
                table_results.append(MonitorCleanupTableResult(
                    tableName="telemetry.traces", status="error",
                    affectedRows=0, cutoffAt=cutoff_at, errorMessage=str(exc),
                ))

            # 2) 孤儿 spans（主行已删而 spans 残留的兜底清理）
            try:
                orphan = await conn.fetchval(
                    "SELECT COUNT(*) FROM telemetry.trace_spans s "
                    "WHERE NOT EXISTS (SELECT 1 FROM telemetry.traces t WHERE t.trace_id = s.trace_id)"
                )
                if not dry_run and orphan:
                    await conn.execute(
                        "DELETE FROM telemetry.trace_spans s "
                        "WHERE NOT EXISTS (SELECT 1 FROM telemetry.traces t WHERE t.trace_id = s.trace_id)"
                    )
                total_affected += int(orphan or 0)
                table_results.append(MonitorCleanupTableResult(
                    tableName="telemetry.trace_spans (orphan)",
                    status="success",
                    affectedRows=int(orphan or 0),
                    cutoffAt=cutoff_at,
                ))
            except Exception as exc:
                overall_status = "partial"
                table_results.append(MonitorCleanupTableResult(
                    tableName="telemetry.trace_spans (orphan)", status="error",
                    affectedRows=0, cutoffAt=cutoff_at, errorMessage=str(exc),
                ))

        return MonitorCleanupSummary(
            retentionDays=max(retention_days, 1),
            dryRun=dry_run,
            status=overall_status,
            deletedRows=total_affected,
            executedAt=datetime.now(UTC),
            tableResults=table_results,
        )


def get_monitor_maintenance_service() -> MonitorMaintenanceService:
    """取 trace 周期维护组件（经全局容器查表；容器未初始化时 fail fast）。"""
    from codegenx.ai_service.system_app import get_app

    return MonitorMaintenanceService.get_instance(get_app())


def initialize_trace_maintenance(system_app: SystemApp) -> MonitorMaintenanceService:
    """注册 trace 周期维护组件（system_app.initialize_components 调用）。"""
    return system_app.register(MonitorMaintenanceService)


# ── internal loop ──────────────────────────────────────────────────────────

async def _periodic_maintenance_loop(*, interval_seconds: int) -> None:
    """Run chat history + telemetry retention cleanup on a timer."""
    service = get_monitor_maintenance_service()
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

            # 2) telemetry retention cleanup
            result = await service.cleanup_history(retention_days=7, dry_run=False)
            if result.status == "success":
                log.info(
                    "Periodic maintenance: chat_messages_deleted={} telemetry_deletedRows={}",
                    chat_deleted, result.deleted_rows,
                )
            else:
                failed = [
                    "{}: {}".format(r.table_name, r.error_message)
                    for r in (result.table_results or [])
                    if r.status == "error"
                ]
                log.warning(
                    "Periodic maintenance: chat_messages_deleted={} telemetry_status={} deletedRows={} failed_tables=[{}]",
                    chat_deleted, result.status, result.deleted_rows, "; ".join(failed),
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Periodic maintenance iteration failed")
