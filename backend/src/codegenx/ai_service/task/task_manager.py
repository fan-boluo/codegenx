"""Persistent task graph (s12, P2 服务化 docs/SystemApp架构设计.md §4.3)。

全局无状态服务 TaskBoardService：方法保留，实例消失，
ids（user_id/app_id/session_id/request_id）进方法签名，路径每次调用推导。

存储边界按 request 对齐（任务由用户请求派生，生命周期为 turn 级）：
    .data/{user_id}/{app_id}/session/{session_id}/.tasks/{request_id}.json

一个请求一个 JSON 文件，内含该请求全部任务的 TaskRecord 列表：
- task_id 按 request 从 1 计数（文件内 max(id)+1），(request_id, task_id)
  复合键天然唯一，跨请求消息握手不会串号；不再维护全局 _counter.json；
- 看板渲染（get_board）仅取当前 request 文件，历史请求任务不进 prompt。

Key concepts
------------
- TaskRecord  — 强类型任务记录（pydantic）：subject/status/executor_agent/blocked_by/blocks/step_id/source
- TaskStatus  — pending / in_progress / completed / deleted
- is_ready()  — True when pending and no remaining blockers (the scheduler rule)
- Auto-unlock — completing a task removes it from all downstream blocked_by lists

磁盘格式一律为当前结构，不做旧格式（task_{id}.json / _counter.json）兼容。
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codegenx.ai_service.component import BaseComponent, ComponentType
from shared import log
from shared.constants import get_current_session_dir

# ------------------------------------------------------------------ constants


class TaskStatus(str, Enum):
    """任务状态（落盘为字符串值）。"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELETED = "deleted"


VALID_STATUSES = frozenset(s.value for s in TaskStatus)

_STATUS_MARKER: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "deleted": "[-]",
}


class TaskRecord(BaseModel):
    """任务看板强类型记录（《上下文工程设计方案2》§3.2）。

    - executor_agent：执行智能体 agent_name（supervisor 表示汇总类自执行步骤）
    - blocked_by / blocks：前置/下游依赖任务 ID（blocks 自动维护，id 为 request 内局部 id）
    - step_id：计划模式关联的 plan step_id（direct 模式为 None）
    - source：派发来源 direct（自主编排）/ planner（计划落板）
    """
    id: int
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    executor_agent: str = ""
    blocked_by: list[int] = Field(default_factory=list)
    blocks: list[int] = Field(default_factory=list)
    step_id: int | None = None
    source: str = "direct"


def _request_file(user_id: str, app_id: str, session_id: str, request_id: str) -> Path:
    """任务文件：按 request 隔离（.tasks/{request_id}.json），一个请求全部任务存一个文件。"""
    rid = str(request_id or "").strip()
    if not rid:
        raise ValueError("request_id is required for task board operations")
    tasks_dir = get_current_session_dir(user_id, app_id, session_id) / ".tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return tasks_dir / f"{rid}.json"


# ------------------------------------------------------------------ TaskBoardService


class TaskBoardService(BaseComponent):
    """Per-request persistent task graph（全局单例，ids 走参数）。

    Disk-backed so tasks survive session restarts.  任务按 request_id 单文件
    隔离：同一会话内不同请求的任务互不可见，id 各自从 1 计数。
    """

    name = ComponentType.TASK_BOARD

    def init_app(self, system_app) -> None:
        self.system_app = system_app

    # ------------------------------------------------------------------ persistence

    def _load_all(self, f: Path) -> list[TaskRecord]:
        """读入请求任务文件（TaskRecord 列表）；文件缺失/损坏返回空表。"""
        if not f.exists():
            return []
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            return [TaskRecord.model_validate(item) for item in raw]
        except Exception as exc:
            log.warning("[TaskBoard] Failed to load request file {}: {}", f.name, exc)
            return []

    def _save_all(self, f: Path, tasks: list[TaskRecord]) -> None:
        """全量回写请求任务文件（按 id 升序）。"""
        payload = [t.model_dump(mode="json") for t in sorted(tasks, key=lambda x: x.id)]
        f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save(self, f: Path, task: TaskRecord) -> None:
        """按 id 原位更新或追加单条任务记录。"""
        tasks = self._load_all(f)
        for i, t in enumerate(tasks):
            if t.id == task.id:
                tasks[i] = task
                break
        else:
            tasks.append(task)
        self._save_all(f, tasks)

    def _load(self, f: Path, task_id: int) -> TaskRecord | None:
        for t in self._load_all(f):
            if t.id == task_id:
                return t
        return None

    def _all_tasks(self, f: Path) -> list[TaskRecord]:
        return self._load_all(f)

    def _next_id(self, f: Path) -> int:
        """task_id 按 request 从 1 计数（文件内 max(id)+1），无需全局计数器。"""
        return max((t.id for t in self._load_all(f)), default=0) + 1

    # ------------------------------------------------------------------ ready rule (s12)

    @staticmethod
    def is_ready(task: TaskRecord) -> bool:
        """True when the task is pending and has no remaining blockers."""
        return task.status is TaskStatus.PENDING and not task.blocked_by

    # ------------------------------------------------------------------ core API

    def create(
        self,
        subject: str,
        description: str = "",
        depends_on: list[int] | None = None,
        *,
        executor_agent: str = "",
        step_id: int | None = None,
        source: str = "direct",
        user_id: str = "",
        app_id: str = "",
        session_id: str = "",
        request_id: str = "",
    ) -> TaskRecord:
        """Create a new task, optionally waiting on ``depends_on`` task IDs."""
        f = _request_file(user_id, app_id, session_id, request_id)
        task_id = self._next_id(f)
        blocked_by = list(depends_on or [])
        task = TaskRecord(
            id=task_id,
            subject=subject,
            description=description,
            status=TaskStatus.PENDING,
            executor_agent=executor_agent,
            blocked_by=blocked_by,
            step_id=step_id,
            source=source,
        )
        self._save(f, task)

        # Maintain bidirectional dependency (s12)
        for upstream_id in blocked_by:
            upstream = self._load(f, upstream_id)
            if upstream is not None and task_id not in upstream.blocks:
                upstream.blocks.append(task_id)
                self._save(f, upstream)

        log.info("[TaskBoard] Created task {} (request {}) — {}", task_id, request_id, subject)
        return task

    def update(
        self,
        task_id: int,
        *,
        status: str | None = None,
        executor_agent: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        user_id: str = "",
        app_id: str = "",
        session_id: str = "",
        request_id: str = "",
    ) -> TaskRecord:
        """Update mutable fields of an existing task."""
        f = _request_file(user_id, app_id, session_id, request_id)
        task = self._load(f, task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found (request {request_id})")

        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}")
            task.status = TaskStatus(status)
        if executor_agent is not None:
            task.executor_agent = executor_agent
        if subject is not None:
            task.subject = subject
        if description is not None:
            task.description = description

        self._save(f, task)

        # Auto-unlock downstream tasks when this one completes (s12)
        if status == "completed":
            self._unlock_downstream(f, task_id)

        return task

    def complete(
        self, task_id: int, *, user_id: str = "", app_id: str = "", session_id: str = "", request_id: str = ""
    ) -> TaskRecord:
        """Convenience wrapper — marks task completed and unlocks dependents."""
        return self.update(
            task_id, status="completed",
            user_id=user_id, app_id=app_id, session_id=session_id, request_id=request_id,
        )

    def get(
        self, task_id: int, *, user_id: str = "", app_id: str = "", session_id: str = "", request_id: str = ""
    ) -> TaskRecord | None:
        return self._load(_request_file(user_id, app_id, session_id, request_id), task_id)

    def list_all(
        self, status: str | None = None, *, user_id: str = "", app_id: str = "", session_id: str = "", request_id: str = ""
    ) -> list[TaskRecord]:
        tasks = self._all_tasks(_request_file(user_id, app_id, session_id, request_id))
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def get_board(
        self, *, user_id: str = "", app_id: str = "", session_id: str = "", request_id: str = ""
    ) -> str:
        """Render a compact text board for the system prompt（仅当前 request 的任务）。

        request_id 缺失时无请求上下文，直接返回空板（不读盘、不落盘）。
        """
        if not str(request_id or "").strip():
            return "No active tasks."
        tasks = [
            t for t in self._all_tasks(_request_file(user_id, app_id, session_id, request_id))
            if t.status is not TaskStatus.DELETED
        ]
        if not tasks:
            return "No active tasks."

        lines = ["## Task Board"]
        for t in tasks:
            marker = _STATUS_MARKER.get(t.status.value, "[ ]")
            ready_tag = " ✓ready" if self.is_ready(t) else ""
            blocked_tag = f" ← blocked by {t.blocked_by}" if t.blocked_by else ""
            owner_tag = f" @{t.executor_agent}" if t.executor_agent else ""
            lines.append(
                f"{marker} [{t.id}] {t.subject}{owner_tag}{ready_tag}{blocked_tag}"
            )
            if t.description:
                lines.append(f"      {t.description}")

        completed = sum(1 for t in tasks if t.status is TaskStatus.COMPLETED)
        active = [t for t in tasks if t.status not in {TaskStatus.COMPLETED, TaskStatus.DELETED}]
        ready = [t for t in active if self.is_ready(t)]
        lines.append(f"\n({completed}/{len(tasks)} completed, {len(ready)} ready to start)")
        return "\n".join(lines)

    # ------------------------------------------------------------------ private

    def _unlock_downstream(self, f: Path, completed_id: int) -> None:
        """Remove completed_id from blocked_by of all tasks that were waiting on it."""
        for task in self._all_tasks(f):
            if completed_id in task.blocked_by:
                task.blocked_by.remove(completed_id)
                self._save(f, task)
                log.debug(
                    "[TaskBoard] Task {} unblocked after task {} completed",
                    task.id,
                    completed_id,
                )


def initialize_tasks(system_app) -> TaskBoardService:
    """注册任务看板服务组件（system_app.initialize_components 调用）。"""
    return system_app.register(TaskBoardService)
