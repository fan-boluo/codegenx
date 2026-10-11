"""Agent 间消息收发测试：验证 IntentionRecognitionAgent 与 PlannerAgent 互相发送消息.

测试要点：
1. 双方注册收件箱后可正常收发。
2. send 返回 True / False 与是否注册对应。
3. receive 阻塞 / 非阻塞 / 超时三种模式。
4. request_id 复用传递正确。
"""

import asyncio
import pytest
from typing import Any
from unittest.mock import MagicMock, patch

from codegenx.ai_service.bus.queue import MessageBus
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.intention_agent import IntentionRecognitionAgent
from codegenx.ai_service.agent.agents.plan_agent import PlannerAgent


class _FakeMessageBus(MessageBus):
    """绕过 ABC 检查的最小 MessageBus 实现."""

    def init_app(self, system_app):
        pass


class _FakeAgentContext:
    """仅提供消息收发所需的最低上下文."""

    session_id = "sess_123"
    app_id = "app_456"
    user_id = "user_789"

    @property
    def publish_context(self):
        return {
            "session_id": self.session_id,
            "request_id": "req_abc",
            "step_counter": 0,
        }


def _make_fake_app(bus: MessageBus) -> MagicMock:
    """构造最小 fake SystemApp，仅暴露 message_bus."""
    app = MagicMock()
    app.message_bus = bus
    return app


def _patch_agent(agent: Any, bus: MessageBus) -> None:
    """将 agent 的 message_bus 替换为独立的 fake bus，并注入最小上下文."""
    agent.message_bus = bus
    agent._inbox = None
    agent.agent_context = _FakeAgentContext()


@pytest.fixture
def bus():
    return _FakeMessageBus()


@pytest.fixture
def patched_agents(bus):
    """实例化两个 agent 并 patch message_bus，返回 (intent, planner)."""
    fake_app = _make_fake_app(bus)
    with patch(
        "codegenx.ai_service.agent.agents.base_react_agent.get_app", return_value=fake_app
    ):
        intent = IntentionRecognitionAgent()
        planner = PlannerAgent()
    # __init__ 中 get_app 被 patch，此处再补写 fake bus 与上下文
    _patch_agent(intent, bus)
    _patch_agent(planner, bus)
    return intent, planner


@pytest.mark.asyncio
async def test_intent_to_planner_send_receive(patched_agents):
    """Intention -> Planner 发送 task_assign，Planner 接收并回复 plan_ready."""
    intent_agent, planner_agent = patched_agents

    # 双方注册收件箱
    intent_agent._ensure_inbox()
    planner_agent._ensure_inbox()

    # Intention 发送计划任务给 Planner
    ok = await intent_agent.send(
        recipient="planner",
        message_type="task_assign",
        payload={"goal": "生成销售分析报告", "tables": ["orders", "products"]},
        request_id="req_001",
        message="请规划分析步骤",
    )
    assert ok is True

    # Planner 接收消息
    msg = await planner_agent.receive(timeout=1.0)
    assert msg is not None
    assert msg.sender == "intent_recognition"
    assert msg.recipient == "planner"
    assert msg.message_type == "task_assign"
    assert msg.request_id == "req_001"
    assert msg.payload["goal"] == "生成销售分析报告"

    # Planner 回复计划就绪
    ok2 = await planner_agent.send(
        recipient="intent_recognition",
        message_type="plan_ready",
        payload={"steps": ["数据探索", "指标计算", "报告生成"]},
        request_id="req_001",
        message="计划已生成",
    )
    assert ok2 is True

    # Intention 接收回复
    reply = await intent_agent.receive(timeout=1.0)
    assert reply is not None
    assert reply.sender == "planner"
    assert reply.message_type == "plan_ready"
    assert reply.request_id == "req_001"


@pytest.mark.asyncio
async def test_receive_non_blocking_and_timeout(patched_agents):
    """验证非阻塞（timeout=0）返回 None，超时（timeout=0.1）返回 None."""
    _, planner_agent = patched_agents
    planner_agent._ensure_inbox()

    # 收件箱为空，非阻塞立即返回 None
    msg = await planner_agent.receive(timeout=0)
    assert msg is None

    # 收件箱为空，超时后返回 None
    msg = await planner_agent.receive(timeout=0.1)
    assert msg is None


@pytest.mark.asyncio
async def test_send_to_unregistered_agent(patched_agents):
    """向未注册的 agent 发送消息应返回 False."""
    intent_agent, _ = patched_agents
    intent_agent._ensure_inbox()

    ok = await intent_agent.send(
        recipient="ghost_agent",
        message_type="task_assign",
        payload={},
        request_id="req_002",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_broadcast_message(patched_agents):
    """广播消息应被多个已注册 agent 接收."""
    intent_agent, planner_agent = patched_agents
    intent_agent._ensure_inbox()
    planner_agent._ensure_inbox()

    ok = await intent_agent.send(
        recipient="*",
        message_type="status_update",
        payload={"status": "running"},
        request_id="req_003",
    )
    assert ok is True

    # 两个 agent 都应收到广播
    msg_i = await intent_agent.receive(timeout=1.0)
    msg_p = await planner_agent.receive(timeout=1.0)
    assert msg_i is not None and msg_i.message_type == "status_update"
    assert msg_p is not None and msg_p.message_type == "status_update"
    assert msg_i.request_id == "req_003"
    assert msg_p.request_id == "req_003"


@pytest.mark.asyncio
async def test_request_id_reuse(patched_agents):
    """验证 request_id 复用：收发链路中 request_id 保持一致."""
    intent_agent, planner_agent = patched_agents
    intent_agent._ensure_inbox()
    planner_agent._ensure_inbox()

    await intent_agent.send(
        recipient="planner",
        message_type="task_assign",
        payload={},
        request_id="shared_req_42",
    )
    msg = await planner_agent.receive(timeout=1.0)
    assert msg is not None
    assert msg.request_id == "shared_req_42"


if __name__ == "__main__":
    # 直接运行：`uv run python tests/test_agent_messaging.py`
    _bus = _FakeMessageBus()
    _fake_app = _make_fake_app(_bus)
    with patch(
        "codegenx.ai_service.agent.agents.base_react_agent.get_app", return_value=_fake_app
    ):
        _intent = IntentionRecognitionAgent()
        _planner = PlannerAgent()
    _patch_agent(_intent, _bus)
    _patch_agent(_planner, _bus)

    async def _run_all():
        _intent._ensure_inbox()
        _planner._ensure_inbox()

        # 双向收发
        await _intent.send("planner", "task_assign", {"goal": "demo"}, request_id="req_demo")
        msg = await _planner.receive(timeout=1.0)
        assert msg.sender == "intent_recognition" and msg.request_id == "req_demo"
        await _planner.send("intent_recognition", "plan_ready", {"steps": ["s1"]}, request_id="req_demo")
        reply = await _intent.receive(timeout=1.0)
        assert reply.sender == "planner" and reply.request_id == "req_demo"
        print("  [PASS] intent <-> planner 双向收发")

        # 非阻塞
        assert await _planner.receive(timeout=0) is None
        print("  [PASS] 非阻塞 receive")

        # 未注册返回 False
        assert await _intent.send("ghost", "t", {}) is False
        print("  [PASS] 未注册返回 False")

        # 广播
        await _intent.send("*", "status", {"s": "ok"}, request_id="req_bc")
        assert (await _intent.receive(timeout=1.0)).message_type == "status"
        assert (await _planner.receive(timeout=1.0)).message_type == "status"
        print("  [PASS] 广播收发")

    asyncio.run(_run_all())
    print("all tests passed")
