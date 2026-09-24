"""
Prometheus metrics for the CodeGenX AI Service.

All metrics are defined once as module-level singletons.  Pipeline lifecycle
methods call into these definitions via the helper functions below.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def _app_id(src) -> str:
    """Extract app_id from telemetry object, default 'main'."""
    return str(getattr(src, "app_id", "") or "main")


def _model(src) -> str:
    """Extract model from telemetry object, default 'unknown'."""
    return str(getattr(src, "model", "") or "unknown")


def _status(src) -> str:
    """Extract normalised status string from telemetry object."""
    if src is None:
        return "running"
    status = getattr(src, "status", "running")
    if hasattr(status, "value"):
        return status.value
    return str(status or "running").lower()


# ---------------------------------------------------------------------------
# Session-level
# ---------------------------------------------------------------------------
sessions_total = Counter(
    "codegenx_sessions_total",
    "Total agent sessions created",
    ["app_id", "status"],
)

active_sessions = Gauge(
    "codegenx_active_sessions",
    "Currently running agent sessions",
    ["app_id"],
)

session_duration_seconds = Histogram(
    "codegenx_session_duration_seconds",
    "Session wall-clock duration",
    ["app_id"],
    buckets=[30, 60, 120, 300, 600, 900, 1800, 3600],
)

# ---------------------------------------------------------------------------
# Turn-level
# ---------------------------------------------------------------------------
turns_total = Counter(
    "codegenx_turns_total",
    "Total turns across all sessions",
    ["app_id", "status"],
)

turn_duration_seconds = Histogram(
    "codegenx_turn_duration_seconds",
    "Turn wall-clock duration",
    ["app_id"],
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
llm_calls_total = Counter(
    "codegenx_llm_calls_total",
    "Total LLM calls",
    ["app_id", "model", "status"],
)

llm_latency_seconds = Histogram(
    "codegenx_llm_latency_seconds",
    "LLM call round-trip latency",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

llm_first_token_seconds = Histogram(
    "codegenx_llm_first_token_seconds",
    "Time-to-first-token for LLM calls",
    ["model"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10],
)

llm_prompt_tokens_total = Counter(
    "codegenx_llm_prompt_tokens_total",
    "Total prompt tokens sent to LLMs",
    ["app_id", "model"],
)

llm_completion_tokens_total = Counter(
    "codegenx_llm_completion_tokens_total",
    "Total completion tokens received from LLMs",
    ["app_id", "model"],
)

# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
tool_calls_total = Counter(
    "codegenx_tool_calls_total",
    "Total tool invocations",
    ["app_id", "tool_name", "status"],
)

tool_latency_seconds = Histogram(
    "codegenx_tool_latency_seconds",
    "Tool execution latency",
    ["app_id", "tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30],
)

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
memory_hits_total = Counter(
    "codegenx_memory_hits_total",
    "Total memory retrieval hits",
    ["app_id"],
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
errors_total = Counter(
    "codegenx_errors_total",
    "Total errors across all scopes",
    ["app_id", "scope", "error_type"],
)

# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------
llm_recoveries_total = Counter(
    "codegenx_llm_recoveries_total",
    "Total LLM call recoveries / retries",
    ["app_id", "model", "recovery_kind"],
)

# ---------------------------------------------------------------------------
# Alert streak gauges (for Prometheus alert rules — set directly by tracker)
# ---------------------------------------------------------------------------
llm_recovery_streak = Gauge(
    "codegenx_llm_recovery_streak",
    "Consecutive LLM recovery count (0 = no active streak)",
    ["model"],
)

tool_failure_streak = Gauge(
    "codegenx_tool_failure_streak",
    "Consecutive tool failure count (0 = no active streak)",
    ["tool_name"],
)

context_breach_streak = Gauge(
    "codegenx_context_breach_streak",
    "Consecutive times context exceeded threshold without compression (0 = no active streak)",
    ["session_id"],
)

llm_last_call_latency_seconds_gauge = Gauge(
    "codegenx_llm_last_call_latency_seconds",
    "Latency of the most recent LLM call",
    ["model"],
)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def record_session_start(session_telemetry) -> None:
    app_id = _app_id(session_telemetry)
    status = _status(session_telemetry)
    sessions_total.labels(app_id=app_id, status=status).inc()
    active_sessions.labels(app_id=app_id).inc()


def record_session_end(session_telemetry) -> None:
    app_id = _app_id(session_telemetry)
    active_sessions.labels(app_id=app_id).dec()
    duration_s = getattr(session_telemetry, "duration_ms", 0) or 0
    if hasattr(session_telemetry, "started_at") and hasattr(session_telemetry, "ended_at"):
        st = session_telemetry.started_at
        ed = session_telemetry.ended_at
        if st and ed:
            duration_s = max(0, (ed - st).total_seconds())
    session_duration_seconds.labels(app_id=app_id).observe(duration_s)


# ---------------------------------------------------------------------------
# Turn helpers
# ---------------------------------------------------------------------------

def record_turn_end(session_telemetry, turn_telemetry) -> None:
    src = turn_telemetry or session_telemetry
    app_id = _app_id(src)
    status = _status(turn_telemetry) if turn_telemetry else "running"
    turns_total.labels(app_id=app_id, status=status).inc()
    duration_s = (getattr(turn_telemetry, "duration_ms", 0) or 0) / 1000
    turn_duration_seconds.labels(app_id=app_id).observe(duration_s)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def record_llm_call(session_telemetry, turn_telemetry, *, is_error: bool = False, total_ms: int = 0, first_token_ms: int = 0) -> None:
    src = turn_telemetry or session_telemetry
    app_id = _app_id(src)
    model = _model(src)
    status = "error" if is_error else "ok"
    total_s = total_ms / 1000
    first_s = first_token_ms / 1000
    prompt_tokens = getattr(turn_telemetry, "total_prompt_tokens", 0) or 0
    completion_tokens = getattr(turn_telemetry, "total_completion_tokens", 0) or 0

    llm_calls_total.labels(app_id=app_id, model=model, status=status).inc()
    llm_latency_seconds.labels(model=model).observe(total_s)
    llm_first_token_seconds.labels(model=model).observe(first_s)
    llm_prompt_tokens_total.labels(app_id=app_id, model=model).inc(prompt_tokens)
    llm_completion_tokens_total.labels(app_id=app_id, model=model).inc(completion_tokens)

    recovery_count = getattr(turn_telemetry, "llm_recovery_count", 0) or 0
    recovery_kind = getattr(turn_telemetry, "last_recovery_kind", "") or ""
    if recovery_count > 0:
        llm_recoveries_total.labels(app_id=app_id, model=model, recovery_kind=recovery_kind).inc(recovery_count)


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def record_tool_call(session_telemetry, turn_telemetry, tool_name: str, is_error: bool, latency_ms: int) -> None:
    src = turn_telemetry or session_telemetry
    app_id = _app_id(src)
    status = "error" if is_error else "ok"
    tool_calls_total.labels(app_id=app_id, tool_name=tool_name, status=status).inc()
    tool_latency_seconds.labels(app_id=app_id, tool_name=tool_name).observe(latency_ms / 1000)


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def record_memory_hits(session_telemetry, turn_telemetry, hits: int) -> None:
    src = turn_telemetry or session_telemetry
    if hits > 0:
        memory_hits_total.labels(app_id=_app_id(src)).inc(hits)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def record_error(session_telemetry, turn_telemetry, scope: str, error_type: str = "") -> None:
    src = session_telemetry or turn_telemetry
    if src is None:
        return
    errors_total.labels(app_id=_app_id(src), scope=scope, error_type=error_type).inc()
