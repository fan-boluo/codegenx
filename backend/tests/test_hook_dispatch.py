"""Hook 分发协议测试：serial 短路 / parallel 隔离 / waterfall 洋葱 / 决策 / once / condition。

运行：backend/.venv/Scripts/python.exe backend/tests/test_hook_dispatch.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.hook import (
    HookAction,
    HookContext,
    HookDecision,
    HookEvent,
    HookManager,
)


def _ctx(event: str) -> HookContext:
    return HookContext(event=event)


class TestSerialDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_order_and_none_passthrough(self):
        m = HookManager()
        order = []
        m.register(HookEvent.TURN_START, lambda c: order.append(1), name="a", priority=20)
        m.register(HookEvent.TURN_START, lambda c: order.append(2), name="b", priority=10)
        m.load_and_freeze()
        await m.emit(HookEvent.TURN_START, _ctx(HookEvent.TURN_START))
        self.assertEqual(order, [2, 1])

    async def test_blocked_short_circuit(self):
        """blocked 终止链，后续 hook 不执行。"""
        m = HookManager()
        ran = []

        def guard(c):
            return HookDecision.block("path 为空")

        def later(c):
            ran.append("later")

        m.register(HookEvent.BEFORE_TOOL_CALL, guard, name="guard", priority=10)
        m.register(HookEvent.BEFORE_TOOL_CALL, later, name="later", priority=20)
        m.load_and_freeze()
        ctx = _ctx(HookEvent.BEFORE_TOOL_CALL)
        await m.emit(HookEvent.BEFORE_TOOL_CALL, ctx)
        self.assertEqual(ctx.action, HookAction.BLOCKED)
        self.assertEqual(ctx.message, "path 为空")
        self.assertEqual(ran, [])

    async def test_inject_escalates_to_blocked(self):
        """action 只升不降：inject 之后 blocked 仍可覆盖。"""
        m = HookManager()

        def soft(c):
            return HookDecision.inject("提示")

        def hard(c):
            return HookDecision.block("拒绝")

        m.register(HookEvent.BEFORE_TOOL_CALL, soft, name="soft", priority=10)
        m.register(HookEvent.BEFORE_TOOL_CALL, hard, name="hard", priority=20)
        m.load_and_freeze()
        ctx = _ctx(HookEvent.BEFORE_TOOL_CALL)
        await m.emit(HookEvent.BEFORE_TOOL_CALL, ctx)
        self.assertEqual(ctx.action, HookAction.BLOCKED)

    async def test_dict_decision_compat(self):
        """兼容旧协议：dict {"action": "blocked"} 视同 HookDecision。"""
        m = HookManager()
        m.register(HookEvent.BEFORE_TOOL_CALL, lambda c: {"action": "blocked", "message": "old"},
                   name="old_style")
        m.load_and_freeze()
        ctx = _ctx(HookEvent.BEFORE_TOOL_CALL)
        await m.emit(HookEvent.BEFORE_TOOL_CALL, ctx)
        self.assertEqual((ctx.action, ctx.message), (HookAction.BLOCKED, "old"))

    async def test_error_policy_blocked_on_pre_event(self):
        """可短路事件上 hook 异常 → 按 blocked 处理（保持旧行为）。"""
        m = HookManager()

        def boom(c):
            raise ValueError("hook 挂了")

        m.register(HookEvent.BEFORE_TOOL_CALL, boom, name="boom")
        m.load_and_freeze()
        ctx = _ctx(HookEvent.BEFORE_TOOL_CALL)
        await m.emit(HookEvent.BEFORE_TOOL_CALL, ctx)
        self.assertEqual(ctx.action, HookAction.BLOCKED)
        self.assertTrue(any("boom" in e for e in ctx.errors))

    async def test_error_policy_raise_on_lifecycle(self):
        """lifecycle 事件（on_session_start）异常上抛。"""
        m = HookManager()

        def boom(c):
            raise RuntimeError("初始化失败")

        m.register(HookEvent.SESSION_START, boom, name="boom")
        m.load_and_freeze()
        with self.assertRaisesRegex(RuntimeError, "初始化失败"):
            await m.emit(HookEvent.SESSION_START, _ctx(HookEvent.SESSION_START))

    async def test_error_policy_swallow_on_turn_end(self):
        """on_turn_end（finally 语义）异常吞掉，不掩盖原始异常。"""
        m = HookManager()

        def boom(c):
            raise ValueError("收尾失败")

        m.register(HookEvent.TURN_END, boom, name="boom")
        m.load_and_freeze()
        ctx = _ctx(HookEvent.TURN_END)
        await m.emit(HookEvent.TURN_END, ctx)  # 不抛
        self.assertTrue(ctx.errors)


class TestParallelDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_all_run_and_errors_isolated(self):
        m = HookManager()
        ran = []

        async def boom(c):
            raise ValueError("boom")

        m.register(HookEvent.AFTER_TOOL_CALL, boom, name="boom", priority=10)
        m.register(HookEvent.AFTER_TOOL_CALL, lambda c: ran.append("ok"), name="ok", priority=20)
        m.load_and_freeze()
        ctx = _ctx(HookEvent.AFTER_TOOL_CALL)
        await m.dispatch(HookEvent.AFTER_TOOL_CALL, ctx)  # dispatch 按事件定义选 parallel
        self.assertEqual(ran, ["ok"])
        self.assertTrue(any("boom" in e for e in ctx.errors))
        self.assertEqual(ctx.action, HookAction.CONTINUE)  # 并行事件不短路


class TestWaterfallDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_onion_order(self):
        """洋葱：前序处理 → 内层 → 后序处理。"""
        m = HookManager()
        order = []

        async def outer(ctx, call_next):
            order.append("outer_pre")
            result = await call_next()
            order.append("outer_post")
            return result

        m.register(HookEvent.BEFORE_BUILD, outer, name="outer")
        m.load_and_freeze()
        result = await m.waterfall(HookEvent.BEFORE_BUILD, _ctx(HookEvent.BEFORE_BUILD),
                                   inner=lambda: "CORE")
        self.assertEqual(result, "CORE")
        self.assertEqual(order, ["outer_pre", "outer_post"])

    async def test_short_circuit_without_call_next(self):
        """不调用 call_next：该层返回值成为链结果，inner 不执行。"""
        m = HookManager()
        inner_ran = []

        async def short(ctx, call_next):
            return "SHORT"

        m.register(HookEvent.BEFORE_BUILD, short, name="short")
        m.load_and_freeze()
        result = await m.waterfall(
            HookEvent.BEFORE_BUILD, _ctx(HookEvent.BEFORE_BUILD),
            inner=lambda: inner_ran.append(1) or "CORE",
        )
        self.assertEqual(result, "SHORT")
        self.assertEqual(inner_ran, [])

    async def test_layer_error_skips_to_next(self):
        """waterfall 层异常 → 跳过该层继续下游（SKIP 策略）。"""
        m = HookManager()

        async def boom(ctx, call_next):
            raise ValueError("层挂了")

        async def ok(ctx, call_next):
            return (await call_next()) + "+ok"

        m.register(HookEvent.BEFORE_BUILD, boom, name="boom", priority=10)
        m.register(HookEvent.BEFORE_BUILD, ok, name="ok", priority=20)
        m.load_and_freeze()
        ctx = _ctx(HookEvent.BEFORE_BUILD)
        result = await m.waterfall(HookEvent.BEFORE_BUILD, ctx, inner=lambda: "CORE")
        self.assertEqual(result, "CORE+ok")
        self.assertTrue(ctx.errors)

    async def test_pre_step_short_circuit_skips_onion(self):
        """事件上注册的 serial 型 hook 作为前置步骤，其 blocked 短路整个洋葱。"""
        m = HookManager()

        def guard(c):
            return {"action": "blocked", "message": "组装被拦截"}

        m.register(HookEvent.BEFORE_BUILD, guard, name="guard", mode="serial")
        m.load_and_freeze()
        inner_ran = []
        ctx = _ctx(HookEvent.BEFORE_BUILD)
        result = await m.waterfall(
            HookEvent.BEFORE_BUILD, ctx,
            inner=lambda: inner_ran.append(1) or "CORE",
        )
        self.assertIsNone(result)
        self.assertEqual(ctx.action, HookAction.BLOCKED)
        self.assertEqual(inner_ran, [])


class TestHookOptions(unittest.IsolatedAsyncioTestCase):
    async def test_condition_filter(self):
        m = HookManager()
        ran = []
        m.register(HookEvent.TURN_END, lambda c: ran.append(1), name="only_app1",
                   condition=lambda c: c.data.get("app_id") == 1)
        m.load_and_freeze()
        await m.emit(HookEvent.TURN_END, HookContext(event=HookEvent.TURN_END, data={"app_id": 2}))
        self.assertEqual(ran, [])
        await m.emit(HookEvent.TURN_END, HookContext(event=HookEvent.TURN_END, data={"app_id": 1}))
        self.assertEqual(ran, [1])

    async def test_once_auto_unregister(self):
        m = HookManager()
        ran = []
        m.register(HookEvent.TURN_END, lambda c: ran.append(1), name="one_shot", once=True)
        m.load_and_freeze()
        await m.emit(HookEvent.TURN_END, _ctx(HookEvent.TURN_END))
        await m.emit(HookEvent.TURN_END, _ctx(HookEvent.TURN_END))
        self.assertEqual(ran, [1])
        self.assertEqual(m.listeners(HookEvent.TURN_END), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
