from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from bot.memory.memory_manager import get_memory_manager
from bot.skill.skill_loader import SkillLoader
from bot.utils.context_utils import ensure_app_workdir
from bot.utils.log_utils import log


class ContextAssembler:
    """Assemble model messages from the slim TurnContext payload."""

    DEFAULT_PROMPT_TEMPLATE = (
        "You are a coding agent operating in {code_dir}.\n\n"
        "Use the provided tools to inspect, edit, and validate work.\n\n"
        "Prefer verification over guessing. Keep working until the task is resolved or blocked."
    )

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
    def build_tool_catalog(tool_catalog: list[Any]) -> list[dict[str, Any]]:
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

    @staticmethod
    def build_directory_skeleton(root: Path, *, max_entries: int = 40, max_depth: int = 2) -> list[str]:
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
        return lines

    @staticmethod
    def load_chat_history(session: Any) -> list[dict[str, Any]]:
        session_manager = getattr(session, "session_manager", None)
        if session_manager is None:
            return []
        return list(session_manager.load_history(session.session_id) or [])

    @staticmethod
    def load_skills() -> list[dict[str, str]]:
        catalog = SkillLoader().load_all_skills() or []
        return [
            {
                "name": skill.name,
                "description": str(skill.metadata.get("description", "") or "").strip(),
            }
            for skill in catalog
            if getattr(skill, "name", None)
        ]

    async def load_turn_memory(self, session: Any, request: Any) -> list[str]:
        try:
            manager = get_memory_manager()
            memories = await manager.load_context_memories(
                user_id=session.user_id or session.session_id,
                project=session.app_id,
                current_query=request.user_input,
            )
        except Exception as exc:
            log.warning("Failed to load memory for session {}: {}", session.session_id, exc)
            return []

        rendered: list[str] = []
        for item in memories or []:
            snippet = getattr(item, "snippet", None)
            if snippet:
                rendered.append(str(snippet))
            elif isinstance(item, dict):
                rendered.append(str(item.get("snippet") or item.get("text") or "").strip())
        return [item for item in rendered if item]

    @staticmethod
    def build_system_prompt(session: Any, request: Any) -> str:
        turn = request if hasattr(request, "workspace_metadata") else None
        code_dir = str(getattr(turn, "code_dir", "") or ensure_app_workdir(session.app_id))
        workspace_metadata = dict(getattr(turn, "workspace_metadata", {}) or {})
        safe_paths = list(getattr(turn, "safe_paths", []) or workspace_metadata.get("safe_paths", []) or [code_dir])
        knowledge_cache = dict(getattr(turn, "knowledge_cache", {}) or {})
        prompt_template = str(getattr(turn, "prompt_template", "") or ContextAssembler.DEFAULT_PROMPT_TEMPLATE)
        requested_code_gen_type = ""
        if hasattr(request, "requested_code_gen_type"):
            requested_code_gen_type = str(getattr(request, "requested_code_gen_type", "") or "")
        elif turn is not None:
            requested_code_gen_type = str(getattr(getattr(turn, "request", None), "requested_code_gen_type", "") or "")

        parts = [prompt_template.format(code_dir=code_dir)]
        if requested_code_gen_type:
            parts.append(f"Requested code generation type: {requested_code_gen_type}")
        parts.append(f"Operating system: {workspace_metadata.get('os_name', 'windows')}")
        parts.append("Writable directories: " + ", ".join(str(path) for path in safe_paths if str(path).strip()))
        project_skeleton = list(workspace_metadata.get("project_skeleton", []) or [])
        if project_skeleton:
            parts.append("Project skeleton:\n" + "\n".join(f"- {item}" for item in project_skeleton))
        cached_templates = list(knowledge_cache.get("code_type_templates", []) or [])
        if cached_templates:
            parts.append("Cached code-type templates:\n" + "\n".join(f"- {item}" for item in cached_templates))
        return "\n\n".join(parts)

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

    async def prepare_turn_context(self, session: Any, turn: Any, tool_catalog: list[Any]) -> None:
        code_dir = ensure_app_workdir(session.app_id)
        safe_paths = [str(code_dir)]
        workspace_metadata = {
            "code_dir": str(code_dir),
            "safe_paths": list(safe_paths),
            "allowed_rw_dirs": list(safe_paths),
            "os_name": (platform.system() or "Windows").lower(),
            "project_skeleton": self.build_directory_skeleton(code_dir),
        }
        knowledge_cache = {
            "code_type_templates": [],
            "template_source": "reserved",
            "code_gen_type": str(turn.request.requested_code_gen_type or ""),
        }

        turn.code_dir = str(code_dir)
        turn.safe_paths = list(safe_paths)
        turn.workspace_metadata = workspace_metadata
        turn.knowledge_cache = knowledge_cache
        turn.prompt_template = self.DEFAULT_PROMPT_TEMPLATE
        turn.plan_summary = str(turn.request.metadata.get("plan_summary", "") or "")
        turn.context.build_tool = self.build_tool_catalog(tool_catalog)
        turn.context.skill = self.load_skills()
        turn.context.memory = await self.load_turn_memory(session, turn.request)
        turn.context.system_prompt = self.build_system_prompt(session, turn)
        turn.context.chat_history = self.load_chat_history(session)
        turn.context.user_input = str(turn.request.user_input or "")

    async def assemble(self, context: Any) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        system_prompt = str(getattr(context, "system_prompt", "") or "").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        memory_block = self._render_memory(list(getattr(context, "memory", []) or []))
        if memory_block:
            messages.append({"role": "system", "content": memory_block})

        skill_block = self._render_skills(list(getattr(context, "skill", []) or []))
        if skill_block:
            messages.append({"role": "system", "content": skill_block})

        messages.extend(self._normalize_history(list(getattr(context, "chat_history", []) or [])))

        return messages
class TurnReducer:
    """Turn state is owned by the runtime session store, so reduction is intentionally empty."""

    async def reduce(self, context: Any) -> None:
        log.debug("TurnReducer skipped for slim TurnContext")
