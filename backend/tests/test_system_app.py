"""SystemApp 容器测试（docs/SystemApp架构设计.md v2 组件化验证）。

覆盖：
  - 未初始化 get_app() 快速失败（fail fast）；
  - init_app 默认装配（15 组件 + hook 冻结）与幂等；
  - 类型化访问器缺组件时 ValueError；
  - start_app/stop_app 幂等；
  - 生命周期广播顺序（启动按注册序 / 关闭按注册逆序）与关闭异常隔离；
  - runtime 启动校验（空 model 拒绝带病启动）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from codegenx.ai_service import system_app
from codegenx.ai_service.component import BaseComponent
from codegenx.ai_service.system_app import (
    SystemApp,
    get_app,
    init_app,
    reset_app,
    start_app,
    stop_app,
)


@pytest.fixture(autouse=True)
def _clean_container():
    """每个用例前后都卸载容器，避免跨用例污染。

    注意：hook 注册表全局唯一且模块级监听器只在 import 时收集，
    这里绝不能调 hook_manager.reset()（会永久清空监听器）。
    """
    reset_app()
    yield
    reset_app()


# ── 测试替身 ─────────────────────────────────────────────────────────────────


class _StubComponent(BaseComponent):
    """记录生命周期钩子调用顺序的桩组件。"""

    def __init__(self, name: str, order: list[str], boom_on_stop: bool = False):
        self.name = name
        self._order = order
        self._boom = boom_on_stop

    def init_app(self, system_app) -> None:
        self.system_app = system_app

    def on_init(self) -> None:
        self._order.append(f"{self.name}.on_init")

    def before_start(self) -> None:
        self._order.append(f"{self.name}.before_start")

    async def async_before_start(self) -> None:
        self._order.append(f"{self.name}.async_before_start")

    def before_stop(self) -> None:
        self._order.append(f"{self.name}.before_stop")

    async def async_before_stop(self) -> None:
        if self._boom:
            raise RuntimeError(f"{self.name} stop failed")
        self._order.append(f"{self.name}.async_before_stop")


def _stub_app(order: list[str], names: tuple[str, ...] = ("c1", "c2", "c3")) -> SystemApp:
    """裸容器 + 预置桩组件（_components_ready=True 跳过真实装配，只测广播）。"""
    app = SystemApp()
    app._components_ready = True
    for i, name in enumerate(names):
        app.register_instance(_StubComponent(name, order, boom_on_stop=(i == 1)))
    return app


# ── 用例 ─────────────────────────────────────────────────────────────────────


def test_get_app_fail_fast_before_init():
    with pytest.raises(RuntimeError, match="SystemApp 未初始化"):
        get_app()


def test_init_app_default_wiring():
    from codegenx.ai_service.hook import hook_manager

    app = init_app()
    assert app._components_ready is True
    assert get_app() is app
    # hook 注册表已冻结（监听器随组件 import 链收集完毕）
    assert hook_manager._frozen is True
    # 重复 init 返回同一容器且不重复装配（幂等）
    assert init_app() is app
    assert len(app.components) == 15


def test_reset_app_unloads_container():
    init_app()
    reset_app()
    with pytest.raises(RuntimeError):
        get_app()


def test_default_assembly_includes_all_components():
    """回归：默认装配必须含全部 15 个组件（防访问器悬空）。"""
    app = init_app()
    for accessor in (
        "llm", "tools", "skills", "agents", "context", "session_io", "tasks",
        "memory", "summary", "compaction", "chat_messages", "monitor",
        "monitor_maintenance", "memory_scheduler", "runtime",
    ):
        assert getattr(app, accessor) is not None, accessor


def test_accessor_missing_component_raises_value_error():
    """未注册即访问 → ValueError（fail fast，宁可显式失败）。"""
    with pytest.raises(ValueError, match="No component found"):
        SystemApp().runtime


def test_start_stop_broadcast_order_and_idempotency():
    """启动按注册序、关闭按注册逆序；重复 start/stop 均幂等。"""
    order: list[str] = []
    app = _stub_app(order)

    init_app(app)  # 同步钩子：c1 → c2 → c3
    assert order.index("c1.on_init") < order.index("c2.on_init") < order.index("c3.on_init")

    asyncio.run(start_app(app))  # 异步钩子：c1 → c2 → c3
    assert order.index("c1.async_before_start") < order.index("c2.async_before_start")
    assert order.index("c2.async_before_start") < order.index("c3.async_before_start")
    assert app._started is True

    n = len(order)
    asyncio.run(start_app(app))  # 幂等：不重复广播
    assert len(order) == n

    asyncio.run(stop_app(app))  # 逆序：c3 → c2 → c1
    assert order.index("c3.before_stop") < order.index("c2.before_stop")
    assert order.index("c2.before_stop") < order.index("c1.before_stop")
    assert app._started is False

    n = len(order)
    asyncio.run(stop_app(app))  # 幂等：stop_event 已置位
    assert len(order) == n


def test_stop_exception_isolated_per_component():
    """关闭时单组件异常不阻断其余组件收尾（c2 抛错，c1/c3 照常执行）。"""
    order: list[str] = []
    app = _stub_app(order)  # c2（index 1）的 async_before_stop 会抛错

    asyncio.run(start_app(app))
    order.clear()
    asyncio.run(stop_app(app))

    assert "c3.before_stop" in order
    assert "c1.before_stop" in order
    assert "c2.async_before_stop" not in order  # c2 抛错被隔离记录
    # 异常组件之后的组件（逆序更早注册的 c1）仍完成收尾
    assert order.index("c3.before_stop") < order.index("c1.before_stop")


def test_stop_before_start_is_noop():
    """未成功启动过的容器 stop 直接返回（不广播、不碰基础设施）。"""
    order: list[str] = []
    app = _stub_app(order)
    asyncio.run(stop_app(app))
    assert order == []


def test_runtime_rejects_empty_default_model(monkeypatch):
    """默认智能体 model 为空 → runtime 启动校验拒绝带病启动（迁自旧 SystemApp）。"""
    from codegenx.ai_service.utils.config import AgentConfig

    import codegenx.ai_service.agent.runtime as runtime_mod

    init_app()  # 组件就绪，AgentRuntime 构造依赖 tools 注册表
    fake_config = SimpleNamespace(get_default_agent=lambda: AgentConfig(model=""))
    monkeypatch.setattr(runtime_mod, "config", fake_config)

    rt = runtime_mod.AgentRuntime()
    with pytest.raises(RuntimeError, match="model"):
        asyncio.run(rt.async_before_start())


def test_system_app_module_reexports_framework():
    """框架再导出：组件模块从 system_app 或 component 导入等价。"""
    from codegenx.ai_service.component import BaseComponent as BC2
    from codegenx.ai_service.component import ComponentType as CT2

    assert system_app.BaseComponent is BC2
    assert system_app.ComponentType is CT2
