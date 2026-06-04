import asyncio
import json
from shared.config.log_config import log
from typing import AsyncGenerator, Dict, Any, List, Optional
from openai import AsyncOpenAI
from bot.utils.config import load_config


def _safe_build_tool_calls(tool_calls_buffer: dict) -> list[dict[str, Any]]:
    """安全构建 tool_calls 列表，每个 tool_call 的 arguments 做容错 JSON 解析。"""
    tool_calls_list = []
    for _, v in sorted(tool_calls_buffer.items()):
        args = {}
        raw_args = str(v.get("arguments", "") or "").strip()
        if raw_args:
            try:
                args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError) as exc:
                log.warning(
                    "Failed to parse tool call arguments for %s, using raw string: %s",
                    v.get("name", "unknown"), exc,
                )
                args = {"_raw_arguments": raw_args}
        tool_calls_list.append({
            "id": v["id"],
            "name": v["name"],
            "arguments": args,
        })
    return tool_calls_list

class AsyncLLMClient:
    """Async LLM Client wrapping OpenAI's Async interface."""
    def __init__(self, model_name: Optional[str] = None):
        config = load_config()
        agent_config = config.get_default_agent()
        provider_config = config.get_provider(agent_config.provider)
        fallback_provider = config.providers.dashscope

        self.api_key = provider_config.api_key or fallback_provider.api_key or config.providers.custom.api_key or ""
        self.model_base_url = provider_config.api_base or fallback_provider.api_base or config.providers.custom.api_base or "https://api.openai.com/v1"
        self.model_name = model_name or agent_config.resolved_model_name or "qwen-plus"
        
        self.client = AsyncOpenAI(
            api_key=self.api_key.strip(),
            base_url=self.model_base_url.strip()
        )
        log.info(f"Init AsyncLLMClient with base_url={self.model_base_url}, model={self.model_name}")

    async def invoke(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> str:
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            completion = await self.client.chat.completions.create(**kwargs)
            if not completion.choices:
                return ""

            message = completion.choices[0].message
            return message.content or ""
        except Exception as e:
            log.error(f"LLM Invoke Error: {e}")
            raise e

    async def invoke_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        timeout: Optional[float] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Yields parsed chunks of execution:
        - {"type": "content", "data": "chunk_string"}
        - {"type": "tool_calls", "data": [{"id": "...", "name": "...", "arguments": "{...}"}]}

        Args:
            timeout: 整个流式调用的超时秒数。超时后抛出 asyncio.TimeoutError。
        """
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        if tools:
            # Drop empty tools list or None to avoid OpenAI validation errors
            kwargs["tools"] = tools

        try:
            stream = await self.client.chat.completions.create(**kwargs)

            tool_calls_buffer = {}
            finish_reason = None

            try:
                # 可选超时包装
                if timeout is not None:
                    stream_iterator = stream.__aiter__()

                    async def _stream_with_timeout():
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    stream_iterator.__anext__(), timeout=min(timeout, 120.0)
                                )
                            except StopAsyncIteration:
                                return
                            yield chunk

                    chunk_source = _stream_with_timeout()
                else:
                    chunk_source = stream

                async for chunk in chunk_source:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                    # Stream Content Chunks directly
                    if delta.content:
                        yield {"type": "content", "data": delta.content}

                    # Accumulate Tool Calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {
                                    "id": tc.id or "",
                                    "name": tc.function.name if tc.function else "",
                                    "arguments": tc.function.arguments if tc.function and tc.function.arguments else ""
                                }
                            else:
                                if tc.function and tc.function.arguments:
                                    tool_calls_buffer[idx]["arguments"] += tc.function.arguments

            except (asyncio.TimeoutError, asyncio.CancelledError):
                # 流中断时仍然返回已累积的 tool_calls 和 finish_reason
                log.warning("LLM stream interrupted (timeout/cancel), returning partial result")
                if tool_calls_buffer:
                    tool_calls_list = _safe_build_tool_calls(tool_calls_buffer)
                    if tool_calls_list:
                        yield {"type": "tool_calls", "data": tool_calls_list}
                if finish_reason:
                    yield {"type": "response_info", "data": {"finish_reason": finish_reason}}
                raise

            # Yield accumulated tools at the end of stream
            if tool_calls_buffer:
                tool_calls_list = _safe_build_tool_calls(tool_calls_buffer)
                if tool_calls_list:
                    yield {"type": "tool_calls", "data": tool_calls_list}

            if finish_reason:
                yield {"type": "response_info", "data": {"finish_reason": finish_reason}}

        except Exception as e:
            log.error(f"LLM Stream Error: {e}")
            raise e