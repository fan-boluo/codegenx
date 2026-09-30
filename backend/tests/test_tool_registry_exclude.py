"""ToolRegistry 注册排除 + build_tool 行为验证。

config.tools.excluded 中的工具在 regist_tools 阶段即不注册（原为构建目录时过滤），
build_tool() 无参直接产出与已注册工具一致的目录。
"""
import asyncio

from codegenx.ai_service.agent import tool_handler
from codegenx.ai_service.agent.tool_handler import ToolRegistry


def _tool_names(registry: ToolRegistry) -> set[str]:
    return {t.name for t in registry.tools}


def test_excluded_tools_are_not_registered(monkeypatch):
    monkeypatch.setattr(tool_handler.config.tools, "excluded", ["read_file"])
    registry = ToolRegistry()
    names = _tool_names(registry)
    assert "read_file" not in names
    assert names, "excluded 之外的工具应正常注册"


def test_build_tool_catalog_matches_registered_tools():
    registry = ToolRegistry()
    catalog = asyncio.run(registry.build_tool())
    assert {item["function"]["name"] for item in catalog} == _tool_names(registry)
