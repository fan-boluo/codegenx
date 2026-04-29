from __future__ import annotations

from pydantic import AliasChoices, Field

from shared.schema.common import CamelBaseModel


class ChatRequest(CamelBaseModel):
    """用户发起聊天的请求模型。"""

    app_id: int | None = Field(default=None, alias="appId")
    model: str | None = None
    messages: str | None = Field(default=None, alias="messages")
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(
        default=None,
        alias="maxTokens",
        validation_alias=AliasChoices("max_tokens", "maxTokens"),
    )
    enable_reasoning: bool | None = Field(
        default=None,
        alias="enableReasoning",
        validation_alias=AliasChoices("enable_reasoning", "enableReasoning"),
    )
    routing_strategy: str | None = Field(
        default=None,
        alias="routingStrategy",
        validation_alias=AliasChoices("routing_strategy", "routingStrategy"),
    )
    code_type: str | None = Field(default=None, alias="codeType")

# ------------------------非流式返回-------------------------------
class ChatMessage(CamelBaseModel):
    role: str
    content: str


# 模型返回choices，虽然返回多个，但只选择一个
class ChatChoice(CamelBaseModel):
    index: int
    message: ChatMessage
    finish_reason: str | None = Field(default=None, alias="finishReason")


# token消耗记录，输入：prompt_tokens，输出，总计
class ChatUsage(CamelBaseModel):
    prompt_tokens: int = Field(alias="promptTokens")
    completion_tokens: int = Field(alias="completionTokens")
    total_tokens: int = Field(alias="totalTokens")


# llm返回数据
class ChatResponse(CamelBaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: list[ChatChoice]  # 返回的消息
    usage: ChatUsage  # token成本

# ------------------------流式返回-------------------------------
# 流式响应的数据块
class StreamChunk(CamelBaseModel):
    text: str | None = None
    reasoning_content: str | None = Field(default=None, alias="reasoningContent")
    prompt_tokens: int | None = Field(default=None, alias="promptTokens")
    completion_tokens: int | None = Field(default=None, alias="completionTokens")
    empty: bool = False


class StreamDelta(CamelBaseModel):
    role: str | None = None
    content: str | None = None
    reasoning_content: str | None = Field(default=None, alias="reasoningContent")


class StreamChoice(CamelBaseModel):
    index: int
    delta: StreamDelta
    finish_reason: str | None = Field(default=None, alias="finishReason")


class StreamResponse(CamelBaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]
