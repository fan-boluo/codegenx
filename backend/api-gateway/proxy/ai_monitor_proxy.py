from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from shared.config.config import get_settings
from services.discovery_adapter import discovery_adapter
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.service_invocation import ServiceInvocationError


settings = get_settings()


class AiMonitorProxy:
    def __init__(self) -> None:
        self.service_name = settings.ai_service_name
        self.base_url = f"http://{settings.ai_service_host}:{settings.ai_service_http_port}"
        self._timeout = httpx.Timeout(30.0)

    async def resolve_base_url(self) -> str:
        return await discovery_adapter.resolve_http_base_url(self.service_name, fallback_base_url=self.base_url)

    async def request_json(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        upstream_instance = await self.resolve_base_url()
        url = f"{upstream_instance}{path}"
        headers: dict[str, str] = {}
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        response = await self._request(method=method, path=path, params=params, trace_id=trace_id)
        return response.json()

    async def request_text(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> str:
        response = await self._request(method=method, path=path, params=params, trace_id=trace_id)
        return response.text

    async def _request(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> httpx.Response:
        upstream_instance = await self.resolve_base_url()
        url = f"{upstream_instance}{path}"
        headers: dict[str, str] = {}
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                )
        except Exception as exc:
            invocation_error = ServiceInvocationError(
                serviceName=self.service_name,
                protocol="http",
                operation=f"{method} {path}",
                target=url,
                message=str(exc),
                traceId=trace_id,
                retryable=False,
            )
            log.error("ai-monitor request failed path={} method={} error={}", path, method, exc)
            raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc

        if response.status_code >= 400:
            invocation_error = ServiceInvocationError(
                serviceName=self.service_name,
                protocol="http",
                operation=f"{method} {path}",
                target=url,
                message=response.text or "调用 ai-service 监控接口失败",
                traceId=trace_id,
                code=response.status_code,
                retryable=response.status_code >= 500,
            )
            log.error(
                "ai-monitor bad response path={} method={} status={} body={}",
                path,
                method,
                response.status_code,
                response.text,
            )
            raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message())

        return response