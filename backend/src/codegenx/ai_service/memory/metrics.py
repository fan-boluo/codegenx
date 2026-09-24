"""
记忆系统指标与结构化日志门面（P1-12，设计方案 v2.1 §10）。

原则：
- 埋点绝不抛异常、绝不阻断业务（prometheus_client 本身不抛，这里再兜一层）；
- summary/content 一律不进日志（§10.2 硬要求），排错用 memory_id 回查 MySQL。
"""
from __future__ import annotations

from shared import log


def _m():
    from codegenx.ai_service.monitor import prometheus_metrics as m
    return m


def inc_extract(app_id: str, status: str) -> None:
    """warm_extract 任务结果计数（status: ok/failed/dead）。"""
    try:
        _m().memory_extract_total.labels(app_id=app_id or "", status=status).inc()
    except Exception:  # noqa: BLE001 — 埋点失败静默
        pass


def observe_extract_latency(app_id: str, seconds: float) -> None:
    try:
        _m().memory_extract_latency_seconds.labels(app_id=app_id or "").observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001
        pass


def inc_extract_triggered(reason: str) -> None:
    """提炼触发原因计数（signal/token/timeout/session_end/stale_scan）。"""
    try:
        _m().memory_extract_triggered_total.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


def inc_write_rejected(reason: str) -> None:
    """候选记忆准入拒绝计数（empty/type/sensitive/inferred_hot/hot_full）。"""
    try:
        _m().memory_write_rejected_total.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


def set_task_dead(count: int) -> None:
    try:
        _m().memory_task_dead.set(max(0, int(count)))
    except Exception:  # noqa: BLE001
        pass


def inc_stuck_reclaimed(count: int) -> None:
    if count <= 0:
        return
    try:
        _m().memory_task_stuck_reclaimed_total.inc(count)
    except Exception:  # noqa: BLE001
        pass


def set_vector_pending(count: int) -> None:
    try:
        _m().memory_vector_pending.set(max(0, int(count)))
    except Exception:  # noqa: BLE001
        pass


def inc_vector_sync_fail() -> None:
    try:
        _m().memory_vector_sync_fail_total.inc()
    except Exception:  # noqa: BLE001
        pass


def inc_reconcile(missing: int = 0, ghost: int = 0, spot_miss: int = 0) -> None:
    try:
        if missing:
            _m().memory_reconcile_missing_total.inc(missing)
        if ghost:
            _m().memory_reconcile_ghost_total.inc(ghost)
        if spot_miss:
            _m().memory_reconcile_spotcheck_miss_total.inc(spot_miss)
    except Exception:  # noqa: BLE001
        pass


def set_hot_gauges(token_ratio: float, hot_count: int) -> None:
    """hot 层注入水位（最近一次采样的用户维度值）。"""
    try:
        _m().memory_hot_token_usage_ratio.set(max(0.0, float(token_ratio)))
        _m().memory_hot_count.set(max(0, int(hot_count)))
    except Exception:  # noqa: BLE001
        pass


def inc_degrade(kind: str) -> None:
    """降级打点（§9 原则 2：降级必须可观测）。kind: qdrant/redis/mysql/embedding/timeout。"""
    try:
        _m().memory_degrade_total.labels(kind=kind).inc()
    except Exception:  # noqa: BLE001
        pass


def inc_conflict(pairs: int) -> None:
    if pairs <= 0:
        return
    try:
        _m().memory_conflict_detected_total.inc(pairs)
    except Exception:  # noqa: BLE001
        pass


def inc_consolidate_merged(count: int) -> None:
    if count <= 0:
        return
    try:
        _m().memory_consolidate_merged_total.inc(count)
    except Exception:  # noqa: BLE001
        pass


def inc_archived(action: str, count: int) -> None:
    if count <= 0:
        return
    try:
        _m().memory_archived_total.labels(action=action).inc(count)
    except Exception:  # noqa: BLE001
        pass


def log_event(event: str, **fields) -> None:
    """§10.2 结构化操作日志：仅元数据字段，禁止传 summary/content。"""
    try:
        rendered = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        log.info("[memory:{}]{}", event, f" {rendered}" if rendered else "")
    except Exception:  # noqa: BLE001
        pass
