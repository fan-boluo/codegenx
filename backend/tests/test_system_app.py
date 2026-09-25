"""SystemApp 容器测试（docs/SystemApp架构设计.md §8 P0 验证）。

覆盖：
  - 未初始化 get_app() 快速失败（fail fast）；
  - init_app 默认装配（config + hook_manager）与幂等；
  - startup 幂等（重复调用只装配一次）；
  - shutdown 严格逆序（后台任务 → runtime → LLM → 基础设施）；
  - reset_app 卸载。
"""
from __future__ import annotations

import asyncio

import pytest

from codegenx.ai_service import system_app
from codegenx.ai_service.system_app import (
    LLMFacade,
    SystemApp,
    get_app,
    init_app,
    reset_app,
)


@pytest.fixture(autouse=True)
def _clean_container():
    """每个用例前后都卸载容器，避免跨用例污染。"""
    reset_app()
    yield
    reset_app()


# ── 测试替身 ─────────────────────────────────────────────────────────────────


class _StubHooks:
    def __init__(self, order: list[str]):
        self._order = order

    def load_and_freeze(self) -> dict[str, int]:
        self._order.append("hooks.freeze")
        return {}


class _StubLLM(LLMFacade):
    def __init__(self, order: list[str]):
        self._order = order

    async def preheat_default_model(self) -> None:
        self._order.append("llm.preheat")

    async def shutdown(self) -> None:
        self._order.append("llm.shutdown")

    def circuit_snapshot(self) -> dict[str, str]:
        return {}


class _StubMaintenance:
    def __init__(self, order: list[str]):
        self._order = order

    async def start_periodic_maintenance(self, *a, **kw) -> None:
        self._order.append("maintenance.start")

    async def stop_periodic_maintenance(self) -> None:
        self._order.append("maintenance.stop")


class _StubScheduler:
    def __init__(self, order: list[str]):
        self._order = order

    async def startup(self) -> None:
        self._order.append("scheduler.start")

    async def shutdown(self, grace: float = 10.0) -> None:
        self._order.append("scheduler.shutdown")


class _StubRuntime:
    def __init__(self, order: list[str]):
        self._order = order
        self.started = 0

    async def start(self) -> None:
        self.started += 1
        self._order.append("runtime.start")

    async def stop(self) -> None:
        self._order.append("runtime.stop")


class _StubApp(SystemApp):
    """截获基础设施与后台任务组件，单测不碰真实 redis/qdrant/mysql/维护任务。"""

    def __init__(self, order: list[str]):
        from codegenx.ai_service.utils.config import Config

        super().__init__(config=Config(), hooks=_StubHooks(order), llm=_StubLLM(order))
        self._order = order
        self.monitor_maintenance = _StubMaintenance(order)
        self.memory_scheduler = _StubScheduler(order)

    async def _startup_infra(self) -> None:
        self._order.append("infra.startup")

    async def _shutdown_infra(self) -> None:
        self._order.append("infra.shutdown")


# ── 用例 ─────────────────────────────────────────────────────────────────────


def test_get_app_fail_fast_before_init():
    with pytest.raises(RuntimeError, match="SystemApp 未初始化"):
        get_app()


def test_init_app_default_wiring():
    from codegenx.ai_service.hook import hook_manager
    from codegenx.ai_service.utils.config import config as app_config

    app = init_app()
    assert app.config is app_config
    assert app.hooks is hook_manager
    assert get_app() is app
    # 重复 init 返回同一容器（幂等）
    assert init_app() is app


def test_reset_app_unloads_container():
    init_app()
    reset_app()
    with pytest.raises(RuntimeError):
        get_app()


def test_startup_is_idempotent(monkeypatch):
    order: list[str] = []
    stub_runtime = _StubRuntime(order)

    # startup 内部为延迟 import，需在源模块上替换组件 getter/构造器
    import codegenx.ai_service.agent.runtime as runtime_mod
    import codegenx.ai_service.monitor.maintenance_service as maintenance_mod
    import codegenx.ai_service.schedule.memory as schedule_mod

    monkeypatch.setattr(runtime_mod, "AgentRuntime", lambda: stub_runtime)
    monkeypatch.setattr(
        maintenance_mod, "get_monitor_maintenance_service", lambda: app_monitor
    )
    monkeypatch.setattr(schedule_mod, "get_memory_scheduler", lambda: app_scheduler)

    app = _StubApp(order)
    app_monitor = app.monitor_maintenance
    app_scheduler = app.memory_scheduler
    system_app.init_app(app)
    asyncio.run(app.startup())

    assert app._started is True
    assert stub_runtime.started == 1
    assert app.runtime is stub_runtime
    # 第二次 startup：不重复装配
    asyncio.run(app.startup())
    assert stub_runtime.started == 1

    # 装配顺序：hook 冻结 → 基础设施 → runtime → 后台任务
    assert order.index("hooks.freeze") < order.index("infra.startup")
    assert order.index("infra.startup") < order.index("runtime.start")
    assert order.index("runtime.start") < order.index("maintenance.start")


def test_shutdown_reverse_order():
    order: list[str] = []
    app = _StubApp(order)
    app._started = True
    app.runtime = _StubRuntime(order)

    asyncio.run(app.shutdown())

    # 严格逆序：后台任务 → runtime → LLM → 基础设施
    assert order.index("maintenance.stop") < order.index("scheduler.shutdown")
    assert order.index("scheduler.shutdown") < order.index("runtime.stop")
    assert order.index("runtime.stop") < order.index("llm.shutdown")
    assert order.index("llm.shutdown") < order.index("infra.shutdown")
    assert app._started is False
    assert app.runtime is None


def test_shutdown_before_startup_is_noop():
    order: list[str] = []
    app = _StubApp(order)
    asyncio.run(app.shutdown())
    assert order == []


def test_validate_config_rejects_empty_model():
    from codegenx.ai_service.utils.config import AgentConfig, Config

    # 未配置 agents：回落默认 AgentConfig（model 有默认值），校验通过
    app = SystemApp(config=Config(agents=[]), hooks=_StubHooks([]), llm=_StubLLM([]))
    app._validate_config()

    # 默认智能体 model 被显式置空：拒绝带病启动
    app2 = SystemApp(
        config=Config(agents=[AgentConfig(model="")]),
        hooks=_StubHooks([]),
        llm=_StubLLM([]),
    )
    with pytest.raises(RuntimeError, match="model"):
        app2._validate_config()
