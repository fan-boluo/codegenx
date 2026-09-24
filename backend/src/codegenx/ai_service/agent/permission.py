from __future__ import annotations

from dataclasses import dataclass
from typing import Any


READ_ONLY_TOOLS = {
    "read_file",
    "memory_search",
    "memory_get",
    "load_skill",
    "web_search",
}

WRITE_TOOLS = {
    "write_file",
    "edit_file",
    "write_short_term",
    "write_long_term",
    "write_identity_memory",
}


@dataclass
class PermissionDecision:
    behavior: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"behavior": self.behavior, "reason": self.reason}


class PermissionManager:
    def __init__(self, mode: str = "default"):
        if mode not in {"default", "plan", "auto"}:
            raise ValueError(f"Unknown permission mode: {mode}")
        self.mode = mode

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionDecision:
        decision = self._check_deny_rules(tool_name, tool_input)
        if decision is not None:
            return decision

        decision = self._check_mode(tool_name)
        if decision is not None:
            return decision

        decision = self._check_allow_rules(tool_name)
        if decision is not None:
            return decision

        return PermissionDecision("ask", f"No explicit permission rule matched for {tool_name}")

    def _check_deny_rules(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionDecision | None:
        if tool_name == "bash":
            command = str(tool_input.get("command", ""))
            denied_fragments = ["rm -rf /", "sudo ", "mkfs", "chown "]
            for fragment in denied_fragments:
                if fragment in command:
                    return PermissionDecision("deny", f"Blocked dangerous bash command pattern: {fragment}")

        for key in ("path", "filename", "src", "dest"):
            value = tool_input.get(key)
            if isinstance(value, str) and ("../" in value or "..\\" in value):
                return PermissionDecision("deny", f"Path traversal detected in argument {key}")

        return None

    def _check_mode(self, tool_name: str) -> PermissionDecision | None:
        if self.mode == "plan":
            if tool_name in WRITE_TOOLS or tool_name == "bash":
                return PermissionDecision("deny", "Plan mode blocks write and command-execution tools")
            return PermissionDecision("allow", "Plan mode auto-approves read-only tools")

        if self.mode == "auto" and (tool_name in READ_ONLY_TOOLS or tool_name == "read_file"):
            return PermissionDecision("allow", "Auto mode auto-approves read-only tools")

        return None

    def _check_allow_rules(self, tool_name: str) -> PermissionDecision | None:
        if tool_name in READ_ONLY_TOOLS:
            return PermissionDecision("allow", f"Tool {tool_name} is allowlisted as read-only")
        return None