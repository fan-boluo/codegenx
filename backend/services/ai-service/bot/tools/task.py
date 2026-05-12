"""Task management tools (s12).

Four tools that give the LLM full CRUD access to the persistent task board:
  task_create  — create a new task, optionally with dependencies
  task_update  — change status / owner / subject / description
  task_get     — fetch a single task record
  task_list    — list all (or filtered) tasks on the board
"""
from __future__ import annotations

import asyncio
from typing import Any

from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log


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
        task_manager = params.get("task_manager")
        if task_manager is None:
            return ToolResult(success=False, data="TaskManager is not available.")
        try:
            task = task_manager.create(
                subject=str(params.get("subject", "")),
                description=str(params.get("description", "") or ""),
                depends_on=list(params.get("depends_on") or []),
            )
            return ToolResult(success=True, data=task)
        except Exception as exc:
            log.error("[task_create] {}", exc)
            return ToolResult(success=False, data=f"Error: {exc}")


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
        task_manager = params.get("task_manager")
        if task_manager is None:
            return ToolResult(success=False, data="TaskManager is not available.")
        task_id = params.get("task_id")
        if task_id is None:
            return ToolResult(success=False, data="task_id is required.")
        try:
            task = task_manager.update(
                int(task_id),
                status=params.get("status"),
                owner=params.get("owner"),
                subject=params.get("subject"),
                description=params.get("description"),
            )
            return ToolResult(success=True, data=task)
        except Exception as exc:
            log.error("[task_update] {}", exc)
            return ToolResult(success=False, data=f"Error: {exc}")


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
        task_manager = params.get("task_manager")
        if task_manager is None:
            return ToolResult(success=False, data="TaskManager is not available.")
        task_id = params.get("task_id")
        if task_id is None:
            return ToolResult(success=False, data="task_id is required.")
        try:
            task = task_manager.get(int(task_id))
            if task is None:
                return ToolResult(success=False, data=f"Task {task_id} not found.")
            return ToolResult(success=True, data=task)
        except Exception as exc:
            log.error("[task_get] {}", exc)
            return ToolResult(success=False, data=f"Error: {exc}")


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
        task_manager = params.get("task_manager")
        if task_manager is None:
            return ToolResult(success=False, data="TaskManager is not available.")
        try:
            status_filter = params.get("status")
            tasks = task_manager.list_all(status=status_filter)
            board = task_manager.get_board()
            return ToolResult(
                success=True,
                data={"tasks": tasks, "board": board},
                details={"count": len(tasks)},
            )
        except Exception as exc:
            log.error("[task_list] {}", exc)
            return ToolResult(success=False, data=f"Error: {exc}")
