"""
会话摘要服务（上下文工程）—— 从记忆系统整体移交而来。

职责边界：会话内摘要服务于「压缩边界」，属于上下文工程；
跨会话的持久记忆（hot/warm）属于记忆系统（memory/ 包），二者不再耦合。

机制（原 memory/session.py，行为保持不变）：
  should_extract()   → token 增长 + 工具调用阈值判断
  fire_extract()     → 非阻塞后台 Task 做摘要，写 session 目录下的摘要文件
  load()             → 同步读取当前摘要（CompactionEngine 压缩前调用，
                        作为 Path A 快速通道的上下文恢复来源）
"""

from __future__ import annotations

import asyncio
import re
import traceback
from pathlib import Path

from shared import log
from shared.constants import get_session_dir
from codegenx.ai_service.llm.resilience import SCENARIO_SUMMARY, resilient_invoke
from codegenx.ai_service.utils.context_utils import rough_tokens
from codegenx.ai_service.utils.config import config

# ── 阈值 ─────────────────────────────────────────────────────────────────────
COMPACT_CONTEXT_WINDOW = config.get_agent().context_max_tokens or 8000
MIN_TOKENS_TO_INIT = 6000           # 上下文达到该规模后开始跟踪
MIN_TOKENS_BETWEEN_UPDATES = 150    # 新增 token 超过该值才允许再次提取
TOOL_CALLS_BETWEEN_UPDATES = 5      # 或新增工具调用超过该值


def _count_tool_calls_since(messages: list[dict], since_idx: int) -> int:
    """统计 since_idx 之后 assistant 消息里的 tool_call 数量。"""
    count = 0
    for msg in messages[since_idx:]:
        if msg.get("role") == "assistant":
            count += len(msg.get("tool_calls", []))
    return count


def get_session_summary_path(user_id: str, app_id: str, session_id: str) -> Path:
    """会话摘要文件：.data/{userId}/{appId}/session/{session_id}/SUMMARY.md"""
    return get_session_dir(user_id, app_id) / session_id / "SUMMARY.md"


# ── 会话摘要模板与提取提示词（原 memory/prompts.py 的 session 部分） ─────────

SESSION_SUMMARY_TEMPLATE = """\
# Session Title
_A short and distinctive 5-10 word descriptive title for the session._

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task Specification
_What did the user ask to build or find? Key design decisions or explanatory context._

# Key Files and Topics
_What are the important files, functions, or topics discussed? Brief notes on each._

# Errors and Corrections
_Errors encountered and how they were fixed. What approaches failed and should not be tried again._

# Learnings
_What has worked well? What should be avoided? Do not duplicate items from other sections._

# Key Results
_If the user asked for a specific output (answer, table, document), include the exact result here._

# Worklog
_Step by step, what was attempted and done? Very terse summary for each step._
"""


def build_summary_extraction_prompt(
    messages: list[dict],
    current_notes: str,
    notes_path: str,
) -> str:
    """构建会话摘要更新的系统提示词。"""
    conversation = _format_messages_for_extraction(messages)
    return (
        f"IMPORTANT: These instructions are NOT part of the actual conversation. "
        f"Do NOT reference note-taking in the notes content.\n\n"
        f"Based on the conversation below, update the session notes at {notes_path}.\n\n"
        f"Current notes:\n<current_notes>\n{current_notes or '(empty)'}\n</current_notes>\n\n"
        f"Conversation:\n<conversation>\n{conversation}\n</conversation>\n\n"
        f"Update every section that has new information. Be terse but info-dense. "
        f"Preserve all section headers and italic description lines exactly."
    )


def _format_messages_for_extraction(messages: list[dict]) -> str:
    """取最近 40 条消息格式化为提取 prompt 输入。"""
    parts: list[str] = []
    for msg in messages[-40:]:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "; ".join(str(item.get("content", "")) for item in content)
        if isinstance(content, str) and content.strip():
            parts.append(f"[{role}]: {content[:400]}")
        for tc in msg.get("tool_calls", []):
            parts.append(f"[tool_call]: {tc.get('name', '?')}({tc.get('input', {})})")
    return "\n".join(parts)


class SummaryState:
    """会话摘要阈值状态（原 SessionSummaryService 实例字段，迁入 SessionContext 持有）。

    这是真·会话状态（非伪会话对象）：服务全局一份，此状态随会话生灭。
    """

    def __init__(self) -> None:
        # 阈值状态
        self.initialized = False
        self.tokens_at_last_extract = 0
        self.extract_msg_idx = 0
        # 并发保护：同一会话的提取任务不重叠
        self.extracting = False
        self.extract_task: asyncio.Task | None = None


class SessionSummaryService:
    """
    全局无状态会话摘要服务（P2 服务化，docs/SystemApp架构设计.md §4.3）。

    生命周期：
      1. compact_after_turn() 判定 should_extract(state, messages) 后 fire_extract()。
      2. CompactionService 压缩前调用 load(ids) 取最新摘要（Path A 快速通道）。
    阈值状态由调用方（SessionContext.summary_state: SummaryState）持有。
    """

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def should_extract(self, state: SummaryState, messages: list[dict]) -> bool:
        """是否应触发一次后台摘要提取。"""
        if state.extracting:
            return False  # 永不重叠

        current = rough_tokens(messages)

        if not state.initialized:
            if current < MIN_TOKENS_TO_INIT:
                return False
            state.initialized = True

        growth = current - state.tokens_at_last_extract
        if growth < MIN_TOKENS_BETWEEN_UPDATES:
            return False

        tool_calls = _count_tool_calls_since(messages, state.extract_msg_idx)
        return (
            tool_calls >= TOOL_CALLS_BETWEEN_UPDATES
            or growth >= MIN_TOKENS_BETWEEN_UPDATES * 3
        )

    def fire_extract(
        self,
        state: SummaryState,
        messages: list[dict],
        *,
        user_id: str,
        app_id: str,
        session_id: str,
    ) -> None:
        """调度非阻塞后台摘要任务（快照消息列表，主对话可继续变更）。"""
        if state.extracting:
            return
        state.extracting = True
        state.extract_msg_idx = len(messages)
        state.tokens_at_last_extract = rough_tokens(messages)
        path = get_session_summary_path(user_id, app_id, session_id)
        state.extract_task = asyncio.create_task(
            self._extract(state, list(messages), path)
        )

    def load(self, *, user_id: str, app_id: str, session_id: str) -> str:
        """同步读取当前会话摘要文件。"""
        path = get_session_summary_path(user_id, app_id, session_id)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ── 内部提取 ──────────────────────────────────────────────────────────────

    async def _extract(self, state: SummaryState, messages: list[dict], path: Path) -> None:
        """后台任务：摘要 messages → 覆盖写 SUMMARY.md；失败非致命。"""
        try:
            current_notes = self._load_from_path(path)
            summary = await self._summarize(messages, current_notes, str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(summary, encoding="utf-8")
            log.debug("session summary 压缩完成:{}", path)
        except Exception:
            log.error("session summary 压缩异常")
            log.error(traceback.format_exc())
        finally:
            state.extracting = False
            state.extract_task = None

    @staticmethod
    def _load_from_path(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    async def _summarize(
        self,
        messages: list[dict],
        current_notes: str,
        notes_path: str,
    ) -> str:
        """调用大模型更新结构化会话摘要；空输出回退到现有笔记。"""
        system_prompt = build_summary_extraction_prompt(
            messages=messages,
            current_notes=current_notes,
            notes_path=notes_path,
        )

        # P1：走韧性层（summary 场景模型链 + 熔断/重试/降级）
        updated_notes = await resilient_invoke(
            SCENARIO_SUMMARY,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "请根据上述指令更新会话笔记。"},
            ],
            temperature=0.0,
            max_tokens=4096,
        )

        if not updated_notes or not updated_notes.strip():
            return current_notes if current_notes else ""

        return updated_notes.strip()
