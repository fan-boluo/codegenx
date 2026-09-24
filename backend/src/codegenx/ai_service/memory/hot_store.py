"""
hot 层存储 —— 核心约束记忆（hot.json，始终注入 system prompt）。

特点：
  - 文件很小（token 预算 HOT_TOKEN_BUDGET=2000），整读整写，无需增量机制；
  - 不进 Qdrant（不需要检索，永远在上下文里）；
  - 超预算时从头部截断（保留最新写入的条目，旧约束自然沉底）。
"""
from __future__ import annotations

import json
from pathlib import Path

from shared import log

from codegenx.ai_service.memory.models import (
    MemoryEntry,
    STATUS_ACTIVE,
    estimate_text_tokens,
)
from codegenx.ai_service.memory.paths import get_hot_store_path

HOT_TOKEN_BUDGET = 2000  # hot 层注入上限（设计文档 §5.4）


def load_hot_entries(user_id: str, app_id: str, path: Path | None = None) -> list[MemoryEntry]:
    """读取 hot.json 全部 active 条目（文件小，直接全量）。"""
    path = path or get_hot_store_path(user_id, app_id)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("hot.json 读取失败 {}:{}", path, exc)
        return []
    entries = []
    for item in raw if isinstance(raw, list) else []:
        entry = MemoryEntry.from_dict(item) if isinstance(item, dict) else None
        if entry is not None and entry.status == STATUS_ACTIVE:
            entries.append(entry)
    return entries


def save_hot_entries(user_id: str, app_id: str, entries: list[MemoryEntry], path: Path | None = None) -> None:
    """整写 hot.json（调用方持有完整列表，本地小文件无并发热点）。"""
    path = path or get_hot_store_path(user_id, app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def add_hot_rule(
    user_id: str,
    app_id: str,
    content: str,
    memory_type: str = "user_preference",
    topic: str = "",
    source_session_id: str = "",
) -> MemoryEntry:
    """追加一条 hot 规则并落盘（超过预算时截掉最旧的条目）。"""
    entries = load_hot_entries(user_id, app_id)
    entry = MemoryEntry(
        layer="hot",
        topic=topic,
        memory_type=memory_type,
        content=content,
        source_session_id=source_session_id,
    )
    entries.append(entry)

    # 从头部截断：保留最新的条目
    while entries and estimate_text_tokens(_entries_text(entries)) > HOT_TOKEN_BUDGET:
        dropped = entries.pop(0)
        log.info("hot 层超预算，截断最旧条目:{}", dropped.id)

    save_hot_entries(user_id, app_id, entries)
    return entry


def _entries_text(entries: list[MemoryEntry]) -> str:
    return "\n".join(e.content for e in entries)


def format_hot_prompt(user_id: str, app_id: str, path: Path | None = None) -> str:
    """hot 层注入格式（每轮都注入，保持极简）。"""
    entries = load_hot_entries(user_id, app_id, path)
    if not entries:
        return ""
    lines = ["# 核心约束（长期有效，优先级最高）"]
    for e in entries:
        lines.append(f"- {e.content}")
    return "\n".join(lines)
