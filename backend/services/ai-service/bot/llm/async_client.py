import json
import logging
from typing import AsyncGenerator, Dict, Any, List, Optional
from openai import AsyncOpenAI
from bot.utils.config import load_config

logger = logging.getLogger(__name__)

class AsyncLLMClient:
    """Async LLM Client wrapping OpenAI's Async interface."""
    def __init__(self, model_name: Optional[str] = None):
        config = load_config()
        agent_config = config.get_default_agent()
        provider_config = config.get_provider(agent_config.provider)
        fallback_provider = config.providers.dashscope

        self.api_key = provider_config.api_key or fallback_provider.api_key or config.providers.custom.api_key or ""
        self.model_base_url = provider_config.api_base or fallback_provider.api_base or config.providers.custom.api_base or "https://api.openai.com/v1"
        self.model_name = model_name or provider_config.model_name or agent_config.resolved_model_name or "qwen-plus"
        
        self.client = AsyncOpenAI(
            api_key=self.api_key.strip(),
            base_url=self.model_base_url.strip()
        )
        logger.info(f"Init AsyncLLMClient with base_url={self.model_base_url}, model={self.model_name}")

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
            logger.error(f"LLM Invoke Error: {e}")
            raise e

    async def invoke_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = 8192,
        temperature: float = 0.0
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Yields parsed chunks of execution:
        - {"type": "content", "data": "chunk_string"}
        - {"type": "tool_calls", "data": [{"id": "...", "name": "...", "arguments": "{...}"}]}
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
            
            async for chunk in stream:
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

            # Yield accumulated tools at the end of stream
            if tool_calls_buffer:
                tool_calls_list = []
                for _, v in sorted(tool_calls_buffer.items()):
                    tool_calls_list.append({
                        "id": v["id"],
                        "name": v["name"],
                        "arguments": json.loads(v["arguments"]) if v["arguments"] else {}
                    })
                yield {"type": "tool_calls", "data": tool_calls_list}

            if finish_reason:
                yield {"type": "response_info", "data": {"finish_reason": finish_reason}}
                
        except Exception as e:
            logger.error(f"LLM Stream Error: {e}")
            raise e