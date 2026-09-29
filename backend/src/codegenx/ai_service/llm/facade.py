"""LLMFacade —— llm/ 调用层的生命周期与观测门面（组件化）。

调用面保持 llm/ 模块函数（resilient_invoke / get_llm），本门面只负责：
  - async_before_start：预热默认模型客户端（构造连接池，免首次请求冷启动）；
  - async_before_stop：统一释放 provider 级共享连接池（close_llm_clients）；
  - 观测聚合：全部熔断器状态快照（管理端点用）。
"""
from __future__ import annotations

from contextlib import suppress

from shared import log

from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.system_app import SystemApp


class LLMFacade(BaseComponent):
    """LLM 调用层生命周期门面（不复制状态；业务代码继续直接调 llm/ 模块函数）。"""

    name = ComponentType.LLM_FACADE

    def init_app(self, system_app: SystemApp) -> None:
        self.system_app = system_app

    async def async_before_start(self) -> None:
        await self.preheat_default_model()

    async def async_before_stop(self) -> None:
        # close_llm_clients 本身幂等（close 后清空注册表），残留调用无害
        with suppress(Exception):
            from codegenx.ai_service.llm.client_registry import close_llm_clients

            await close_llm_clients()

    async def preheat_default_model(self) -> None:
        """预热默认模型客户端（仅构造，不发起网络请求；失败不阻断启动）。"""
        with suppress(Exception):
            from codegenx.ai_service.llm.async_client import get_llm
            from codegenx.ai_service.utils.config import config as app_config

            model = app_config.get_default_agent().resolved_model_name
            if model:
                get_llm(model)
                log.info("LLM 默认模型客户端已预热: {}", model)

    def circuit_snapshot(self) -> dict[str, str]:
        """ 熔断器快照 """
        from codegenx.ai_service.llm.resilience import circuit_snapshot

        return circuit_snapshot()


def initialize_llm(system_app: SystemApp) -> LLMFacade:
    """注册 LLM 生命周期门面组件（system_app.initialize_components 调用）。"""
    return system_app.register(LLMFacade)
