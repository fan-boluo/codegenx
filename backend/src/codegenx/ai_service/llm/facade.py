"""LLMFacade —— llm/ 调用层的生命周期与观测门面（组件化）。

调用面保持 llm/ 模块函数（resilient_invoke / get_llm），本门面只负责：
  - async_before_stop：统一释放 provider 级共享连接池（close_llm_clients）；
  - 观测聚合：全部熔断器状态快照（管理端点用）。

模型客户端预热不在门面做：使用模型的组件（AgentRuntime/CompactionService/
SessionSummaryService/MemoryScheduler）在各自 async_before_start 中预热
自己需要的模型客户端与熔断器，随组件生命周期启停。
"""
from __future__ import annotations

from contextlib import suppress

from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.system_app import SystemApp


class LLMFacade(BaseComponent):
    """LLM 调用层生命周期门面（不复制状态；业务代码继续直接调 llm/ 模块函数）。"""

    name = ComponentType.LLM_FACADE

    def init_app(self, system_app: SystemApp) -> None:
        self.system_app = system_app

    async def async_before_stop(self) -> None:
        # close_llm_clients 本身幂等（close 后清空注册表），残留调用无害
        with suppress(Exception):
            from codegenx.ai_service.llm.client_registry import close_llm_clients

            await close_llm_clients()

    def circuit_snapshot(self) -> dict[str, str]:
        """ 熔断器快照 """
        from codegenx.ai_service.llm.resilience import circuit_snapshot

        return circuit_snapshot()


def initialize_llm(system_app: SystemApp) -> LLMFacade:
    """注册 LLM 生命周期门面组件（system_app.initialize_components 调用）。"""
    return system_app.register(LLMFacade)
