from __future__ import annotations

import json
import platform
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.runtime_schema import TurnContext, RuntimeSessionState
from bot.memory.memory_manager import get_memory_manager
from bot.skill.skill_loader import SkillLoader
from bot.utils.context_utils import ensure_app_workdir
from bot.utils.log_utils import log
from prompt.runtime_prompt import DEFAULT_PROMPT_TEMPLATE
from shared.schema.ai_service import AiServiceGenerateRequest


class ContextAssembler:
    """Assemble model messages from the slim TurnContext payload."""

    async def build_tool(self,tools:list,exclude_tools:list) -> list[dict[str, Any]]:
        tool_catalog = [tool for tool in tools if tool.name not in exclude_tools]
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tool_catalog or []
        ]

    async def build_skill(self,skills:list) -> list[dict[str, Any]]:
        return [
            {
                "name": skill.name,
                "description": str(skill.metadata.get("description", "") or "").strip(),
            }
            for skill in skills
            if getattr(skill, "name", None)
        ]

    def build_memory(self, memory) -> str:
        """将 list[MemorySearchResult] 按 s9 分组（user/feedback/project/reference）转为清晰可读的字符串。
        过滤掉 todo（任务状态，不属于跨会话记忆）。
        """
        if not memory:
            return "暂无跨会话记忆"

        # s9 分组映射：语义类型 → 展示组别
        _CATEGORY_GROUP = {
            "preference": "user",
            "identity": "user",
            "feedback": "feedback",
            "decision": "project",
            "fact": "project",
            "principle": "project",
            "reference": "reference",
        }
        _GROUP_LABEL = {
            "user": "用户偏好 / User",
            "feedback": "反馈记录 / Feedback & Corrections",
            "project": "项目约定 / Project Facts",
            "reference": "外部资源 / References",
        }
        _GROUP_ORDER = ["user", "feedback", "project", "reference"]

        groups: dict[str, list] = {g: [] for g in _GROUP_ORDER}
        for item in memory:
            category = str(item.category or "").lower()
            if category == "todo":  # s9: task state 不进记忆 prompt
                continue
            group = _CATEGORY_GROUP.get(category, "project")
            groups[group].append(item)

        lines = [
            "# 跨会话记忆（只包含无法从当前代码直接推导的信息）",
            "",
        ]
        has_content = False
        for group_key in _GROUP_ORDER:
            items = groups[group_key]
            if not items:
                continue
            has_content = True
            lines.append(f"## [{_GROUP_LABEL[group_key]}]")
            for item in items:
                importance_str = f"{float(item.importance or 0):.2f}" if item.importance is not None else "无"
                lines.append(f"- [{item.type.value}] 重要度={importance_str}  {item.text or item.snippet}")
            lines.append("")

        if not has_content:
            return "暂无跨会话记忆"
        return "\n".join(lines)

    async def build_fix_context(self,session: RuntimeSessionState):
        """
        工作区、skill、tool这些是固定的
        """
        runtime = session.runtime
        request = session.request
        code_dir = ensure_app_workdir(session.request.app_id)
        safe_paths = [str(code_dir)]
        workspace_metadata = {
            "code_dir": str(code_dir),
            "safe_paths": list(safe_paths),
            "allowed_rw_dirs": list(safe_paths),
            "os_name": (platform.system() or "Windows").lower(),
            "project_skeleton": self.build_directory_skeleton(code_dir),
            "code_gen_type": str(session.request.code_gen_type or "")
        }

        # tool — 使用 tool_executor 的工具列表（子任务时会过滤掉 task 等工具，保持和执行器一致）
        build_tool = await self.build_tool(runtime.tool_registry.tools, runtime.config.tools.excluded)
        # skill
        build_skill = await self.build_skill(runtime.skills)

        session.workspace_metadata = workspace_metadata
        session.tool = build_tool
        session.skill = build_skill



    async def prepare_turn_context(self, session: RuntimeSessionState, context: TurnContext) -> None:
        """
        每个turn要构建的
        memory \ plann \ chat_history 都是随turn变化的
        session 的skill tool workspace 都是固定的
        """
        runtime = session.runtime
        request = session.request

        workspace_metadata = session.workspace_metadata
        prompt_template = DEFAULT_PROMPT_TEMPLATE.format("code_dir", workspace_metadata.get("code_dir"))

        # 开头是提示词
        parts = [prompt_template]

        workspace_prompt = "## 项目工作区元数据\n"
        workspace_prompt += f"- 代码根目录：{workspace_metadata['code_dir']}\n"
        workspace_prompt += f"- 安全路径列表：{', '.join(workspace_metadata['safe_paths'])}\n"
        workspace_prompt += f"- 允许读写的目录：{', '.join(workspace_metadata['allowed_rw_dirs'])}\n"
        workspace_prompt += f"- 操作系统类型：{workspace_metadata['os_name']}\n"
        workspace_prompt += f"- 代码生成类型：{workspace_metadata['code_gen_type']}\n"
        workspace_prompt += "- 项目目录结构：\n" + workspace_metadata["project_skeleton"]
        parts.append(workspace_prompt)

        tool_prompt = json.dumps(session.tool, ensure_ascii=False, indent=2)
        parts.append(f"以下是你可以使用的工具：\n{tool_prompt}")

        skill_prompt = json.dumps(session.skill, ensure_ascii=False, indent=2)
        parts.append(f"以下是你可以使用的技能：\n {skill_prompt}")

        # memory
        # s9: 用户说忽略记忆时，按记忆为空处理
        ignore_memory = bool(getattr(request, "ignore_memory", False))
        if ignore_memory:
            memory = []
        else:
            memory = await runtime.memory_manager.retrieve_user_query(request.user_id, str(request.app_id), request.message)
        build_memory_text = self.build_memory(memory)
        parts.append(f"以下是历史记忆：\n {build_memory_text}")

        # task board (s12) — replaces planner (s03)
        task_manager = getattr(runtime, "task_manager", None) or getattr(session, "task_manager", None)
        if task_manager is not None:
            plan_state = task_manager.get_board()
        else:
            plan_state = "No active tasks."
        parts.append(f"以下是任务看板：\n {plan_state}")

        system_prompt = "\n\n".join(parts)
        # 必要的信息
        context.workspace_metadata = session.workspace_metadata
        context.skill = session.skill
        context.tool = session.tool
        context.memory = memory
        context.plan_summary = plan_state
        context.system_prompt = system_prompt
        # TODO 聊天历史这里只是加了当前的请求
        context.chat_history = [
            {"role": "user", "content": str(request.message or "")},
        ]


    @staticmethod
    def _normalize_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    @staticmethod
    def _render_memory(memory: list[str]) -> str:
        cleaned = [str(item or "").strip() for item in memory if str(item or "").strip()]
        if not cleaned:
            return ""
        return "Relevant memory:\n" + "\n".join(f"- {item}" for item in cleaned)

    @staticmethod
    def _render_skills(skills: list[dict[str, Any]]) -> str:
        lines = []
        for skill in skills or []:
            name = str(skill.get("name", "") or "").strip()
            description = str(skill.get("description", "") or "").strip()
            if not name:
                continue
            lines.append(f"- {name}: {description}" if description else f"- {name}")
        if not lines:
            return ""
        return "Available skills:\n" + "\n".join(lines)



    @staticmethod
    
    def build_directory_skeleton(root: Path, *, max_entries: int = 40, max_depth: int = 2) -> str:
        """当前项目目录结构 """
        if not root.exists():
            return []

        lines: list[str] = []

        def walk(current: Path, depth: int) -> None:
            if depth > max_depth or len(lines) >= max_entries:
                return
            try:
                children = sorted(current.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
            except OSError as exc:
                log.warning("Failed to read project skeleton for {}: {}", current, exc)
                return

            for child in children:
                relative = child.relative_to(root).as_posix()
                lines.append(relative + ("/" if child.is_dir() else ""))
                if child.is_dir() and depth < max_depth:
                    walk(child, depth + 1)
                if len(lines) >= max_entries:
                    break

        walk(root, 0)
        return "\n".join(lines)

    # @staticmethod
    # def load_chat_history(session: Any) -> list[dict[str, Any]]:
    #     session_manager = getattr(session, "session_manager", None)
    #     if session_manager is None:
    #         return []
    #     return list(session_manager.load_history(session.session_id) or [])

    # @staticmethod
    # def load_skills() -> list[dict[str, str]]:
    #     catalog = SkillLoader().load_all_skills() or []
    #     return [
    #         {
    #             "name": skill.name,
    #             "description": str(skill.metadata.get("description", "") or "").strip(),
    #         }
    #         for skill in catalog
    #         if getattr(skill, "name", None)
    #     ]

    # async def load_turn_memory(self, session: Any, request: Any) -> list[str]:
    #     try:
    #         manager = get_memory_manager()
    #         memories = await manager.load_context_memories(
    #             user_id=session.user_id or session.session_id,
    #             project=session.app_id,
    #             current_query=str(getattr(request, "message", "") or getattr(request, "user_input", "") or ""),
    #         )
    #     except Exception as exc:
    #         log.warning("Failed to load memory for session {}: {}", session.session_id, exc)
    #         return []
    #
    #     rendered: list[str] = []
    #     for item in memories or []:
    #         snippet = getattr(item, "snippet", None)
    #         if snippet:
    #             rendered.append(str(snippet))
    #         elif isinstance(item, dict):
    #             rendered.append(str(item.get("snippet") or item.get("text") or "").strip())
    #     return [item for item in rendered if item]



    @staticmethod
    def ensure_user_message(context: Any) -> None:
        user_input = str(getattr(context, "user_input", "") or "").strip()
        if not user_input:
            return

        history = list(getattr(context, "chat_history", []) or [])
        if history:
            last_message = history[-1]
            if isinstance(last_message, dict) and last_message.get("role") == "user" and str(last_message.get("content", "") or "") == user_input:
                return
        history.append({"role": "user", "content": user_input})
        context.chat_history = history






    async def assemble(self, context: Any) -> list[dict[str, Any]]:
        """ 发送llm前最终的组装 prepare_turn_context已经组装到了system_prompt里面了
        这里就只加上user_message即可
        """
        messages: list[dict[str, Any]] = []

        system_prompt = str(getattr(context, "system_prompt", "") or "").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # memory_block = self._render_memory(list(getattr(context, "memory", []) or []))
        # if memory_block:
        #     messages.append({"role": "system", "content": memory_block})
        #
        # skill_block = self._render_skills(list(getattr(context, "skill", []) or []))
        # if skill_block:
        #     messages.append({"role": "system", "content": skill_block})

        messages.extend(self._normalize_history(list(getattr(context, "chat_history", []) or [])))

        return messages
class TurnReducer:
    """Finalize turn context: micro-compact old tool results + full compact if over threshold."""

    def __init__(self) -> None:
        from bot.agent.context_compaction import ContextCompactor
        self._compactor = ContextCompactor()

    async def reduce(self, context: Any) -> None:
        await self._compactor.finalize_turn(context)

@lru_cache(maxsize=1)
def get_context_assembler() -> ContextAssembler:
    return ContextAssembler()