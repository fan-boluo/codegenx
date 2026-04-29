"""Model adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from shared.models.model import Model
from shared.models.model_provider import ModelProvider
from shared.schema.chat import ChatRequest, ChatResponse, StreamChunk


# 模型调用抽象类，定义三个方法，基本调用、流式、支持模型名称
class ModelAdapter(ABC):
    @abstractmethod
    async def invoke(self, model: Model, provider: ModelProvider, chat_request: ChatRequest) -> ChatResponse:
        raise NotImplementedError

    @abstractmethod
    async def invoke_stream_chunk(
        self, model: Model, provider: ModelProvider, chat_request: ChatRequest
    ) -> AsyncGenerator[StreamChunk, None]:
        raise NotImplementedError

    @abstractmethod
    def supports(self, provider_name: str) -> bool:
        raise NotImplementedError
