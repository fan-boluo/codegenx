from __future__ import annotations

from typing import Any

from pydantic import Field

from shared.schema.common import CamelBaseModel


class AiServiceGenerateRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    user_id: str = Field(default="userx", alias="userId")
    message: str
    session_id: str = Field(default="", alias="sessionId")
    trace_id: str  = Field(default="", alias="traceId")
    request_id: str = Field(default="", alias="requestId")
    client_version: str = Field(default="ai-service", alias="clientVersion")
    db_name: str | None = Field(default=None, alias="dbName")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AiServiceStopRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    user_id: str | None = Field(default=None, alias="userId")
    session_id: str = Field(alias="sessionId")
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")
    reason: str | None = None
    grace_seconds: float | None = Field(default=None, alias="graceSeconds")


class AiServiceStopResponse(CamelBaseModel):
    accepted: bool
    session_id: str = Field(alias="sessionId")
    stopped_request_count: int = Field(alias="stoppedRequestCount")
    dropped_request_count: int = Field(alias="droppedRequestCount")
    active_request_ids: list[str] = Field(default_factory=list, alias="activeRequestIds")
    dropped_request_ids: list[str] = Field(default_factory=list, alias="droppedRequestIds")
    active_step_ids: list[str] = Field(default_factory=list, alias="activeTurnIds")


class AiServiceStreamMeta(CamelBaseModel):
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")
    upstream_instance: str | None = Field(default=None, alias="upstreamInstance")
    timeout_ms: int = Field(alias="timeoutMs")
    idempotency_mode: str = Field(default="best-effort", alias="idempotencyMode")


class AiServiceStreamChunk(CamelBaseModel):
    content: str
    index: int


class AiServiceStreamDone(CamelBaseModel):
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")
    total_chunks: int = Field(alias="totalChunks")


class AiServiceErrorPayload(CamelBaseModel):
    code: int
    message: str
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")
    upstream_instance: str | None = Field(default=None, alias="upstreamInstance")
    retryable: bool = False
