"""App-service HTTP proxy for API gateway."""

from __future__ import annotations

import time
from typing import Any

import httpx

from services.discovery_adapter import discovery_adapter
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.service_invocation import ServiceInvocationError


settings = get_settings()


class AppProxy:
    """Proxy class that forwards requests to app-service HTTP endpoints."""

    def __init__(self) -> None:
        self.service_name = settings.app_service_name
        self.base_url = f"http://{settings.app_service_host}:{settings.app_service_http_port}"
        self._timeout = httpx.Timeout(120.0)

    async def resolve_base_url(self) -> str:
        return await discovery_adapter.resolve_http_base_url(self.service_name, fallback_base_url=self.base_url)

    async def build_url(self, path: str) -> str:
        base_url = await self.resolve_base_url()
        return f"{base_url}{path}"

    async def request_json(
        self,
        *,
        method: str,
        path: str,
        authorization: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if authorization:
            headers["Authorization"] = authorization
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        started_at = time.perf_counter()
        base_url = await self.resolve_base_url()
        url = f"{base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
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
                log.error("app-service request failed path={} method={} error={}", path, method, exc)
                raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000
        log.info(
            "app-service request serviceName={} resolvedInstance={} path={} method={} latencyMs={:.2f} traceId={}",
            self.service_name,
            base_url,
            path,
            method,
            latency_ms,
            trace_id,
        )

        if response.status_code >= 400:
            detail = response.text or "调用应用服务失败"
            invocation_error = ServiceInvocationError(
                serviceName=self.service_name,
                protocol="http",
                operation=f"{method} {path}",
                target=url,
                message=detail,
                traceId=trace_id,
                code=response.status_code,
                retryable=response.status_code >= 500,
            )
            log.error(
                "app-service bad response path={} method={} status={} body={}",
                path,
                method,
                response.status_code,
                detail,
            )
            raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message())

        try:
            return response.json()
        except Exception as exc:
            log.error("app-service invalid json path={} method={} body={}", path, method, response.text)
            invocation_error = ServiceInvocationError(
                serviceName=self.service_name,
                protocol="http",
                operation=f"{method} {path}",
                target=url,
                message="应用服务返回格式错误",
                traceId=trace_id,
                retryable=False,
            )
            raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc
