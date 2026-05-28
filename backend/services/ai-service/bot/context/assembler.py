from __future__ import annotations

import datetime
import json
import platform
from functools import lru_cache
from pathlib import Path
from typing import Any
from bot.utils.context_utils import ensure_app_workdir
from bot.utils.log_utils import log
from shared.constants import get_memory_dir, get_code_dir
from prompt.runtime_prompt import DEFAULT_PROMPT_TEMPLATE, AUTO_MEMORY_PROMPT


class ContextAssembler:
    """Assemble model messages from the slim TurnContext payload."""
    base_prompt: str = ""

    # Custom persona / personality set by the user at runtime
    persona: str = ""

    # Injected by MemoryManager each turn
    memory_prompt: str = ""

    # Injected by SessionContext each turn from metadata
    # workspace_metadata_prompt: str = ""
    workspace_metadata:dict = {}

    # Appended by caller (e.g. skill frontmatter, plugin text)
    skill_prompt: str = ""

    # 任务看板
    task_prompt :str = ""
    # 会话记忆
    session_memory_prompt:str = ""

    # 自动记忆
    auto_memorize_prompt = AUTO_MEMORY_PROMPT

    extra :str = ""


    async def build_workspace(self,app_id:str):
        code_dir = ensure_app_workdir(app_id)
        safe_paths = [str(code_dir)]
        self.workspace_metadata = {
            "code_dir": str(code_dir),
            "safe_paths": list(safe_paths),
            "allowed_rw_dirs": safe_paths + [str(get_memory_dir(app_id))],
            "os_name": (platform.system() or "Windows").lower(),
            "project_skeleton": self.build_directory_skeleton(code_dir),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        }

        self.base_prompt = DEFAULT_PROMPT_TEMPLATE.format(code_dir=self.workspace_metadata.get("code_dir"))
        self.auto_memorize_prompt = AUTO_MEMORY_PROMPT.format(memoryDir=get_memory_dir(app_id), projectDir=get_code_dir(app_id))

    def build_extra(self) -> str:
        """ 提醒，需要更新任务看板了
        # TODO
        """
        parts = []
        if self.extra:
            parts.append(self.extra)
        if not parts:
            return None
        content = "<system-reminder>\n" + "\n".join(parts) + "\n</system-reminder>"
        return content


    def prepare_turn_context(self) -> str:
        """
        每个turn要构建de
        """
        # 开头是提示词
        parts = []
        if self.base_prompt:
            parts.append(self.base_prompt)
        if self.memory_prompt:
            parts.append(self.memory_prompt)
        if self.workspace_metadata:
            workspace_prompt = "## 项目工作区元数据\n"
            workspace_prompt += f"- 代码根目录：{self.workspace_metadata['code_dir']}\n"
            workspace_prompt += f"- 安全路径列表：{', '.join(self.workspace_metadata['safe_paths'])}\n"
            workspace_prompt += f"- 允许读写的目录：{', '.join(self.workspace_metadata['allowed_rw_dirs'])}\n"
            workspace_prompt += f"- 操作系统类型：{self.workspace_metadata['os_name']}\n"
            workspace_prompt += "- 项目目录结构：\n" + self.workspace_metadata["project_skeleton"] + "\n"
            workspace_prompt += "- 元数据生成时间：" + str(self.workspace_metadata["timestamp"])

            parts.append(workspace_prompt)
        if self.skill_prompt:
            parts.append(f"# 以下是你可以使用的技能：\n {self.skill_prompt}")
        if self.session_memory_prompt:
            parts.append(f"# 以下是提取的历史对话信息：\n {self.session_memory_prompt}")
        if self.task_prompt:
            parts.append(f"# 以下是任务看板：\n {self.task_prompt}")
        if self.auto_memorize_prompt:
            parts.append(self.auto_memorize_prompt)
        if self.extra:
            parts.append(self.build_extra())
        return "\n".join(parts)

    @staticmethod
    def build_directory_skeleton(root: Path, *, max_entries: int = 40, max_depth: int = 2) -> str:
        """当前项目目录结构 """
        if not root.exists():
            return ""

        _IGNORED_DIRS = {
            ".git", "node_modules", "__pycache__", ".data",
            "dist", "build", ".next", ".nuxt",
            "venv", ".venv", "env", ".env", ".tox",
            ".eggs", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        }

        lines: list[str] = []
        prefix_map = {0: "", 1: "  ", 2: "    "}

        def walk(current: Path, depth: int) -> None:
            if depth > max_depth or len(lines) >= max_entries:
                return
            try:
                children = sorted(
                    current.iterdir(),
                    key=lambda item: (item.is_file(), item.name.lower()),
                )
            except OSError as exc:
                log.warning("Failed to read project skeleton for {}: {}", current, exc)
                return

            indent = prefix_map.get(depth, "    " * depth)
            for child in children:
                if child.is_dir() and child.name in _IGNORED_DIRS:
                    continue
                prefix = indent + ("- " if depth > 0 else "")
                lines.append(prefix + child.name + ("/" if child.is_dir() else ""))
                if child.is_dir() and depth < max_depth:
                    walk(child, depth + 1)
                if len(lines) >= max_entries:
                    break

        walk(root, 0)
        return "\n".join(lines)


    @staticmethod
    def _normalize_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        将对话标准化，完成后长这样：
        [
    {  "role": "system",      "content": "你是助手"    },
    {  "role": "user",        "content": "123"    },
    {  "role": "assistant",    "content": "我在",   "tool_calls": [{"id": "1"}]    },
    {  "role": "tool",        "content": "晴天",    "tool_call_id": "1",    "name": "get_weather"    }
]
        """
        normalized: list[dict[str, Any]] = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue

            role = str(message.get("role", "")).strip()
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            payload = {
                "role": role,
                "content": str(message.get("content", "") or ""),
            }
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                payload["tool_calls"] = message.get("tool_calls")
            if role == "tool":
                payload["tool_call_id"] = str(message.get("tool_call_id", "") or "")
                payload["name"] = str(message.get("name", "") or "")
            normalized.append(payload)
        return normalized

    # def _ensure_user_message(self,user_input:str,history:list) -> None:
    #     """ 确保最后一个消息是用户输入的
    #     这样做的目的是什么，在多次step后，用户消息可能以及被冲掉了
    #     """
    #     if not user_input:
    #         return
    #     if history:
    #         last_message = history[-1]
    #         if isinstance(last_message, dict) and last_message.get("role") == "user" and str(
    #                 last_message.get("content", "") or "") == user_input:
    #             return
    #     history.append({"role": "user", "content": user_input})
    #     context.chat_history = history

    async def assemble(self,system_prompt:str,chat_messages:list) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        # 不加了
        # self.ensure_user_message(user_input,chat_messages)
        messages.extend(self._normalize_history(chat_messages))

        return messages



@lru_cache(maxsize=1)
def get_context_assembler() -> ContextAssembler:
    return ContextAssembler()