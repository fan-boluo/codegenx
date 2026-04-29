from __future__ import annotations

import time
from typing import Any

from shared.monitor.ai_model_metrics_collector import AIModelMetricsCollector, ai_model_metrics_collector
from shared.monitor.monitor_context import MonitorContextHolder


class AIModelMonitorListener:
    def __init__(self, collector: AIModelMetricsCollector | None = None) -> None:
        self.collector = collector or ai_model_metrics_collector

    def on_request(self, model_name: str) -> float:
        context = MonitorContextHolder.get_context()
        user_id = context.user_id if context is not None else "unknown"
        app_id = context.app_id if context is not None else "unknown"
        self.collector.record_request(user_id, app_id, model_name, "started")
        return time.perf_counter()

    def on_response(self, model_name: str, started_at: float, usage: dict[str, Any] | None = None) -> None:
        context = MonitorContextHolder.get_context()
        user_id = context.user_id if context is not None else "unknown"
        app_id = context.app_id if context is not None else "unknown"
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.collector.record_request(user_id, app_id, model_name, "success")
        self.collector.record_response_time(user_id, app_id, model_name, duration_ms)
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
            self.collector.record_token_usage(user_id, app_id, model_name, "input", prompt_tokens)
            self.collector.record_token_usage(user_id, app_id, model_name, "output", completion_tokens)
            self.collector.record_token_usage(user_id, app_id, model_name, "total", total_tokens)

    def on_error(self, model_name: str, started_at: float, error_message: str) -> None:
        context = MonitorContextHolder.get_context()
        user_id = context.user_id if context is not None else "unknown"
        app_id = context.app_id if context is not None else "unknown"
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.collector.record_request(user_id, app_id, model_name, "error")
        self.collector.record_error(user_id, app_id, model_name, error_message)
        self.collector.record_response_time(user_id, app_id, model_name, duration_ms)


ai_model_monitor_listener = AIModelMonitorListener()