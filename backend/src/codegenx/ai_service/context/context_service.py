"""ContextService —— 无状态上下文组装服务（P1，docs/SystemApp架构设计.md §4.4）。

迁自 ContextAssembler 的「无状态逻辑」：
  - 工作区元数据构建（build_workspace_metadata）
  - 目录骨架扫描（带 (user_id, app_id) → skeleton 的 TTL 缓存，§9）
  - 历史消息标准化（normalize_history）
  - system prompt 渲染（render_turn_context）与最终消息列表组装（assemble）

原 ContextAssembler 的「每轮可变字段」（memory_prompt/skill_prompt/task_prompt/
session_summary_prompt/workspace_metadata）拆到 TurnPrompts，归属 SessionContext——
全局组件不得持有会话/轮次可变状态（§10.1 红线）。

原 get_context_assembler() 陷阱单例已删除：它带 lru_cache 却持有每轮可变状态，
一旦被使用就是跨会话串话事故。
"""
from __future__ import annotations

import datetime
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codegenx.ai_service.utils.context_utils import ensure_app_workdir
from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.hook import HookContext, HookEvent, on
from codegenx.ai_service.prompt.runtime_prompt import DEFAULT_PROMPT_TEMPLATE
from shared import log



# ── 无状态组装服务 ───────────────────────────────────────────────────────────

_SKELETON_TTL_SECONDS = 60.0
_SKELETON_CACHE_CAPACITY = 200


class ContextService(BaseComponent):
    """无状态上下文组装（全局组件，ids/每轮产物全部走参数）。"""

    name = ComponentType.CONTEXT_SERVICE

    def __init__(self, system_app=None) -> None:
        BaseComponent.__init__(self, system_app)
        # 目录骨架 TTL 缓存：(user_id, app_id) → (skeleton, monotonic 时间戳)
        self._skeleton_cache: dict[tuple[str, str], tuple[str, float]] = {}

    def init_app(self, system_app) -> None:
        self.system_app = system_app

    # ------------------------------------------------------------------ workspace

    async def build_workspace_metadata(
        self, user_id: str, app_id: str, db_name: str | None = None
    ) -> dict:
        """构建工作区元数据（迁自 ContextAssembler.build_workspace，不再写入 self）。"""
        code_dir = ensure_app_workdir(user_id, app_id)
        safe_paths = [str(code_dir)]

        # CSV 数据目录作为安全路径（从配置读取）
        csv_data_dirs: list[str] = []
        try:
            from shared.config.config import get_settings

            settings = get_settings()
            csv_dir = getattr(settings, "csv_data_dir", None) or ""
            if csv_dir:
                csv_dir = str(csv_dir).strip()
                if csv_dir and Path(csv_dir).exists():
                    csv_data_dirs.append(csv_dir)
        except Exception:
            pass

        return {
            "code_dir": str(code_dir),
            "safe_paths": list(safe_paths),
            # 记忆写入已改为条件触发的离线任务（schedule/），agent 不再直写 memory 目录
            "allowed_rw_dirs": safe_paths + csv_data_dirs,
            "os_name": (platform.system() or "Windows").lower(),
            "project_skeleton": self.get_directory_skeleton(user_id, app_id, code_dir),
            "db_name": db_name or "",
            "csv_data_dirs": csv_data_dirs,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }

    def build_base_prompt(self, workspace_metadata: dict, persona: str = "") -> str:
        """基础 persona prompt（原 base_prompt，由 code_dir 推导，非每轮可变状态）。

        P4 §10.3：persona 由 AgentSpec 提供；空=内置 DEFAULT_PROMPT_TEMPLATE（现行为）。
        """
        if not workspace_metadata:
            return ""
        template = persona.strip() or DEFAULT_PROMPT_TEMPLATE
        return template.format(
            code_dir=workspace_metadata.get("code_dir")
        )

    # ------------------------------------------------------------------ skeleton

    def get_directory_skeleton(
        self, user_id: str, app_id: str, code_dir: Path | str
    ) -> str:
        """带 TTL 缓存的目录骨架（§9：工作区目录变化由 TTL 60s 兜底）。"""
        key = (str(user_id), str(app_id))
        cached = self._skeleton_cache.get(key)
        if cached is not None:
            skeleton, ts = cached
            if time.monotonic() - ts < _SKELETON_TTL_SECONDS:
                return skeleton
        skeleton = self.build_directory_skeleton(Path(code_dir))
        if len(self._skeleton_cache) >= _SKELETON_CACHE_CAPACITY:
            self._skeleton_cache.clear()  # 简单容量兜底，避免无限膨胀
        self._skeleton_cache[key] = (skeleton, time.monotonic())
        return skeleton

    def invalidate_skeleton(self, user_id: str | None = None, app_id: str | None = None) -> None:
        """显式失效骨架缓存（文件工具写盘后可选触发）。"""
        if user_id is None or app_id is None:
            self._skeleton_cache.clear()
        else:
            self._skeleton_cache.pop((str(user_id), str(app_id)), None)

    @staticmethod
    def build_directory_skeleton(root: Path, *, max_entries: int = 40, max_depth: int = 2) -> str:
        """当前项目目录结构"""
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

    # ------------------------------------------------------------------ rendering

    def build_extra(self, extra: str) -> str | None:
        """提醒，需要更新任务看板了"""
        parts = []
        if extra:
            parts.append(extra)
        if not parts:
            return None
        content = "<system-reminder>\n" + "\n".join(parts) + "\n</system-reminder>"
        return content


def initialize_context(system_app) -> ContextService:
    """注册上下文组装服务组件（system_app.initialize_components 调用）。"""
    return system_app.register(ContextService)
