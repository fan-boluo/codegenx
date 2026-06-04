"""Persistent task graph (s12).

Upgrades the session-only Planner (s03) to a durable, dependency-aware task
board stored on disk. One JSON file per task under:

    ~/.bot/workspace/{app_id}/.tasks/task_{id}.json

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

from shared.config.log_config import log
from shared.constants import get_current_session_dir

# ------------------------------------------------------------------ constants

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "deleted"})

_STATUS_MARKER: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "deleted": "[-]",
}


# ------------------------------------------------------------------ TaskManager


class TaskManager:
    """Per-app-id persistent task graph.

    Scope: one ``TaskManager`` per ``app_id``/``session_id``; disk-backed so tasks survive
    session restarts.  Multiple sessions for the same ``app_id`` share the
    same board transparently.
    """

    def __init__(self, app_id: str, session_id: str = "") -> None:
        self._tasks_dir: Path = get_current_session_dir(app_id, session_id) / ".tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._counter_file: Path = self._tasks_dir / "_counter.json"

    # ------------------------------------------------------------------ ID management

    def _next_id(self) -> int:
        if self._counter_file.exists():
            data = json.loads(self._counter_file.read_text(encoding="utf-8"))
            next_id = int(data.get("next_id", 1))
        else:
            next_id = 1
        self._counter_file.write_text(
            json.dumps({"next_id": next_id + 1}), encoding="utf-8"
        )
        return next_id

    # ------------------------------------------------------------------ persistence

    def _task_file(self, task_id: int) -> Path:
        return self._tasks_dir / f"task_{task_id}.json"

    def _save(self, task: dict[str, Any]) -> None:
        self._task_file(task["id"]).write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load(self, task_id: int) -> dict[str, Any] | None:
        f = self._task_file(task_id)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("[TaskManager] Failed to load task {}: {}", task_id, exc)
            return None

    def _all_tasks(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for f in sorted(self._tasks_dir.glob("task_*.json"), key=lambda p: p.name):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("[TaskManager] Skipping unreadable task file {}: {}", f, exc)
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
    ) -> dict[str, Any]:
        """Create a new task, optionally waiting on ``depends_on`` task IDs."""
        task_id = self._next_id()
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
        self._save(task)

        # Maintain bidirectional dependency (s12)
        for upstream_id in blocked_by:
            upstream = self._load(upstream_id)
            if upstream is not None and task_id not in upstream["blocks"]:
                upstream["blocks"].append(task_id)
                self._save(upstream)

        log.info("[TaskManager] Created task {} — {}", task_id, subject)
        return task

    def update(
        self,
        task_id: int,
        *,
        status: str | None = None,
        owner: str | None = None,
        subject: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Update mutable fields of an existing task."""
        task = self._load(task_id)
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

        self._save(task)

        # Auto-unlock downstream tasks when this one completes (s12)
        if status == "completed":
            self._unlock_downstream(task_id)

        return task

    def complete(self, task_id: int) -> dict[str, Any]:
        """Convenience wrapper — marks task completed and unlocks dependents."""
        return self.update(task_id, status="completed")

    def get(self, task_id: int) -> dict[str, Any] | None:
        return self._load(task_id)

    def list_all(self, status: str | None = None) -> list[dict[str, Any]]:
        tasks = self._all_tasks()
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        return tasks

    def get_board(self) -> str:
        """Render a compact text board for the system prompt."""
        tasks = [t for t in self._all_tasks() if t.get("status") != "deleted"]
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

    def _unlock_downstream(self, completed_id: int) -> None:
        """Remove completed_id from blockedBy of all tasks that were waiting on it."""
        for task in self._all_tasks():
            if completed_id in task.get("blockedBy", []):
                task["blockedBy"].remove(completed_id)
                self._save(task)
                log.debug(
                    "[TaskManager] Task {} unblocked after task {} completed",
                    task["id"],
                    completed_id,
                )
