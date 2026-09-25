"""Task management tools (s12, P2 服务化改写 docs/SystemApp架构设计.md §4.3)。

Four tools that give the LLM full CRUD access to the persistent task board:
  task_create  — create a new task, optionally with dependencies
  task_update  — change status / owner / subject / description
  task_get     — fetch a single task record
  task_list    — list all (or filtered) tasks on the board

原注入 TaskManager 实例改为注入 user_id/app_id/session_id，
工具内部经 get_app().tasks（全局 TaskBoardService，ids 作方法参数）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from codegenx.ai_service.tools.base import BaseTool, ToolResult
from shared import log


def _task_service(params: dict) -> Any | None:
    """取全局任务看板服务；ids 缺失返回 None（由调用方给出错误提示）。"""
    from codegenx.ai_service.system_app import get_app

    if not str(params.get("session_id", "") or ""):
        return None
    return get_app().tasks


def _ids(params: dict) -> dict:
    return {
        "user_id": str(params.get("user_id", "") or ""),
        "app_id": str(params.get("app_id", "") or ""),
        "session_id": str(params.get("session_id", "") or ""),
    }


class TaskCreateTool(BaseTool):
    @property
    def name(self) -> str:
        return "task_create"

    @property
    def label(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return (
            "Create a new task on the persistent task board. "
            "Use depends_on to specify task IDs that must complete before this task can start. "
            "Returns the created TaskRecord."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "One-line description of the task.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer explanation or acceptance criteria.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of tasks that must complete before this task can start.",
                },
            },
            "required": ["subject"],
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        tasks = _task_service(params)
        if tasks is None:
            return ToolResult(success=False, data="Task board is not available (missing session ids).")
        try:
            task = tasks.create(
                subject=str(params.get("subject", "")),
                description=str(params.get("description", "") or ""),
                depends_on=list(params.get("depends_on") or []),
                **_ids(params),
            )
            return ToolResult(
                success=True,
                data=str(task),
                render=json.dumps({"action": "create", "task": task}, ensure_ascii=False),
            )
        except Exception as exc:
            log.error("[task_create] {}", exc)
            return ToolResult(success=False, message=f"Error: {exc}",render=f"{self.name} 任务创建失败")


class TaskUpdateTool(BaseTool):
    @property
    def name(self) -> str:
        return "task_update"

    @property
    def label(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return (
            "Update a task's status, owner, subject, or description. "
            "Setting status to 'completed' automatically unblocks dependent tasks. "
            "Valid statuses: pending, in_progress, completed, deleted."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID of the task to update."},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                    "description": "New status for the task.",
                },
                "owner": {"type": "string", "description": "Who is working on this task."},
                "subject": {"type": "string", "description": "Updated one-line subject."},
                "description": {"type": "string", "description": "Updated description."},
            },
            "required": ["task_id"],
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        tasks = _task_service(params)
        if tasks is None:
            return ToolResult(success=False, data="Task board is not available (missing session ids).")
        task_id = params.get("task_id")
        if task_id is None:
            return ToolResult(success=False, data="task_id is required.")
        try:
            task = tasks.update(
                int(task_id),
                status=params.get("status"),
                owner=params.get("owner"),
                subject=params.get("subject"),
                description=params.get("description"),
                **_ids(params),
            )
            return ToolResult(
                success=True,
                data=str(task),
                render=json.dumps({"action": "update", "task": task}, ensure_ascii=False),
            )
        except Exception as exc:
            log.error("[task_update] {}", exc)
            return ToolResult(success=False, message=f"Error: {exc}",render=f"{self.name} 任务更新失败")


class TaskGetTool(BaseTool):
    @property
    def name(self) -> str:
        return "task_get"

    @property
    def label(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return "Fetch a single task record by ID. Returns the full TaskRecord including blockedBy and blocks lists."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID of the task to fetch."},
            },
            "required": ["task_id"],
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        tasks = _task_service(params)
        if tasks is None:
            return ToolResult(success=False, data="Task board is not available (missing session ids).")
        task_id = params.get("task_id")
        if task_id is None:
            return ToolResult(success=False, data="task_id is required.")
        try:
            task = tasks.get(int(task_id), **_ids(params))
            if task is None:
                return ToolResult(success=False, data=f"Task {task_id} not found.", render=f"任务 #{task_id} 不存在")
            return ToolResult(success=True, data=str(task), render=f"获取任务: {task.get('subject', '')}")
        except Exception as exc:
            log.error("[task_get] {}", exc)
            return ToolResult(success=False, message=f"Error: {exc}",render=f"{self.name} 任务获取失败")


class TaskListTool(BaseTool):
    @property
    def name(self) -> str:
        return "task_list"

    @property
    def label(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return (
            "List all tasks on the board. Optionally filter by status. "
            "Returns an array of TaskRecords and a text summary of the board."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                    "description": "Filter by status. Omit to list all tasks.",
                },
            },
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        tasks = _task_service(params)
        if tasks is None:
            return ToolResult(success=False, data="Task board is not available (missing session ids).")
        try:
            status_filter = params.get("status")
            board_tasks = tasks.list_all(status=status_filter, **_ids(params))
            board = tasks.get_board(**_ids(params))
            return ToolResult(
                success=True,
                data=str({"tasks": board_tasks, "board": board}),
                render=f"任务列表: {len(board_tasks)} 个任务"
            )
        except Exception as exc:
            log.error("[task_list] {}", exc)
            return ToolResult(success=False, message=f"Error: {exc}")
