from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.constants import get_code_dir


def ensure_app_workdir(app_id: str | int) -> Path:
    workdir = get_code_dir(app_id)
    workdir.mkdir(parents=True, exist_ok=True)
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