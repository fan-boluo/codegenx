"""
Prompt templates for memory extraction and recall.

Mirrors:
  src/services/SessionMemory/prompts.ts  — session notes template + update prompt
  src/memdir/memdir.ts                   — buildSearchingPastContextSection()
"""
from __future__ import annotations

# ── Session memory template ────────────────────────────────────────────────────
# Mirrors DEFAULT_SESSION_MEMORY_TEMPLATE in prompts.ts.
# Section structure MUST be preserved by the extractor — headers and italic
# description lines are the scaffold; only the content below them changes.

SESSION_MEMORY_TEMPLATE = """\
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


# ── Extraction prompt ─────────────────────────────────────────────────────────

def build_extraction_prompt(
    messages: list[dict],
    current_notes: str,
    notes_path: str,
) -> str:
    """
    Build the prompt sent to the session-memory extraction subagent.
    Mirrors buildSessionMemoryUpdatePrompt() in prompts.ts.

    In production this is sent to a Sonnet sideQuery with FileEdit tool access.
    The mock extractor in session.py uses it for reference only.
    """
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
    """Format the last 40 messages for inclusion in an extraction prompt."""
    parts: list[str] = []
    for msg in messages[-40:]:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            # tool results — join content fields
            content = "; ".join(str(item.get("content", "")) for item in content)
        if isinstance(content, str) and content.strip():
            parts.append(f"[{role}]: {content[:400]}")
        # Summarise tool calls compactly
        for tc in msg.get("tool_calls", []):
            parts.append(f"[tool_call]: {tc.get('name', '?')}({tc.get('input', {})})")
    return "\n".join(parts)

FIND_RELEVANT_MD_SYSTEM_PROMPT = """
你是一个专门评估记忆文件与用户查询相关性的助手。  
你将收到一个**用户查询**和一个**记忆文件列表**，每个文件包含文件名和描述。  
请为每个文件输出一个 0 到 10 之间的整数分数，表示该文件与用户查询的相关性：

- 10：非常相关，文件内容极有可能直接回答查询或提供关键上下文。
- 7‑9：相当相关，文件内容明显有助于回答查询。
- 4‑6：部分相关，可能包含与查询间接相关的信息。
- 1‑3：微弱相关，仅有非常宽泛或偶然的联系。
- 0：完全无关。

**评分时请考虑：**
- 文件名和描述中是否包含查询的关键词或同义词。
- 描述中提到的主题是否与用户查询的意图一致（不仅仅是字面匹配）。
- 如果查询涉及“如何做 X”，那么描述中包含“X 的步骤/最佳实践”的文件分数应更高。

只输出一个 JSON 对象，格式如下（不要输出任何其他文字）：
{
  "scores": {
    "文件名1.md": 分数,
    "文件名2.md": 分数,
    ...
  }
}
"""

FIND_RELEVANT_MD_USER_PROMPT = """
用户查询：{query}

记忆文件列表：
{files_list}

请评估每个文件与查询的相关性，输出 JSON 分数。
"""