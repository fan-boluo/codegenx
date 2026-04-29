from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any


class AIModelMetricsCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._request_counters: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._error_counters: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._token_counters: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._response_durations: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    def record_request(self, user_id: str, app_id: str, model_name: str, status: str) -> None:
        with self._lock:
            self._request_counters[(user_id, app_id, model_name, status)] += 1

    def record_error(self, user_id: str, app_id: str, model_name: str, error_message: str) -> None:
        with self._lock:
            self._error_counters[(user_id, app_id, model_name, error_message)] += 1

    def record_token_usage(self, user_id: str, app_id: str, model_name: str, token_type: str, token_count: int) -> None:
        with self._lock:
            self._token_counters[(user_id, app_id, model_name, token_type)] += int(token_count)

    def record_response_time(self, user_id: str, app_id: str, model_name: str, duration_ms: int) -> None:
        with self._lock:
            self._response_durations[(user_id, app_id, model_name)].append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests": dict(self._request_counters),
                "errors": dict(self._error_counters),
                "tokens": dict(self._token_counters),
                "responseDurations": {key: list(value) for key, value in self._response_durations.items()},
            }


ai_model_metrics_collector = AIModelMetricsCollector()