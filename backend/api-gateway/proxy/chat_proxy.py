"""Chat-service HTTP proxy for API gateway."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi.responses import StreamingResponse

from services.discovery_adapter import discovery_adapter
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.service_invocation import ServiceInvocationError


settings = get_settings()


def _build_forward_headers(authorization: str | None, trace_id: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return headers


class ChatProxy:
    """Proxy class that forwards requests to chat-service HTTP endpoints."""

    def __init__(self) -> None:
        self.service_name = settings.chat_service_name
        self.base_url = f"http://{settings.chat_service_host}:{settings.chat_service_http_port}"
        self._timeout = httpx.Timeout(120.0)

    async def resolve_base_url(self) -> str:
        return await discovery_adapter.resolve_http_base_url(self.service_name, fallback_base_url=self.base_url)

    async def stream_sse(
        self,
        *,
        method: str,
        path: str,
        authorization: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> StreamingResponse:
        started_at = time.perf_counter()

        async def event_stream():
            first_chunk_latency_ms: int | None = None
            upstream_instance = await self.resolve_base_url()
            upstream_url = f"{upstream_instance}{path}"
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        method=method,
                        url=upstream_url,
                        headers=_build_forward_headers(authorization, trace_id),
                        params=params,
                        json=json_body,
                    ) as response:
                        response.raise_for_status()
                        upstream_instance = response.headers.get("X-Upstream-Instance") or upstream_instance
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            if first_chunk_latency_ms is None:
                                first_chunk_latency_ms = int((time.perf_counter() - started_at) * 1000)
                            yield chunk
            except httpx.HTTPStatusError as exc:
                body = await exc.response.aread()
                detail = body.decode("utf-8", errors="ignore") or "调用聊天服务失败"
                log.error(
                    "chat-service stream bad response path={} method={} status={} traceId={} upstreamInstance={} body={}",
                    path,
                    method,
                    exc.response.status_code,
                    trace_id,
                    upstream_instance,
                    detail,
                )
                payload = json.dumps(
                    {
                        "error": True,
                        "message": "生成过程中出现错误，请稍后重试。",
                        "traceId": trace_id,
                    },
                    ensure_ascii=False,
                )
                yield f"event: business-error\ndata: {payload}\n\n"
                yield "event: done\ndata: \n\n"
            except Exception as exc:
                log.error(
                    "chat-service stream failed path={} method={} traceId={} upstreamInstance={} error={}",
                    path,
                    method,
                    trace_id,
                    upstream_instance,
                    exc,
                )
                payload = json.dumps(
                    {
                        "error": True,
                        "message": "生成过程中出现错误，请稍后重试。",
                        "traceId": trace_id,
                    },
                    ensure_ascii=False,
                )
                yield f"event: business-error\ndata: {payload}\n\n"
                yield "event: done\ndata: \n\n"
            finally:
                total_latency_ms = int((time.perf_counter() - started_at) * 1000)
                log.info(
                    "chat-service stream path={} method={} traceId={} upstreamInstance={} firstChunkLatencyMs={} totalLatencyMs={}",
                    path,
                    method,
                    trace_id,
                    upstream_instance,
                    first_chunk_latency_ms,
                    total_latency_ms,
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

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
                log.error("chat-service request failed path={} method={} error={}", path, method, exc)
                raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000
        log.info(
            "chat-service request serviceName={} resolvedInstance={} path={} method={} latencyMs={:.2f} traceId={}",
            self.service_name,
            base_url,
            path,
            method,
            latency_ms,
            trace_id,
        )

        if response.status_code >= 400:
            detail = response.text or "调用聊天服务失败"
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
                "chat-service bad response path={} method={} status={} body={}",
                path,
                method,
                response.status_code,
                detail,
            )
            raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message())

        try:
            return response.json()
        except Exception as exc:
            log.error("chat-service invalid json path={} method={} body={}", path, method, response.text)
            invocation_error = ServiceInvocationError(
                serviceName=self.service_name,
                protocol="http",
                operation=f"{method} {path}",
                target=url,
                message="聊天服务返回格式错误",
                traceId=trace_id,
                retryable=False,
            )
            raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc