from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import httpx

from shared.config.config import get_settings
from shared.config.log_config import log
from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.monitor.monitor_context import MonitorContextHolder
from shared.schema.ai_service import (
    AiServiceErrorPayload,
    AiServiceGenerateRequest,
    AiServiceStopRequest,
    AiServiceStopResponse,
    AiServiceStreamChunk,
    AiServiceStreamMeta,
)


settings = get_settings()


@dataclass(slots=True)
class _SseEvent:
    event: str
    data: str


class AiServiceClient:
    def __init__(self) -> None:
        http_port = getattr(settings, "ai_service_http_port", None) or "8002"
        self.base_url = f"http://{settings.ai_service_host}:{http_port}"
        self.stream_url = f"{self.base_url}/internal/ai/codegen/stream"
        self.stop_url = f"{self.base_url}/internal/ai/codegen/stop"
        self.timeout_ms = int(settings.ai_timeout_seconds * 1000)
        self._stream_timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
        self._max_attempts = 2
        self._request_timeout = httpx.Timeout(10.0)

    async def generate_code_stream(
        self,
        *,
        user_message: str,
        code_gen_type: CodeGenTypeEnum | None = None,
        app_id: int,
        user_id: str | None = None,
        trace_id: str,
        request_id: str,
        session_id: str,
    ) -> AsyncGenerator[str, None]:
        resolved_trace_id, resolved_request_id, resolved_session_id = self._validate_call_context(
            trace_id=trace_id,
            request_id=request_id,
            session_id=session_id,
        )
        payload = AiServiceGenerateRequest(
            appId=app_id,
            userId=user_id,
            message=user_message,
            codeGenType=code_gen_type.value if code_gen_type else None,
            traceId=resolved_trace_id,
            requestId=resolved_request_id,
            sessionId=resolved_session_id,
        )
        started_at = time.perf_counter()
        chunk_count = 0
        first_chunk_latency_ms: int | None = None
        upstream_instance = self.base_url

        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._stream_timeout) as client:
                    async with client.stream(
                        "POST",
                        self.stream_url,
                        headers=self._build_headers(resolved_trace_id, resolved_request_id),
                        json=payload.model_dump(by_alias=True, exclude_none=True),
                    ) as response:
                        if response.status_code >= 400:
                            error_payload = await self._read_stream_error_payload(response)
                            if chunk_count == 0 and attempt < self._max_attempts and self._should_retry_status(response.status_code):
                                await asyncio.sleep(0.2 * attempt)
                                continue
                            raise BusinessException(error_payload.code, error_payload.message)

                        upstream_instance = response.headers.get("X-Upstream-Instance") or upstream_instance
                        self._update_context(upstream_instance=upstream_instance)

                        async for event in self._iter_sse_events(response):
                            if event.event == "meta":
                                meta = AiServiceStreamMeta.model_validate_json(event.data)
                                upstream_instance = meta.upstream_instance or upstream_instance
                                self._update_context(upstream_instance=upstream_instance)
                                continue

                            if event.event == "chunk":
                                chunk = AiServiceStreamChunk.model_validate_json(event.data)
                                chunk_count += 1
                                if first_chunk_latency_ms is None:
                                    first_chunk_latency_ms = int((time.perf_counter() - started_at) * 1000)
                                    self._update_context(
                                        upstream_instance=upstream_instance,
                                        first_chunk_latency_ms=first_chunk_latency_ms,
                                        chunk_count=chunk_count,
                                    )
                                else:
                                    self._update_context(chunk_count=chunk_count)
                                yield chunk.content
                                continue

                            if event.event == "error":
                                error_payload = AiServiceErrorPayload.model_validate_json(event.data)
                                raise BusinessException(error_payload.code, error_payload.message)

                            if event.event == "done":
                                break

                total_latency_ms = int((time.perf_counter() - started_at) * 1000)
                self._update_context(
                    upstream_instance=upstream_instance,
                    first_chunk_latency_ms=first_chunk_latency_ms,
                    total_latency_ms=total_latency_ms,
                    chunk_count=chunk_count,
                )
                log.info(
                    "ai-service stream traceId={} requestId={} upstreamInstance={} firstChunkLatencyMs={} totalLatencyMs={} chunkCount={} attempt={}",
                    resolved_trace_id,
                    resolved_request_id,
                    upstream_instance,
                    first_chunk_latency_ms,
                    total_latency_ms,
                    chunk_count,
                    attempt,
                )
                return
            except BusinessException:
                raise
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                if chunk_count > 0 or attempt >= self._max_attempts:
                    raise BusinessException(ErrorCode.SYSTEM_ERROR, f"调用 AI 服务流式生成失败: {exc}") from exc
                log.warning(
                    "ai-service stream retry traceId={} requestId={} attempt={} error={}",
                    resolved_trace_id,
                    resolved_request_id,
                    attempt,
                    exc,
                )
                await asyncio.sleep(0.2 * attempt)

        raise BusinessException(ErrorCode.SYSTEM_ERROR, "调用 AI 服务流式生成失败")

    async def stop_generation(
        self,
        *,
        app_id: int,
        user_id: str | None,
        trace_id: str,
        request_id: str,
        session_id: str,
        reason: str | None = None,
        grace_seconds: float | None = None,
    ) -> AiServiceStopResponse:
        payload = AiServiceStopRequest(
            appId=app_id,
            userId=user_id,
            sessionId=session_id,
            traceId=trace_id,
            requestId=request_id,
            reason=reason,
            graceSeconds=grace_seconds,
        )
        async with httpx.AsyncClient(timeout=self._request_timeout) as client:
            response = await client.post(
                self.stop_url,
                headers=self._build_headers(trace_id, request_id),
                json=payload.model_dump(by_alias=True, exclude_none=True),
            )
        if response.status_code >= 400:
            error_payload = self._parse_error_payload(response)
            raise BusinessException(error_payload.code, error_payload.message)
        return AiServiceStopResponse.model_validate(response.json())

    @staticmethod
    def _validate_call_context(*, trace_id: str | None, request_id: str | None, session_id: str | None) -> tuple[str, str, str]:
        resolved_trace_id = str(trace_id or "").strip()
        resolved_request_id = str(request_id or "").strip()
        resolved_session_id = str(session_id or "").strip()
        if not resolved_trace_id:
            raise ValueError("trace_id is required")
        if not resolved_request_id:
            raise ValueError("request_id is required")
        if not resolved_session_id:
            raise ValueError("session_id is required")

        context = MonitorContextHolder.get_context()
        if context is not None:
            context.trace_id = resolved_trace_id
            context.request_id = resolved_request_id

        return resolved_trace_id, resolved_request_id, resolved_session_id

    @staticmethod
    def _build_headers(trace_id: str, request_id: str) -> dict[str, str]:
        return {
            "X-Trace-Id": trace_id,
            "X-Idempotency-Key": request_id,
            "Accept": "text/event-stream, application/json",
        }

    @staticmethod
    def _should_retry_status(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _parse_error_payload(self, response: httpx.Response) -> AiServiceErrorPayload:
        try:
            body = response.json()
        except ValueError:
            return AiServiceErrorPayload(
                code=ErrorCode.SYSTEM_ERROR.get_code(),
                message=response.text or "AI 服务返回异常",
                upstreamInstance=response.headers.get("X-Upstream-Instance") or self.base_url,
                retryable=self._should_retry_status(response.status_code),
            )

        if "code" in body and "message" in body and "traceId" in body:
            return AiServiceErrorPayload.model_validate(body)
        return AiServiceErrorPayload(
            code=int(body.get("code", ErrorCode.SYSTEM_ERROR.get_code())),
            message=body.get("message") or response.text or "AI 服务返回异常",
            traceId=body.get("traceId"),
            requestId=body.get("requestId"),
            upstreamInstance=body.get("upstreamInstance") or response.headers.get("X-Upstream-Instance") or self.base_url,
            retryable=bool(body.get("retryable", self._should_retry_status(response.status_code))),
        )

    async def _read_stream_error_payload(self, response: httpx.Response) -> AiServiceErrorPayload:
        body_bytes = await response.aread()
        try:
            body = json.loads(body_bytes.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return AiServiceErrorPayload(
                code=ErrorCode.SYSTEM_ERROR.get_code(),
                message=body_bytes.decode("utf-8", errors="ignore") or "AI 服务返回异常",
                upstreamInstance=response.headers.get("X-Upstream-Instance") or self.base_url,
                retryable=self._should_retry_status(response.status_code),
            )
        if "code" in body and "message" in body and "traceId" in body:
            return AiServiceErrorPayload.model_validate(body)
        return AiServiceErrorPayload(
            code=int(body.get("code", ErrorCode.SYSTEM_ERROR.get_code())),
            message=body.get("message") or "AI 服务返回异常",
            traceId=body.get("traceId"),
            requestId=body.get("requestId"),
            upstreamInstance=body.get("upstreamInstance") or response.headers.get("X-Upstream-Instance") or self.base_url,
            retryable=bool(body.get("retryable", self._should_retry_status(response.status_code))),
        )

    async def _iter_sse_events(self, response: httpx.Response):
        event_name = "message"
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line == "":
                if data_lines:
                    yield _SseEvent(event=event_name, data="\n".join(data_lines))
                event_name = "message"
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            yield _SseEvent(event=event_name, data="\n".join(data_lines))

    @staticmethod
    def _update_context(
        *,
        upstream_instance: str | None = None,
        first_chunk_latency_ms: int | None = None,
        total_latency_ms: int | None = None,
        route_latency_ms: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        context = MonitorContextHolder.get_context()
        if context is None:
            return
        if upstream_instance:
            context.upstream_instance = upstream_instance
        if first_chunk_latency_ms is not None:
            context.first_chunk_latency_ms = first_chunk_latency_ms
        if total_latency_ms is not None:
            context.total_latency_ms = total_latency_ms
        if route_latency_ms is not None:
            context.route_latency_ms = route_latency_ms
        if chunk_count is not None:
            context.chunk_count = chunk_count