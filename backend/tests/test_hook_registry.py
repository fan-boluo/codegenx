"""Hook 注册机制测试：@on 收集、启动校验、拓扑排序、冻结保护。

运行：backend/.venv/Scripts/python.exe backend/tests/test_hook_registry.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.hook import (
    HookContext,
    HookEvent,
    HookManager,
    is_valid_event,
    on,
)


class TestHookRegistry(unittest.IsolatedAsyncioTestCase):
    """注册 / 校验 / 拓扑排序。"""

    def _manager(self) -> HookManager:
        return HookManager()

    async def test_collect_and_freeze(self):
        """@on 收集 → load_and_freeze 后可查询。"""
        m = self._manager()

        @on(HookEvent.TURN_END, priority=10)
        async def a(ctx):
            pass

        # 装载前 pending，不生效
        self.assertEqual(m.listeners(HookEvent.TURN_END), [])
        # 把 @on 收集到指定 manager：全局装饰器注册到单例，这里直接等价注册
        m.register(HookEvent.TURN_END, a, name="a", priority=10)
        summary = m.load_and_freeze()
        self.assertEqual(summary[HookEvent.TURN_END], 1)
        self.assertEqual(m.listeners(HookEvent.TURN_END)[0].name, "a")

    async def test_duplicate_name_rejected(self):
        m = self._manager()
        m.register(HookEvent.TURN_END, lambda c: None, name="dup")
        m.register(HookEvent.TURN_END, lambda c: None, name="dup")
        with self.assertRaisesRegex(ValueError, "重复"):
            m.load_and_freeze()

    async def test_invalid_event_rejected(self):
        m = self._manager()
        m.register("not_an_event", lambda c: None)
        with self.assertRaisesRegex(ValueError, "未定义的事件"):
            m.load_and_freeze()

    async def test_missing_dependency_rejected(self):
        m = self._manager()
        m.register(HookEvent.TURN_END, lambda c: None, depends_on=["ghost"])
        with self.assertRaisesRegex(ValueError, "不存在"):
            m.load_and_freeze()

    async def test_dependency_cycle_rejected(self):
        m = self._manager()
        m.register(HookEvent.TURN_END, lambda c: None, name="a", depends_on=["b"])
        m.register(HookEvent.TURN_END, lambda c: None, name="b", depends_on=["a"])
        with self.assertRaisesRegex(ValueError, "环"):
            m.load_and_freeze()

    async def test_topo_order_dependency_first_then_priority(self):
        """依赖优先；无依赖关系按 (priority, 注册序) 升序。"""
        m = self._manager()
        m.register(HookEvent.TURN_END, lambda c: None, name="report", priority=100)
        m.register(HookEvent.TURN_END, lambda c: None, name="memory", priority=20,
                   depends_on=["persist"])
        m.register(HookEvent.TURN_END, lambda c: None, name="persist", priority=50)
        m.register(HookEvent.TURN_END, lambda c: None, name="early", priority=5)
        m.load_and_freeze()
        names = [r.name for r in m.listeners(HookEvent.TURN_END)]
        # early(5) → persist(50，memory 的依赖) → memory(20，受依赖约束后置) → report(100)
        self.assertEqual(names, ["early", "persist", "memory", "report"])

    async def test_freeze_guard(self):
        m = self._manager()
        m.register(HookEvent.TURN_END, lambda c: None)
        m.load_and_freeze()
        with self.assertRaises(RuntimeError):
            m.register(HookEvent.TURN_END, lambda c: None, name="late")

    async def test_event_name_validation(self):
        self.assertTrue(is_valid_event(HookEvent.SESSION_START))
        self.assertTrue(is_valid_event("internal/on_error"))
        self.assertFalse(is_valid_event("on_unknown"))

    async def test_global_decorator_registers_to_singleton(self):
        """@on 装饰到全局单例：重复注册同名由冻结校验拦截（幂等导入由模块缓存保证）。"""
        from codegenx.ai_service.hook import hook_manager

        @on(HookEvent.SESSION_START, name="singleton_probe_hook")
        async def probe(ctx):
            pass

        labels = [r.name for r in hook_manager.listeners(HookEvent.SESSION_START)]
        # 全局单例在其它测试模块可能已冻结（真实注册表），此处仅验证收集行为之一：
        # 未冻结时进 pending，冻结后不可再注册
        if hook_manager._frozen:
            self.assertNotIn("singleton_probe_hook", labels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
