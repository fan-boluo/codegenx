"""P3 单测：ToolRegistry.child_view 过滤视图 + idle 会话 swap-out（§6/§7）。

运行：backend/.venv/Scripts/python.exe -m pytest backend/tests/test_p3_subagent_swap.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.agent.session_pool import SessionPool
from codegenx.ai_service.agent.runtime import AgentRuntime
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.agent.tool_handler import ToolRegistry
from codegenx.ai_service.tools.base import BaseTool, ToolResult


# ── ToolRegistry.child_view ─────────────────────────────────────────────────

class _DummyTool(BaseTool):
    def __init__(self, tool_name: str):
        self._name = tool_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def label(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "dummy tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict, signal=None) -> ToolResult:
        return ToolResult(success=True, data="ok")


def _registry_with(*names: str) -> ToolRegistry:
    reg = ToolRegistry.__new__(ToolRegistry)  # 绕过目录扫描
    reg.tools = [_DummyTool(n) for n in names]
    return reg


def test_child_view_excluded_and_allowed():
    reg = _registry_with("read_file", "write_file", "subagent", "compact")

    view = reg.child_view(excluded={"subagent", "compact"})
    assert [t.name for t in view.tools] == ["read_file", "write_file"]

    view2 = reg.child_view(excluded={"subagent", "compact"}, allowed=["read_file"])
    assert [t.name for t in view2.tools] == ["read_file"]

    # allowed=None 表示不白名单裁剪；空列表=全裁掉（显式指定即生效）
    assert len(reg.child_view(excluded={"compact"}).tools) == 3
    assert reg.child_view(allowed=["read_file", "compact"]).tools[0].name == "read_file"


def test_child_view_no_rescan_and_parent_untouched():
    reg = _registry_with("a", "b", "c")
    view = reg.child_view(excluded={"a"})

    # 视图过滤不影响父注册表
    assert [t.name for t in reg.tools] == ["a", "b", "c"]
    # 浅拷贝：共享同一批 Tool 对象（executor 复用，不重复实例化）
    assert view.tools[0] is reg.tools[1]


# ── SessionPool swap-out ────────────────────────────────────────────────────

def _make_pool_session(chat_len: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        closed=False,
        processing=False,
        active_tasks={},
        worker_task=None,
        last_activity_at=time.time() - 1000,  # 闲置 1000s
        swapped_out=False,
        context_manager=SimpleNamespace(
            chat_messages=[{"role": "user", "content": f"m{i}"} for i in range(chat_len)],
        ),
    )


def _make_pool(**kwargs) -> SessionPool:
    return SessionPool(
        idle_timeout_seconds=3600,
        swap_idle_seconds=300,
        **kwargs,
    )


def test_swap_out_unloads_idle_quiescent_session():
    pool = _make_pool()
    session = _make_pool_session()
    pool._sessions["s1"] = session

    asyncio.run(pool._cleanup_inactive_sessions())

    assert session.swapped_out is True
    assert session.context_manager.chat_messages == []
    assert "s1" in pool._sessions  # 会话对象保留池中（不等 close）


def test_swap_out_skips_busy_session():
    """processing / 活跃任务未完成 / worker 在跑 → 不卸载。"""
    busy_processing = _make_pool_session()
    busy_processing.processing = True
    busy_task = _make_pool_session()
    busy_task.active_tasks = {"r1": _PendingTask()}
    busy_worker = _make_pool_session()
    busy_worker.worker_task = _PendingTask()

    pool = _make_pool()
    for i, s in enumerate([busy_processing, busy_task, busy_worker]):
        pool._sessions[f"s{i}"] = s

    asyncio.run(pool._cleanup_inactive_sessions())

    for i, s in enumerate([busy_processing, busy_task, busy_worker]):
        assert s.swapped_out is False, f"busy session s{i} 被误卸载"
        assert len(s.context_manager.chat_messages) == 3


class _PendingTask:
    """未完成任务桩：done() 恒 False（跨事件循环安全）。"""

    def done(self) -> bool:
        return False


def test_swap_out_disabled_when_zero():
    pool = _make_pool()
    pool.swap_idle_seconds = 0
    session = _make_pool_session()
    pool._sessions["s1"] = session

    asyncio.run(pool._cleanup_inactive_sessions())
    assert session.swapped_out is False


def test_swap_out_not_repeated():
    """已卸载的会话不重复处理（swapped_out 幂等）。"""
    pool = _make_pool()
    session = _make_pool_session()
    session.swapped_out = True
    pool._sessions["s1"] = session

    asyncio.run(pool._cleanup_inactive_sessions())
    assert "s1" in pool._sessions  # 不被 close 也不报错


# ── swap-out 恢复（runtime） ────────────────────────────────────────────────

def test_restore_swapped_session_reloads_snapshot(monkeypatch):
    """swapped_out 会话下次请求到来时从快照重载 chat_messages。"""
    import codegenx.ai_service.system_app as system_app_mod

    snapshot = [{"role": "user", "content": "历史消息"}]

    class _StubSessionIO:
        async def get_turn_chat_message_snapshot(self, *, user_id, app_id, session_id):
            assert (user_id, app_id, session_id) == ("u1", "a1", "s1")
            return snapshot

    class _StubApp:
        session_io = _StubSessionIO()

    monkeypatch.setattr(system_app_mod, "get_app", lambda: _StubApp())

    rt = AgentRuntime()
    request = SimpleNamespace(user_id="u1", app_id="a1", session_id="s1")
    session = RuntimeSessionState(session_id="s1", request=request)
    session.swapped_out = True
    session.context_manager = SimpleNamespace(chat_messages=[])

    asyncio.run(rt._restore_swapped_session(session))

    assert session.swapped_out is False
    assert session.context_manager.chat_messages == snapshot


def test_restore_skips_non_swapped_session():
    """非 swap 会话：恢复逻辑为空操作，不触发任何 IO。"""
    rt = AgentRuntime()
    request = SimpleNamespace(user_id="u1", app_id="a1", session_id="s1")
    session = RuntimeSessionState(session_id="s1", request=request)
    session.context_manager = SimpleNamespace(chat_messages=[{"role": "user", "content": "x"}])

    asyncio.run(rt._restore_swapped_session(session))

    assert session.swapped_out is False
    assert len(session.context_manager.chat_messages) == 1
