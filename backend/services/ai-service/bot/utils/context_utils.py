from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.constants import get_code_dir


def rough_tokens(messages: list[dict]) -> int:
    """Rough token estimate for a message list: total characters ÷ 4.

    Replace with tiktoken or the Anthropic token-count API for accuracy.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(item.get("content", ""))) for item in content)
        for tc in msg.get("tool_calls", []):
            total += len(str(tc.get("input") or tc.get("function", {}).get("arguments", "")))
    return total // 4


def ensure_app_workdir(app_id: str | int) -> Path:
    workdir = get_code_dir(app_id)
    workdir.mkdir(parents=True, exist_ok=True)
    if workdir.exists():
        print(workdir, "已创建")
    else:
        print(workdir, "创建失败")
    return workdir



def ensure_context_workdir(context: Any) -> Path:
    existing = str(getattr(context, "workdir", "") or "").strip()
    if existing:
        workdir = Path(existing)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = ensure_app_workdir(getattr(context, "app_id", "main"))
        setattr(context, "workdir", str(workdir))
    return workdir