from __future__ import annotations

from pydantic import Field

from shared.schema.common import CamelBaseModel


class AiServiceRouteRequest(CamelBaseModel):
    init_prompt: str = Field(alias="initPrompt")
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")


class AiServiceRouteResponse(CamelBaseModel):
    code_gen_type: str = Field(alias="codeGenType")
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")
    upstream_instance: str | None = Field(default=None, alias="upstreamInstance")
    timeout_ms: int = Field(alias="timeoutMs")
    idempotency_mode: str = Field(default="same-input", alias="idempotencyMode")


class AiServiceGenerateRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    message: str
    code_gen_type: str | None = Field(default=None, alias="codeGenType")
    session_id: str | None = Field(default=None, alias="sessionId")
    trace_id: str | None = Field(default=None, alias="traceId")
    request_id: str | None = Field(default=None, alias="requestId")


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