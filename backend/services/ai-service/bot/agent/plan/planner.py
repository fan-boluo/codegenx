from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class PlanItem:
    content: str
    status: str = field(default="pending") # "pending", "in_progress", "completed"
    active_form: str = ""

@dataclass
class PlanningState:
    items: list[PlanItem] = field(default_factory=list)
    rounds_since_update: int = 0

class Planner:
    def __init__(self):
        self.state = PlanningState()
        self.plan_reminder_interval = 3

    def update_plan(self, items: List[Dict[str, Any]]) -> str:
        """ Update the plan items """
        if len(items) > 12:
            raise ValueError("Keep the session plan short (max 12 items)")

        normalized = []
        in_progress_count = 0
        for index, raw_item in enumerate(items):
            content = str(raw_item.get("content", "")).strip()
            status = str(raw_item.get("status", "pending")).lower()
            active_form = str(raw_item.get("activeForm", "")).strip()
            if not content:
                continue
            if status not in {"pending", "in_progress", "completed"}:
                status = "pending"
            if status == "in_progress":
                in_progress_count += 1
            normalized.append(PlanItem(
                content=content,
                status=status,
                active_form=active_form,
            ))
        if in_progress_count > 1:
            raise ValueError("Only one plan item can be in_progress")
        if len(normalized) > 1 and in_progress_count != 1:
            raise ValueError("Multi-step plans must have exactly one in_progress item")
        self.state.items = normalized
        self.state.rounds_since_update = 0
        return self.get_state()

    def get_state(self) -> str:
        """ Get the current plan state text """
        if not self.state.items:
            return "No active session plan."
        lines = []
        for item in self.state.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(item.status, "[ ]")
            line = f"{marker} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)
        completed = sum(1 for item in self.state.items if item.status == "completed")
        lines.append(f"\n({completed}/{len(self.state.items)} completed)")
        return "\n".join(lines)

    def note_round(self):
        self.state.rounds_since_update += 1
