"""
Alert streak tracker for Prometheus-based alerting.

Manages in-memory consecutive-event counters and pushes them to Prometheus
gauges.  The actual alert evaluation is done by Prometheus alert rules
(see infra/monitoring/prometheus_alerts.yml).  This module only tracks
"streaks" that require state across events — single-event metrics (latency,
error counts, etc.) are recorded directly by prometheus_metrics helpers.
"""
from __future__ import annotations

from threading import Lock

from monitor.prometheus_metrics import (
    context_breach_streak,
    llm_last_call_latency_seconds_gauge,
    llm_recovery_streak,
    tool_failure_streak,
)
from shared.config.log_config import log

# ---------------------------------------------------------------------------
# Thresholds (tune as needed)
# ---------------------------------------------------------------------------
CONTEXT_USAGE_BREACH_RATIO = 0.85  # token_usage > 85% of budget → breach

_SINGLETON: "AlertStreakTracker | None" = None
_LOCK = Lock()


class AlertStreakTracker:
    """Tracks consecutive-event streaks and exposes them as Prometheus gauges."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._recovery_streaks: dict[str, int] = {}       # session_id → streak
        self._tool_failure_streaks: dict[str, int] = {}   # session_id → streak
        self._context_breach_streaks: dict[str, int] = {}  # session_id → streak

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def track_llm_outcome(
        self,
        *,
        session_id: str,
        model: str,
        recovery_count: int,
        latency_s: float,
    ) -> None:
        """Update recovery streak and last-call latency gauge."""
        llm_last_call_latency_seconds_gauge.labels(model=model).set(latency_s)

        with self._lock:
            if recovery_count > 0:
                streak = self._recovery_streaks.get(session_id, 0) + 1
            else:
                streak = 0
            self._recovery_streaks[session_id] = streak

        llm_recovery_streak.labels(model=model).set(streak)
        if streak >= 3:
            log.warning("LLM recovery streak={} session={}", streak, session_id)

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def track_context_breach(
        self,
        *,
        session_id: str,
        token_usage: float,
        is_compress: bool,
    ) -> None:
        """Update context threshold breach streak.

        A "breach" is defined as ``token_usage > CONTEXT_USAGE_BREACH_RATIO``
        while compression did NOT happen.  Consecutive breaches suggest the
        compressor is broken or misconfigured.
        """
        breached = (token_usage > CONTEXT_USAGE_BREACH_RATIO) and not is_compress

        with self._lock:
            if breached:
                streak = self._context_breach_streaks.get(session_id, 0) + 1
            else:
                streak = 0
            self._context_breach_streaks[session_id] = streak

        context_breach_streak.labels(session_id=session_id).set(streak)
        if streak >= 3:
            log.warning(
                "Context breach streak={} session={} usage={:.2f} is_compress={}",
                streak, session_id, token_usage, is_compress,
            )

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    def track_tool_outcome(
        self,
        *,
        session_id: str,
        tool_name: str,
        is_error: bool,
    ) -> None:
        """Update tool consecutive-failure streak."""
        with self._lock:
            if is_error:
                streak = self._tool_failure_streaks.get(session_id, 0) + 1
            else:
                streak = 0
            self._tool_failure_streaks[session_id] = streak

        tool_failure_streak.labels(tool_name=tool_name).set(streak)
        if streak >= 3:
            log.warning("Tool failure streak={} session={}", streak, session_id)

    # ------------------------------------------------------------------
    # Periodic cleanup
    # ------------------------------------------------------------------

    @property
    def tracked_session_count(self) -> int:
        """Return total number of active streak-tracked sessions."""
        with self._lock:
            return (
                len(self._recovery_streaks)
                + len(self._tool_failure_streaks)
                + len(self._context_breach_streaks)
            )

    def cleanup_stale_sessions(self, active_session_ids: set[str]) -> int:
        """Remove streak state for sessions that are no longer active.

        Returns the number of entries removed.
        """
        removed = 0
        with self._lock:
            for d in (self._recovery_streaks, self._tool_failure_streaks, self._context_breach_streaks):
                stale = [sid for sid in d if sid not in active_session_ids]
                for sid in stale:
                    d.pop(sid, None)
                    removed += 1
        if removed:
            log.info("AlertStreakTracker cleaned up {} stale entries across {} active sessions",
                     removed, len(active_session_ids))
        return removed

    def cleanup_session(self, session_id: str) -> None:
        """Remove per-session streak state (call on session end)."""
        with self._lock:
            self._recovery_streaks.pop(session_id, None)
            self._tool_failure_streaks.pop(session_id, None)
            self._context_breach_streaks.pop(session_id, None)


def get_alert_streak_tracker() -> AlertStreakTracker:
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _LOCK:
        if _SINGLETON is None:
            _SINGLETON = AlertStreakTracker()
    return _SINGLETON
