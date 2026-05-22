from dataclasses import field, dataclass

from threading import Lock

from memory import append_to_hot_memory, format_warm_memory_prompt, find_relevant_topics, load_hot_memory, \
    format_hot_memory_prompt

_MEMORY_MANAGER_SINGLETON: "MemoryManager | None" = None
_MEMORY_MANAGER_LOCK = Lock()

@dataclass
class MemoryManager:
    """
    Multi-tier memory manager.

    Hot tier  — loads ~/.bot/memory/MEMORY.md on every turn (global, persistent).
    Warm tier — keyword-recalls relevant topic files from ~/.bot/memory/topics/
                on each query; deduplicates within the session.

    Session-tier memory is handled separately by SessionMemory in engine.py
    and injected during compaction, not here.
    """

    session_id: str = ""
    app_id:str = ""

    # Per-session warm-memory dedup state
    _surfaced: set[str] = field(default_factory=set)
    _session_bytes: int = field(default=0)

    def load(self, query: str = "") -> str:
        """
        Assemble memory prompt for the current turn.

        Args:
            query: User query for warm-memory keyword matching.
                   Pass "" to skip warm-tier lookup (e.g. first turn).
        """
        ignore_memory = bool(getattr(query, "ignore_memory", False))
        if ignore_memory:
            return ""
        parts: list[str] = []

        # ── Hot tier ──────────────────────────────────────────────────────────
        hot_content = load_hot_memory(self.app_id)
        hot_prompt = format_hot_memory_prompt(hot_content)
        if hot_prompt:
            parts.append(hot_prompt)

        # ── Warm tier ─────────────────────────────────────────────────────────
        if query:
            relevant = find_relevant_topics(
                app_id=self.app_id,
                query=query,
                already_surfaced=self._surfaced,
                session_bytes_used=self._session_bytes,
            )
            for name, content in relevant:
                self._surfaced.add(name)
                self._session_bytes += len(content.encode("utf-8"))
            warm_prompt = format_warm_memory_prompt(relevant)
            if warm_prompt:
                parts.append(warm_prompt)

        return "\n\n".join(parts)

    def remember(self, fact: str) -> None:
        """
        Persist a fact to ~/.bot/memory/MEMORY.md (hot tier, survives restarts).
        Mirrors the old in-process remember() contract but backed by disk.
        """
        append_to_hot_memory(fact)

    def clear_session_warm_cache(self) -> None:
        """Reset per-session warm-memory dedup state (useful between sessions)."""
        self._surfaced.clear()
        self._session_bytes = 0

def get_memory_manager() -> MemoryManager | None:
    global _MEMORY_MANAGER_SINGLETON
    if _MEMORY_MANAGER_SINGLETON is None:
        _MEMORY_MANAGER_SINGLETON = MemoryManager()

    return _MEMORY_MANAGER_SINGLETON


def build_memory(self, memory) -> str:
    """将 list[MemorySearchResult] 按 s9 分组（user/feedback/project/reference）转为清晰可读的字符串。
    过滤掉 todo（任务状态，不属于跨会话记忆）。
    """
    if not memory:
        return "暂无跨会话记忆"

    # s9 分组映射：语义类型 → 展示组别
    _CATEGORY_GROUP = {
        "preference": "user",
        "identity": "user",
        "feedback": "feedback",
        "decision": "project",
        "fact": "project",
        "principle": "project",
        "reference": "reference",
    }
    _GROUP_LABEL = {
        "user": "用户偏好 / User",
        "feedback": "反馈记录 / Feedback & Corrections",
        "project": "项目约定 / Project Facts",
        "reference": "外部资源 / References",
    }
    _GROUP_ORDER = ["user", "feedback", "project", "reference"]

    groups: dict[str, list] = {g: [] for g in _GROUP_ORDER}
    for item in memory:
        category = str(item.category or "").lower()
        if category == "todo":  # s9: task state 不进记忆 prompt
            continue
        group = _CATEGORY_GROUP.get(category, "project")
        groups[group].append(item)

    lines = [
        "# 跨会话记忆（只包含无法从当前代码直接推导的信息）",
        "",
    ]
    has_content = False
    for group_key in _GROUP_ORDER:
        items = groups[group_key]
        if not items:
            continue
        has_content = True
        lines.append(f"## [{_GROUP_LABEL[group_key]}]")
        for item in items:
            importance_str = f"{float(item.importance or 0):.2f}" if item.importance is not None else "无"
            lines.append(f"- [{item.type.value}] 重要度={importance_str}  {item.text or item.snippet}")
        lines.append("")

    if not has_content:
        return "暂无跨会话记忆"
    return "\n".join(lines)