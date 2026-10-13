"""验证 DateExploreAgent 初始化提示词构建成功。

运行:
  cd backend
  .venv/Scripts/python.exe -m pytest tests/test_data_explore_prompt.py -s
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.agent.agents.data_explore_agent import DateExploreAgent
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.utils.config import AgentConfig


# ── 辅助：构造最小 RuntimeSessionState ─────────────────────────────────────

def _make_session_state() -> RuntimeSessionState:
    """构造一个最小可用的 RuntimeSessionState（只需 session_id + request）。"""
    from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest

    request = AiServiceGenerateRequest(
        appId="test-app",
        userId="test-user",
        message="测试消息",
        sessionId="test-session",
        traceId="t1",
        requestId="r1",
    )
    return RuntimeSessionState(session_id="test-session", request=request)


# ── 测试 ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_date_explore_agent_prompt():
    """DateExploreAgent._prepare_agent 应成功构建含 SQL 上下文的 system prompt。"""

    # 1) 预解析 agent_config（模拟系统初始化时 cls.resolve_agent_config()）
    #    直接构造一个匹配 data_explore 的 AgentConfig 作为类属性
    fake_config = AgentConfig(
        id="data_explore",
        role="data_analysis",
        name="data_explore",
        model_name="qwen3.7-plus",
    )
    DateExploreAgent.agent_config = fake_config
    DateExploreAgent.id = "data_explore"

    # 2) 构造 agent 实例，mock 掉 __init__ 中对 SystemApp/LLM 的依赖
    with patch("codegenx.ai_service.agent.agents.base_react_agent.get_llm"), \
         patch("codegenx.ai_service.agent.agents.base_react_agent.get_breaker"), \
         patch("codegenx.ai_service.agent.agents.base_react_agent.get_app") as mock_app:

        mock_message_bus = MagicMock()
        mock_app.return_value.message_bus = mock_message_bus

        agent = DateExploreAgent()

    # 3) 验证 profile 继承正确
    assert agent.profile.role == "data_analysis"
    assert agent.profile.name == "data_explore"

    # 4) 调用 _prepare_agent 构建提示词
    session_state = _make_session_state()
    await agent._prepare_agent(session_state)

    # 5) 验证 system_prompt 非空且包含关键内容
    prompt = agent._system_prompt
    assert prompt, "system_prompt 不应为空"

    # Jinja2 应已渲染 {{type}} → mysql
    assert "mysql" in prompt, "prompt 应包含 mysql（由 _prepare_prompt_params 提供）"

    # Jinja2 应已渲染 {{database_info}} 中的表结构
    assert "users" in prompt, "prompt 应包含 users 表结构"
    assert "products" in prompt, "prompt 应包含 products 表结构"
    assert "orders" in prompt, "prompt 应包含 orders 表结构"

    # role 应被渲染
    assert "data_analysis" in prompt, "prompt 应包含角色 data_analysis"

    # 6) 打印完整提示词（-s 模式下可见）
    print("\n" + "=" * 60)
    print("DateExploreAgent system_prompt:")
    print("=" * 60)
    print(prompt)
    print("=" * 60)
