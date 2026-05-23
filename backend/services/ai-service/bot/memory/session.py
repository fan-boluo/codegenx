"""
Session memory tier — periodic background extraction.

Mirrors Claude Code's src/services/SessionMemory/sessionMemory.ts:

  shouldExtractMemory()   → check token-growth + tool-call thresholds
  extractSessionMemory()  → sequential async, fires as a background Task,
                            writes ~/.bot/sessions/<id>/MEMORY.md
  load()                  → sync read of the current MEMORY.md

Architecture:
  - One SessionMemory instance per QueryEngine (same lifetime as the session).
  - fire_extract() is non-blocking: schedules an asyncio.Task so the main
    response stream is never delayed by extraction I/O.
  - Extraction failures are silently swallowed (non-fatal).
  - The written file is read back by compact.py during context compaction so
    the model retains context across the compaction boundary.

Swap _summarize_messages() for a real sideQuery(Sonnet) call to reproduce
Claude Code's production behaviour.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from llm.async_client import AsyncLLMClient
from .paths import get_session_memory_path
from .prompts import SESSION_MEMORY_TEMPLATE, build_extraction_prompt

# ── Thresholds (scaled-down; mirrors DEFAULT_SESSION_MEMORY_CONFIG) ───────────
# Production: minimumMessageTokensToInit = 10_000
#             minimumTokensBetweenUpdate  =  5_000
#             toolCallsBetweenUpdates     =      3
MIN_TOKENS_TO_INIT = 300           # start tracking once context reaches this
MIN_TOKENS_BETWEEN_UPDATES = 150   # extract after this many new tokens
TOOL_CALLS_BETWEEN_UPDATES = 3     # …or after this many tool calls


# ── Token estimation ──────────────────────────────────────────────────────────

def _rough_tokens(messages: list[dict]) -> int:
    """Rough token estimate: total characters / 4 (no tiktoken dependency)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(item.get("content", ""))) for item in content)
        for tc in msg.get("tool_calls", []):
            total += len(str(tc.get("input", "")))
    return total // 4


def _count_tool_calls_since(messages: list[dict], since_idx: int) -> int:
    """Count assistant tool-call blocks after message index *since_idx*."""
    count = 0
    for msg in messages[since_idx:]:
        if msg.get("role") == "assistant":
            count += len(msg.get("tool_calls", []))
    return count


# ── SessionMemory class ───────────────────────────────────────────────────────

class SessionMemory:
    """
    Per-session background extractor.

    Lifecycle:
      1. QueryEngine creates one instance at __init__ time.
      2. After each completed turn, engine calls should_extract() then
         fire_extract() when warranted.
      3. compact.py calls load() to get the latest summary before trimming.
    """

    def __init__(
        self,
            app_id:str,
        session_id: str,
        memory_path: Path | None = None,
    ) -> None:
        self.app_id = app_id
        self.session_id = session_id
        self._path = memory_path or get_session_memory_path(app_id,session_id)

        # Thresholds state
        self._initialized = False
        self._tokens_at_last_extract = 0
        self._extract_msg_idx = 0   # message index when last extraction started

        # Concurrency guard — mirrors the sequential() wrapper in Claude Code
        self._extracting = False

    # ── Public API ────────────────────────────────────────────────────────────

    def should_extract(self, messages: list[dict]) -> bool:
        """
        Return True when a background extraction should be fired.
        Mirrors shouldExtractMemory() in sessionMemory.ts.

        Conditions (both must hold):
          • Token growth since last extraction ≥ MIN_TOKENS_BETWEEN_UPDATES
          • Tool calls since last extraction  ≥ TOOL_CALLS_BETWEEN_UPDATES
            OR token growth is 3× the normal threshold (natural break)
        """
        if self._extracting:
            return False  # never overlap

        current = _rough_tokens(messages)

        if not self._initialized:
            if current < MIN_TOKENS_TO_INIT:
                return False
            self._initialized = True

        growth = current - self._tokens_at_last_extract
        if growth < MIN_TOKENS_BETWEEN_UPDATES:
            return False

        tool_calls = _count_tool_calls_since(messages, self._extract_msg_idx)
        return (
            tool_calls >= TOOL_CALLS_BETWEEN_UPDATES
            or growth >= MIN_TOKENS_BETWEEN_UPDATES * 3
        )

    def fire_extract(self, messages: list[dict]) -> None:
        """
        Schedule background extraction as an asyncio Task (non-blocking).

        Mirrors the sequential(async function extractSessionMemory()) pattern:
        a snapshot of *messages* is taken so the main conversation can continue
        mutating its list without affecting the extraction.
        """
        if self._extracting:
            return
        self._extracting = True
        self._extract_msg_idx = len(messages)
        self._tokens_at_last_extract = _rough_tokens(messages)
        # Fire-and-forget; errors handled inside _extract()
        asyncio.create_task(self._extract(list(messages)))

    def load(self) -> str:
        """Synchronously read the current session MEMORY.md."""
        if not self._path.exists():
            return ""
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ── Internal extraction ───────────────────────────────────────────────────

    async def _extract(self, messages: list[dict]) -> None:
        """
        Background task: summarise *messages* → write MEMORY.md.

        Mirrors runForkedAgent({ querySource: 'session_memory' }).
        Production: call sideQuery(Sonnet) with the update prompt and
        give it FileEditTool access to write the notes directly.
        """
        try:
            current_notes = self.load()
            summary = await _session_summarize(messages, current_notes,str(self._path))
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(summary, encoding="utf-8")
        except Exception:
            pass  # extraction failure is non-fatal
        finally:
            self._extracting = False


# ── Mock summariser ────────────────────────────────────────────────────────────
# Replace with a real sideQuery(Sonnet) call for production.
# Signature mirrors sideQuery({ model, system, messages, output_format }).

async def _session_summarize(
    messages: list[dict],
    current_notes: str,
    notes_path: str,
) -> str:
    """
    使用大模型从对话中提取结构化 session 摘要。

    Args:
        messages: 完整的对话历史（list of message dicts）
        current_notes: 当前已有的 MEMORY.md 内容（可能为空）
        notes_path: 记忆文件路径（用于提示词中指示更新目标）

    Returns:
        更新后的 session 记忆内容（完整的新 MEMORY.md 文本）
    """
    # 构建提取提示词（系统指令）
    system_prompt = build_extraction_prompt(
        messages=messages,
        current_notes=current_notes,
        notes_path=notes_path,
    )

    # 初始化 LLM 客户端
    client = AsyncLLMClient()

    # 调用大模型（使用 Sonnet 级别模型，temperature=0 保证稳定输出）
    updated_notes = await client.invoke(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请根据上述指令更新会话笔记。"}
        ],
        temperature=0.0,
        max_tokens=4096,
    )

    # 如果模型返回空或失败，回退到当前已有的笔记
    if not updated_notes or not updated_notes.strip():
        return current_notes if current_notes else ""

    return updated_notes.strip()

async def _mock_summarize(messages: list[dict], current_notes: str) -> str:
    """
    Produce a structured session summary from *messages*.

    Uses simple heuristics: first user message → title, last exchange →
    current state, all tool calls → worklog entries.

    In production swap this body for:
        response = await side_query(
            model=SONNET,
            system=build_extraction_prompt(messages, current_notes, path),
            messages=[...],
            output_format="text",
        )
        return response
    """
    user_msgs = [m for m in messages if m.get("role") == "user"]
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]

    first_user = _text(user_msgs[0]) if user_msgs else "Conversation"
    last_user = _text(user_msgs[-1]) if user_msgs else ""
    last_assist = _text(assistant_msgs[-1]) if assistant_msgs else ""

    title = first_user[:60].replace("\n", " ")

    # Worklog from recent messages
    worklog_lines: list[str] = []
    for msg in messages[-12:]:
        role = msg.get("role", "")
        text = _text(msg)
        if role == "user" and text:
            worklog_lines.append(f"- [user] {text[:80]}")
        elif role == "assistant" and text:
            worklog_lines.append(f"- [assistant] {text[:80]}")
        for tc in msg.get("tool_calls", []):
            worklog_lines.append(
                f"- [tool] {tc.get('name', '?')}({tc.get('input', {})})"
            )

    # Start from existing notes or the blank template
    base = current_notes if current_notes else SESSION_MEMORY_TEMPLATE

    base = _set_section(base, "Session Title", title)
    base = _set_section(
        base,
        "Current State",
        f"Last user: {last_user[:200]}\nLast assistant: {last_assist[:200]}",
    )
    base = _set_section(base, "Task Specification", first_user[:400])
    base = _set_section(base, "Worklog", "\n".join(worklog_lines))

    return base


# ── Template helpers ──────────────────────────────────────────────────────────

def _text(msg: dict) -> str:
    """Extract plain-text content from a message dict."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "; ".join(
            str(item.get("content", "")) for item in content
        ).strip()
    return ""


def _set_section(template: str, header: str, new_content: str) -> str:
    """
    Replace the body of a section in a SESSION_MEMORY_TEMPLATE-style document.

    Each section looks like:
      # Header
      _italic description line_
      <body content here>

    We keep the header and italic line, replace everything up to the next
    top-level header (or end of string).
    """
    pattern = rf"(# {re.escape(header)}\n_[^\n]*_\n)(.*?)(?=\n# |\Z)"
    replacement = rf"\g<1>\n{new_content.strip()}\n"
    return re.sub(pattern, replacement, template, flags=re.DOTALL)
