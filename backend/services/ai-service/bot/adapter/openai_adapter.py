import time
import uuid
from typing import AsyncGenerator, Iterable

import httpx
from langchain_core.exceptions import LangChainException
from openai import AsyncOpenAI
import logging
from app.adapter.model_adapter import ModelAdapter
from app.core.config import get_settings
from app.core.constants import CHAT_OBJECT
from app.core.logging_config import setup_logging
from app.schemas.chat import ChatRequest, StreamChunk, ChatResponse, ChatMessage
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from app.models.model import Model
from app.models.model_provider import ModelProvider
from app.schemas.chat import ChatRequest

from schema.code import LLMContextBuildModel

# from tenacity import (
#     retry,
#     stop_after_attempt,  # 停止重试条件
#     wait_exponential,  # 指数退避
#     retry_if_exception_type, before_sleep_log,  # 只重试网络/超时类异常
# )
settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("app")
class OpenAIAdapter(ModelAdapter):
    supported_providers = {"openai", "qwen"}

    # def __init__(self):
    #     # ========== 全局连接池（只初始化一次，性能最高） ==========
    #     self.http_client = httpx.AsyncClient(
    #         limits=httpx.Limits(
    #             max_connections=200,  # 最大并发连接
    #             max_keepalive_connections=100,  # 长连接数量
    #             keepalive_expiry=10,  # 长连接保留时间
    #         ),
    #     )

    # @retry(
    #     # 指数退避：等待 1s → 2s → 4s → 8s（最大10s）
    #     wait=wait_exponential(multiplier=1, min=1, max=10),
    #     # 最多重试 3 次
    #     stop=stop_after_attempt(3),
    #     # 只重试网络错误、超时、服务端错误
    #     retry=(
    #             retry_if_exception_type(httpx.TransportError)
    #             | retry_if_exception_type(httpx.TimeoutException)
    #             | retry_if_exception_type(LangChainException)
    #     ),
    #     # 重试前打印日志
    #     before_sleep=before_sleep_log(logger, logging.WARNING),
    # )
    # async def _retry_llm_invoke(self, llm, messages):
    #     return await llm.ainvoke(messages)

    async def invoke(self, model: Model, provider: ModelProvider, context_model: LLMContextBuildModel) -> ChatResponse:
        llm = self._build_llm(model, provider, context_model, stream=False)
        # message: AIMessage = await self._retry_llm_invoke(llm,self._langchain_to_messages(chat_request.messages))
        message: AIMessage = await llm.ainvoke(self._to_openai_messages(context_model.message))
        usage = self._extract_token_usage(message)
        return ChatResponse(
            id=getattr(message, "id", None) or uuid.uuid4().hex,
            object=CHAT_OBJECT,
            created=int(time.time()),
            model=model.model_key,
            choices=[
                {
                    "index": 0,
                    "message": ChatMessage(role="assistant", content=self._normalize_text(message.content)),
                    "finishReason": self._extract_finish_reason(message),
                }
            ],
            usage={
                "promptTokens": usage["prompt_tokens"],
                "completionTokens": usage["completion_tokens"],
                "totalTokens": usage["total_tokens"],
            },
        )

    async def invoke_stream_chunk(
            self, model: Model, provider: ModelProvider, chat_request: ChatRequest
    ) -> AsyncGenerator[StreamChunk, None]:
        client = AsyncOpenAI(
            api_key=provider.api_key,
            base_url=self._resolve_base_url(provider.base_url),
            timeout=model.default_timeout / 1000 if model.default_timeout else 60,
        )
        payload: dict = {
            "model": model.model_key,
            "messages": self._to_openai_messages(chat_request.messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if chat_request.temperature is not None:
            payload["temperature"] = chat_request.temperature
        if chat_request.max_tokens is not None:
            payload["max_tokens"] = chat_request.max_tokens
        extra_body = self._build_reasoning_extra_body(model, provider, chat_request)
        if extra_body:
            payload["extra_body"] = extra_body

        stream = await client.chat.completions.create(**payload)

        # @retry(
        #     wait=wait_exponential(multiplier=1, min=1, max=10),
        #     stop=stop_after_attempt(3),  # 最多重试3次
        #     retry=(
        #             retry_if_exception_type(httpx.TransportError)
        #             | retry_if_exception_type(httpx.TimeoutException)
        #             | retry_if_exception_type(Exception)  # 可根据需要缩小范围
        #     ),
        #     before_sleep=before_sleep_log(logger, logging.WARNING),
        # )
        # async def _create_stream():
        #     # 【连接池优化】使用全局 http_client
        #     client = AsyncOpenAI(
        #         api_key=provider.api_key,
        #         base_url=self._resolve_base_url(provider.base_url),
        #         timeout=model.default_timeout / 1000 if model.default_timeout else 60,
        #         http_client=self.http_client,# 使用连接池
        #     )
        #
        #     payload = {
        #         "model": model.model_key,
        #         "messages": self._to_openai_messages(chat_request.messages),
        #         "stream": True,
        #         "stream_options": {"include_usage": True},
        #     }
        #     if chat_request.temperature is not None:
        #         payload["temperature"] = chat_request.temperature
        #     if chat_request.max_tokens is not None:
        #         payload["max_tokens"] = chat_request.max_tokens
        #
        #     extra_body = self._build_reasoning_extra_body(model, provider, chat_request)
        #     if extra_body:
        #         payload["extra_body"] = extra_body
        #
        #     # 真正发起请求（会重试这里）
        #     return await client.chat.completions.create(**payload)

        # stream = await _create_stream()
        async for chunk in stream:
            text = ""
            reasoning = ""
            prompt_tokens = None
            completion_tokens = None

            usage_obj = getattr(chunk, "usage", None)
            if usage_obj is not None:
                # print(chunk['usage'])
                prompt_tokens = getattr(usage_obj, "prompt_tokens", None)
                completion_tokens = getattr(usage_obj, "completion_tokens", None)

            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                if delta is not None:
                    print(delta)
                    text = getattr(delta, "content", "") or ""
                    reasoning = getattr(delta, "reasoning_content", "") or ""

            stream_chunk = StreamChunk(
                text=text or None,
                reasoningContent=reasoning or None,
                promptTokens=prompt_tokens or None,
                completionTokens=completion_tokens or None,
                empty=not bool(text or reasoning or prompt_tokens or completion_tokens),
            )
            if not stream_chunk.empty:
                yield stream_chunk

    def supports(self, provider_name: str) -> bool:
        return provider_name.lower() in self.supported_providers

    # 统一构建为langchain_openai形式的
    def _build_llm(
        self, model: Model, provider: ModelProvider, context_model: LLMContextBuildModel, stream: bool
    ) -> ChatOpenAI:
        kwargs: dict = {
            "api_key": provider.api_key,
            "base_url": self._resolve_base_url(provider.base_url),
            "model": model.model_key,
            "streaming": stream,
            "timeout": model.default_timeout / 1000 if model.default_timeout else 60,
            # "http_async_client": self.http_client,
        }
        if context_model.max_tokens is not None:
            kwargs["max_tokens"] = context_model.max_tokens
        model_kwargs: dict = {}
        if stream:
            model_kwargs["stream_options"] = {"include_usage": True}
        extra_body = self._build_reasoning_extra_body(model, provider, context_model)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs
        return ChatOpenAI(**kwargs)

    @staticmethod
    def _langchain_to_messages(items: Iterable[ChatMessage]):
        messages = []
        for item in items:
            if item.role == "system":
                messages.append(SystemMessage(content=item.content))
            elif item.role == "assistant":
                messages.append(AIMessage(content=item.content))
            else:
                messages.append(HumanMessage(content=item.content))
        return messages

    # 解析模型响应，获取token消耗
    @staticmethod
    def _extract_token_usage(message: AIMessage) -> dict[str, int]:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        usage_metadata = getattr(message, "usage_metadata", None) or {}
        if usage_metadata:
            usage["prompt_tokens"] = int(usage_metadata.get("input_tokens", 0) or 0)
            usage["completion_tokens"] = int(usage_metadata.get("output_tokens", 0) or 0)
            usage["total_tokens"] = int(usage_metadata.get("total_tokens", 0) or 0)
        response_metadata = getattr(message, "response_metadata", None) or {}
        token_usage = response_metadata.get("token_usage", {}) if isinstance(response_metadata, dict) else {}
        usage["prompt_tokens"] = usage["prompt_tokens"] or int(token_usage.get("prompt_tokens", 0) or 0)
        usage["completion_tokens"] = usage["completion_tokens"] or int(token_usage.get("completion_tokens", 0) or 0)
        usage["total_tokens"] = usage["total_tokens"] or int(token_usage.get("total_tokens", 0) or 0)
        if usage["total_tokens"] == 0:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return usage
    @staticmethod
    def _resolve_base_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if "dashscope.aliyuncs.com/compatible-mode" in normalized and not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        if "api.deepseek.com" in normalized and not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        if "open.bigmodel.cn/api/paas" in normalized and not normalized.endswith("/v4"):
            normalized = f"{normalized}/v4"
        return normalized

    # 基于不同厂商构建启用思考的参数
    @staticmethod
    def _build_reasoning_extra_body(
            model: Model, provider: ModelProvider, context_model: LLMContextBuildModel
    ) -> dict:
        if not (model.support_reasoning == 1 and context_model.enable_reasoning):
            return {}
        provider_name = provider.provider_name.lower()
        if provider_name in {"qwen", "dashscope", "tongyi", "aliyun"}:
            return {"enable_thinking": True}
        if provider_name in {"zhipu", "zhipuai", "glm"}:
            return {"thinking": {"type": "enabled"}}
        return {}

    @staticmethod
    def _normalize_text(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return str(content)

    @staticmethod
    def _extract_finish_reason(message: AIMessage) -> str | None:
        metadata = getattr(message, "response_metadata", {}) or {}
        value = metadata.get("finish_reason")
        return str(value) if value is not None else None

    @staticmethod
    def _to_openai_messages(items: Iterable[ChatMessage]) -> list[dict[str, str]]:
        return [{"role": item.role, "content": item.content} for item in items]