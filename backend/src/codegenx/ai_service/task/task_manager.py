"""Persistent task graph (s12, P2 服务化 docs/SystemApp架构设计.md §4.3)。

原 TaskManager（每会话一个实例、只持磁盘路径，且被 SessionContext 兜底重复创建）
改为全局无状态服务 TaskBoardService：方法保留，实例消失，
ids（user_id/app_id/session_id）进方法签名，路径每次调用推导。

One JSON file per task under:
    .data/{user_id}/{app_id}/session/{session_id}/.tasks/task_{id}.json

Key concepts
------------
- TaskRecord  — the unit of work: subject, status, blockedBy, blocks, owner
- TaskStatus  — pending / in_progress / completed / deleted
- is_ready()  — True when pending and no remaining blockers (the scheduler rule)
- Auto-unlock — completing a task removes it from all downstream blockedBy lists
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared import log
from shared.constants import get_current_session_dir

# ------------------------------------------------------------------ constants

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "deleted"})

_STATUS_MARKER: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "deleted": "[-]",
}


def _tasks_dir(user_id: str, app_id: str, session_id: str) -> Path:
    """任务看板目录：随会话目录按 用户/项目 两级隔离。"""
    tasks_dir = get_current_session_dir(user_id, app_id, session_id) / ".tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return tasks_dir


# ------------------------------------------------------------------ TaskBoardService


class TaskBoardService:
    """Per-(user, app, session) persistent task graph（全局单例，ids 走参数）。

    Disk-backed so tasks survive session restarts.  Multiple sessions for
    the same ``app_id`` are isolated by their session directories.
    """

    # ------------------------------------------------------------------ ID management

    def _next_id(self, tasks_dir: Path) -> int:
        counter_file = tasks_dir / "_counter.json"
        if counter_file.exists():
            data = json.loads(counter_file.read_text(encoding="utf-8"))
            next_id = int(data.get("next_id", 1))
        else:
            next_id = 1
        counter_file.write_text(
            json.dumps({"next_id": next_id + 1}), encoding="utf-8"
        )
        return next_id

    # ------------------------------------------------------------------ persistence

    def _task_file(self, tasks_dir: Path, task_id: int) -> Path:
        return tasks_dir / f"task_{task_id}.json"

    def _save(self, tasks_dir: Path, task: dict[str, Any]) -> None:
        self._task_file(tasks_dir, task["id"]).write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load(self, tasks_dir: Path, task_id: int) -> dict[str, Any] | None:
        f = self._task_file(tasks_dir, task_id)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("[TaskBoard] Failed to load task {}: {}", task_id, exc)
            return None

    def _all_tasks(self, tasks_dir: Path) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for f in sorted(tasks_dir.glob("task_*.json"), key=lambda p: p.name):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("[TaskBoard] Skipping unreadable task file {}: {}", f, exc)
        return tasks

    # ------------------------------------------------------------------ ready rule (s12)

    @staticmethod
    def is_ready(task: dict[str, Any]) -> bool:
        """True when the task is pending and has no remaining blockers."""
        return task.get("status") == "pending" and not task.get("blockedBy")

    # ------------------------------------------------------------------ core API

    def create(
        self,
        subject: str,
        description: str = "",
        depends_on: list[int] | None = None,
        *,
        user_id: str = "",
        app_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Create a new task, optionally waiting on ``depends_on`` task IDs."""
        tasks_dir = _tasks_dir(user_id, app_id, session_id)
        task_id = self._next_id(tasks_dir)
        blocked_by = list(depends_on or [])
        task: dict[str, Any] = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blockedBy": blocked_by,
            "blocks": [],
            "owner": "",
        }
        self._save(tasks_dir, task)

        # Maintain bidirectional dependency (s12)
        for upstream_id in blocked_by:
            upstream = self._load(tasks_dir, upstream_id)
            if upstream is not None and task_id not in upstream["blocks"]:
                upstream["blocks"].append(task_id)
                self._save(tasks_dir, upstream)

        log.info("[TaskBoard] Created task {} — {}", task_id, subject)
        return task

    def update(
        self,
        task_id: int,
        *,
        status: str | None = None,
        owner: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        user_id: str = "",
        app_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Update mutable fields of an existing task."""
        tasks_dir = _tasks_dir(user_id, app_id, session_id)
        task = self._load(tasks_dir, task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}")
            task["status"] = status
        if owner is not None:
            task["owner"] = owner
        if subject is not None:
            task["subject"] = subject
        if description is not None:
            task["description"] = description

        self._save(tasks_dir, task)

        # Auto-unlock downstream tasks when this one completes (s12)
        if status == "completed":
            self._unlock_downstream(tasks_dir, task_id)

        return task

    def complete(
        self, task_id: int, *, user_id: str = "", app_id: str = "", session_id: str = ""
    ) -> dict[str, Any]:
        """Convenience wrapper — marks task completed and unlocks dependents."""
        return self.update(task_id, status="completed", user_id=user_id, app_id=app_id, session_id=session_id)

    def get(
        self, task_id: int, *, user_id: str = "", app_id: str = "", session_id: str = ""
    ) -> dict[str, Any] | None:
        return self._load(_tasks_dir(user_id, app_id, session_id), task_id)

    def list_all(
        self, status: str | None = None, *, user_id: str = "", app_id: str = "", session_id: str = ""
    ) -> list[dict[str, Any]]:
        tasks = self._all_tasks(_tasks_dir(user_id, app_id, session_id))
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        return tasks

    def get_board(self, *, user_id: str = "", app_id: str = "", session_id: str = "") -> str:
        """Render a compact text board for the system prompt."""
        tasks = [
            t for t in self._all_tasks(_tasks_dir(user_id, app_id, session_id))
            if t.get("status") != "deleted"
        ]
        if not tasks:
            return "No active tasks."

        lines = ["## Task Board"]
        for t in tasks:
            marker = _STATUS_MARKER.get(str(t.get("status", "")), "[ ]")
            ready_tag = " ✓ready" if self.is_ready(t) else ""
            blocked_tag = f" ← blocked by {t['blockedBy']}" if t.get("blockedBy") else ""
            owner_tag = f" @{t['owner']}" if t.get("owner") else ""
            lines.append(
                f"{marker} [{t['id']}] {t['subject']}{owner_tag}{ready_tag}{blocked_tag}"
            )
            if t.get("description"):
                lines.append(f"      {t['description']}")

        completed = sum(1 for t in tasks if t.get("status") == "completed")
        active = [t for t in tasks if t.get("status") not in {"completed", "deleted"}]
        ready = [t for t in active if self.is_ready(t)]
        lines.append(f"\n({completed}/{len(tasks)} completed, {len(ready)} ready to start)")
        return "\n".join(lines)

    # ------------------------------------------------------------------ private

    def _unlock_downstream(self, tasks_dir: Path, completed_id: int) -> None:
        """Remove completed_id from blockedBy of all tasks that were waiting on it."""
        for task in self._all_tasks(tasks_dir):
            if completed_id in task.get("blockedBy", []):
                task["blockedBy"].remove(completed_id)
                self._save(tasks_dir, task)
                log.debug(
                    "[TaskBoard] Task {} unblocked after task {} completed",
                    task["id"],
                    completed_id,
                )
