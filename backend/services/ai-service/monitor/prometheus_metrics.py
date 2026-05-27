"""
Prometheus metrics for the CodeGenX AI Service.

All metrics are defined once as module-level singletons.  Pipeline lifecycle
methods call into these definitions via the helper functions below.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Labels common to many metrics
# ---------------------------------------------------------------------------
BASE_LABELS = ["app_id", "user_id", "model"]

# ---------------------------------------------------------------------------
# Session-level
# ---------------------------------------------------------------------------
sessions_total = Counter(
    "codegenx_sessions_total",
    "Total agent sessions created",
    BASE_LABELS,
)

active_sessions = Gauge(
    "codegenx_active_sessions",
    "Currently running agent sessions",
    BASE_LABELS,
)

session_duration_seconds = Histogram(
    "codegenx_session_duration_seconds",
    "Session wall-clock duration",
    BASE_LABELS,
    buckets=[30, 60, 120, 300, 600, 900, 1800, 3600],
)

# ---------------------------------------------------------------------------
# Turn-level
# ---------------------------------------------------------------------------
turns_total = Counter(
    "codegenx_turns_total",
    "Total turns across all sessions",
    BASE_LABELS + ["status"],
)

turn_duration_seconds = Histogram(
    "codegenx_turn_duration_seconds",
    "Turn wall-clock duration",
    BASE_LABELS,
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
llm_calls_total = Counter(
    "codegenx_llm_calls_total",
    "Total LLM calls",
    BASE_LABELS + ["status"],
)

llm_latency_seconds = Histogram(
    "codegenx_llm_latency_seconds",
    "LLM call round-trip latency",
    BASE_LABELS,
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

llm_first_token_seconds = Histogram(
    "codegenx_llm_first_token_seconds",
    "Time-to-first-token for LLM calls",
    BASE_LABELS,
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10],
)

llm_prompt_tokens_total = Counter(
    "codegenx_llm_prompt_tokens_total",
    "Total prompt tokens sent to LLMs",
    BASE_LABELS,
)

llm_completion_tokens_total = Counter(
    "codegenx_llm_completion_tokens_total",
    "Total completion tokens received from LLMs",
    BASE_LABELS,
)

# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
tool_calls_total = Counter(
    "codegenx_tool_calls_total",
    "Total tool invocations",
    BASE_LABELS + ["tool_name", "status"],
)

tool_latency_seconds = Histogram(
    "codegenx_tool_latency_seconds",
    "Tool execution latency",
    BASE_LABELS + ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30],
)

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
memory_hits_total = Counter(
    "codegenx_memory_hits_total",
    "Total memory retrieval hits",
    BASE_LABELS,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
errors_total = Counter(
    "codegenx_errors_total",
    "Total errors across all scopes",
    BASE_LABELS + ["scope", "error_type"],
)

# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------
token_usage_ratio = Gauge(
    "codegenx_token_usage_ratio",
    "Current turn token usage as fraction of budget (0.0 - 1.0)",
    BASE_LABELS,
)

context_token_count = Gauge(
    "codegenx_context_token_count",
    "Token count for the current turn's assembled context",
    BASE_LABELS,
)

# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------
llm_recoveries_total = Counter(
    "codegenx_llm_recoveries_total",
    "Total LLM call recoveries / retries",
    BASE_LABELS + ["recovery_kind"],
)

# ---------------------------------------------------------------------------
# Alert streak gauges (for Prometheus alert rules — set directly by tracker)
# ---------------------------------------------------------------------------
llm_recovery_streak = Gauge(
    "codegenx_llm_recovery_streak",
    "Consecutive LLM recovery count (0 = no active streak)",
    BASE_LABELS,
)

tool_failure_streak = Gauge(
    "codegenx_tool_failure_streak",
    "Consecutive tool failure count (0 = no active streak)",
    BASE_LABELS,
)

context_breach_streak = Gauge(
    "codegenx_context_breach_streak",
    "Consecutive times context exceeded threshold without compression (0 = no active streak)",
    BASE_LABELS,
)

llm_last_call_latency_seconds_gauge = Gauge(
    "codegenx_llm_last_call_latency_seconds",
    "Latency of the most recent LLM call",
    BASE_LABELS,
)


# ---------------------------------------------------------------------------
# Helper: build base label values dict from session / turn state
# ---------------------------------------------------------------------------

def _base_labels(session_telemetry, turn_telemetry=None) -> dict[str, str]:
    """Return a label dict from whichever telemetry object is available."""
    src = turn_telemetry or session_telemetry
    return {
        "app_id": str(getattr(src, "app_id", "") or "main"),
        "user_id": str(getattr(src, "user_id", "") or ""),
        "model": str(getattr(src, "model", "") or "unknown"),
    }


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def record_session_start(session_telemetry) -> None:
    labels = _base_labels(session_telemetry)
    sessions_total.labels(**labels).inc()
    active_sessions.labels(**labels).inc()


def record_session_end(session_telemetry) -> None:
    labels = _base_labels(session_telemetry)
    active_sessions.labels(**labels).dec()
    duration_s = getattr(session_telemetry, "duration_ms", 0) or 0
    if hasattr(session_telemetry, "started_at") and hasattr(session_telemetry, "ended_at"):
        st = session_telemetry.started_at
        ed = session_telemetry.ended_at
        if st and ed:
            duration_s = max(0, (ed - st).total_seconds())
    session_duration_seconds.labels(**labels).observe(duration_s)


# ---------------------------------------------------------------------------
# Turn helpers
# ---------------------------------------------------------------------------

def record_turn_end(session_telemetry, turn_telemetry) -> None:
    labels = _base_labels(session_telemetry, turn_telemetry)
    status = str(getattr(turn_telemetry, "status", "running")).lower()
    turns_total.labels(**labels, status=status).inc()
    duration_s = (getattr(turn_telemetry, "duration_ms", 0) or 0) / 1000
    turn_duration_seconds.labels(**labels).observe(duration_s)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def record_llm_call(session_telemetry, turn_telemetry, *, is_error: bool = False, total_ms: int = 0, first_token_ms: int = 0) -> None:
    labels = _base_labels(session_telemetry, turn_telemetry)
    status = "error" if is_error else "ok"
    total_s = total_ms / 1000
    first_s = first_token_ms / 1000
    prompt_tokens = getattr(turn_telemetry, "total_prompt_tokens", 0) or 0
    completion_tokens = getattr(turn_telemetry, "total_completion_tokens", 0) or 0

    llm_calls_total.labels(**labels, status=status).inc()
    llm_latency_seconds.labels(**labels).observe(total_s)
    llm_first_token_seconds.labels(**labels).observe(first_s)
    llm_prompt_tokens_total.labels(**labels).inc(prompt_tokens)
    llm_completion_tokens_total.labels(**labels).inc(completion_tokens)

    recovery_count = getattr(turn_telemetry, "llm_recovery_count", 0) or 0
    recovery_kind = getattr(turn_telemetry, "last_recovery_kind", "") or ""
    if recovery_count > 0:
        llm_recoveries_total.labels(**labels, recovery_kind=recovery_kind).inc(recovery_count)


def record_context_metrics(session_telemetry, turn_telemetry) -> None:
    labels = _base_labels(session_telemetry, turn_telemetry)
    count = getattr(turn_telemetry, "token_count", 0) or 0
    usage = getattr(turn_telemetry, "token_usage", 0.0) or 0.0
    context_token_count.labels(**labels).set(count)
    token_usage_ratio.labels(**labels).set(min(usage, 1.0))


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def record_tool_call(session_telemetry, turn_telemetry, tool_name: str, is_error: bool, latency_ms: int) -> None:
    labels = _base_labels(session_telemetry, turn_telemetry)
    labels["tool_name"] = tool_name
    status = "error" if is_error else "ok"
    tool_calls_total.labels(**labels, status=status).inc()
    tool_latency_seconds.labels(**labels).observe(latency_ms / 1000)


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def record_memory_hits(session_telemetry, turn_telemetry, hits: int) -> None:
    labels = _base_labels(session_telemetry, turn_telemetry)
    if hits > 0:
        memory_hits_total.labels(**labels).inc(hits)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def record_error(session_telemetry, turn_telemetry, scope: str, error_type: str = "") -> None:


    abels = _base_labels(session_telemetry, turn_telemetry)
    errors_total.labels(**labels, scope=scope, error_type=error_type).inc()
