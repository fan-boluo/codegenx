from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import grpc as grpc_client

from services.discovery_adapter import discovery_adapter
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.service_invocation import ServiceInvocationError


T = TypeVar("T")
StubFactory = Callable[[grpc_client.aio.Channel], object]
RpcCallback = Callable[[object], Awaitable[T]]

settings = get_settings()


class GrpcServiceClientBase:
    def __init__(self, *, service_name: str, fallback_target: str, timeout_seconds: int | None = None, max_attempts: int | None = None) -> None:
        self.service_name = service_name
        self.fallback_target = fallback_target
        self.timeout_seconds = timeout_seconds or int(settings.gateway_grpc_timeout_seconds or 8)
        self.max_attempts = max_attempts or int(settings.gateway_grpc_max_attempts or 2)

    async def invoke(self, *, operation: str, stub_factory: StubFactory, callback: RpcCallback[T], trace_id: str | None = None) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            target = await discovery_adapter.resolve_grpc_target(self.service_name, fallback_target=self.fallback_target)
            started_at = time.perf_counter()
            try:
                async with grpc_client.aio.insecure_channel(target) as channel:
                    stub = stub_factory(channel)
                    response = await callback(stub)
                latency_ms = (time.perf_counter() - started_at) * 1000
                log.info(
                    "grpc request serviceName={} resolvedInstance={} operation={} latencyMs={:.2f} attempt={} traceId={}",
                    self.service_name,
                    target,
                    operation,
                    latency_ms,
                    attempt,
                    trace_id,
                )
                return response
            except grpc_client.aio.AioRpcError as exc:
                last_error = exc
                invocation_error = ServiceInvocationError(
                    serviceName=self.service_name,
                    protocol="grpc",
                    operation=operation,
                    target=target,
                    message=exc.details() or exc.code().name,
                    traceId=trace_id,
                    code=exc.code().name,
                    retryable=self._is_retryable(exc.code()),
                )
                log.warning(
                    "grpc request failed serviceName={} resolvedInstance={} operation={} code={} retryable={} attempt={} traceId={} message={}",
                    self.service_name,
                    target,
                    operation,
                    exc.code().name,
                    invocation_error.retryable,
                    attempt,
                    trace_id,
                    invocation_error.message,
                )
                if invocation_error.retryable and attempt < self.max_attempts:
                    discovery_adapter.invalidate(self.service_name, "grpc")
                    await asyncio.sleep(0.2 * attempt)
                    continue
                raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc
            except Exception as exc:
                last_error = exc
                invocation_error = ServiceInvocationError(
                    serviceName=self.service_name,
                    protocol="grpc",
                    operation=operation,
                    target=target,
                    message=str(exc),
                    traceId=trace_id,
                    retryable=False,
                )
                log.error(
                    "grpc request failed serviceName={} resolvedInstance={} operation={} attempt={} traceId={} error={}",
                    self.service_name,
                    target,
                    operation,
                    attempt,
                    trace_id,
                    exc,
                )
                raise BusinessException(ErrorCode.SYSTEM_ERROR, invocation_error.to_message()) from exc

        raise BusinessException(ErrorCode.SYSTEM_ERROR, f"调用 {self.service_name}(grpc) 失败: {last_error}")

    @staticmethod
    def _is_retryable(status_code: grpc_client.StatusCode) -> bool:
        return status_code in {grpc_client.StatusCode.UNAVAILABLE, grpc_client.StatusCode.DEADLINE_EXCEEDED}