"""
BashTool — 预留工具，暂不注册（不继承 BaseTool）。
后续如需启用，改为继承 BaseTool 并恢复执行逻辑即可。
"""
from __future__ import annotations

from typing import Any


# 去掉继承BaseTool，危险工具，不加载
class BashTool:
    """Shell 命令执行工具（预留，暂未启用）"""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def label(self) -> str:
        return "system"

    @property
    def description(self) -> str:
        return "Execute a shell command. (预留工具，暂未实现)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in seconds (optional)"},
            },
            "required": ["command"],
        }
