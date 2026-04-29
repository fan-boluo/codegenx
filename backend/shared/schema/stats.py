from datetime import datetime

from pydantic import field_serializer, Field

from shared.schema.common import LongIdModel, CamelBaseModel

# 每次调用大模型的日志VO
class RequestLogVO(LongIdModel):
    trace_id: str | None = Field(default=None, alias="traceId")
    user_id: int | None = Field(default=None, alias="userId")
    api_key_id: int | None = Field(default=None, alias="apiKeyId")
    model_id: int | None = Field(default=None, alias="modelId")
    request_model: str | None = Field(default=None, alias="requestModel")
    model_name: str | None = Field(default=None, alias="modelName")
    request_type: str | None = Field(default=None, alias="requestType")
    source: str | None = None
    prompt_tokens: int | None = Field(default=None, alias="promptTokens")
    completion_tokens: int | None = Field(default=None, alias="completionTokens")
    total_tokens: int | None = Field(default=None, alias="totalTokens")
    cost: float | None = None
    duration: int | None = None
    status: str | None = None
    error_message: str | None = Field(default=None, alias="errorMessage")
    error_code: str | None = Field(default=None, alias="errorCode")
    routing_strategy: str | None = Field(default=None, alias="routingStrategy")
    is_fallback: int | None = Field(default=None, alias="isFallback")
    client_ip: str | None = Field(default=None, alias="clientIp")
    user_agent: str | None = Field(default=None, alias="userAgent")
    create_time: datetime | None = Field(default=None, alias="createTime")
    update_time: datetime | None = Field(default=None, alias="updateTime")

    @field_serializer("user_id", "api_key_id", "model_id", when_used="json")
    def serialize_long_ids(self, value: int | None) -> str | None:
        return str(value) if value is not None else None


class TokenStatsVO(CamelBaseModel):
    total_tokens: int = Field(alias="totalTokens")