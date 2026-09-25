"""Provider 级 AsyncOpenAI 客户端注册表（P0-1：连接池复用）。

进程内每个 provider 只创建一个 AsyncOpenAI（其底层 httpx.AsyncClient 即连接池），
全项目所有 LLM 调用共享同一实例，TCP/TLS 连接真正可复用；
修复原 AsyncLLMClient 即用即弃、从不 close 导致的连接零复用与句柄泄漏。
应用退出时在 lifespan 中调用 close_llm_clients() 统一释放。
"""
from __future__ import annotations

import contextlib
from typing import Dict

import httpx
from openai import AsyncOpenAI
from shared import log

from codegenx.ai_service.utils.config import ProviderConfig

# provider 名 → 共享客户端
_clients: Dict[str, AsyncOpenAI] = {}


def get_openai_client(provider_name: str, provider_config: ProviderConfig) -> AsyncOpenAI:
    """取（或创建）provider 级共享 AsyncOpenAI 客户端。

    - max_retries=0：关闭 SDK 内部隐式重试，避免与上层业务重试叠加放大（P1-4）；
      重试统一收口到 llm/errors.py 分类后的韧性层。
    - default_headers：透传 extra_headers（修复 AiHubMix APP-Code 等配置静默失效，P2-7）。
    """
    key = (provider_name or "custom").strip().lower()
    client = _clients.get(key)
    if client is not None:
        return client

    # P2-10：坏配置快速失败——api_key 缺失时给出带 provider 名的明确报错，
    # 而不是等第一次请求 401 时抛 SDK 通用错误
    if not provider_config.api_key.strip():
        raise ValueError(
            f"LLM provider '{key}' 的 api_key 未配置；"
            f"请在 config.json 的 providers.{key}.apiKey 补齐后重启"
        )

    client = AsyncOpenAI(
        api_key=provider_config.api_key.strip(),
        base_url=(provider_config.api_base or "").strip() or None,
        default_headers=provider_config.extra_headers or None,
        # connect 收紧到 5s：连不上的服务快速失败才能及时触发重试/降级；
        # read 放宽到 300s，兼容长思考模型在两个 chunk 之间的长间隔
        timeout=httpx.Timeout(600.0, read=300.0, write=30.0, connect=5.0, pool=10.0),
        max_retries=0,
    )
    _clients[key] = client
    log.info("LLM shared client created: provider={}", key)
    return client


async def close_llm_clients() -> None:
    """应用退出时统一关闭共享客户端（释放连接池）。"""
    for key, client in list(_clients.items()):
        with contextlib.suppress(Exception):
            await client.close()
        log.debug("LLM shared client closed: provider={}", key)
    _clients.clear()
