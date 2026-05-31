"""
Warm memory tier — topic .md files recalled per query.

Mirrors Claude Code's findRelevantMemories() / memoryScan.ts:

  Scan:    Read first FRONTMATTER_SCAN_LINES of each *.md in topics/
           Parse YAML frontmatter for `type:` and `description:` fields
           Sort by mtime descending (most recently updated first)

  Select:  Score each file by keyword overlap between its description
           and the current user query (replaces the Sonnet sideQuery
           used in production — swap find_relevant_topics() body with
           a real LLM call when ready)

  Budget:  ≤ MAX_FILES_RETURNED files, ≤ MAX_FILE_BYTES per file,
           ≤ MAX_SESSION_BYTES across the whole session

Topic file format  (~/.bot/memory/topics/my-topic.md):
  ---
  type: topic
  description: Brief one-line description shown to the selector
  ---
  # Title
  ... content ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set,Optional,List,Tuple
import json
from bot.llm.async_client import AsyncLLMClient
from shared.constants import get_memory_dir
from memory.prompts import  FIND_RELEVANT_MD_USER_PROMPT, FIND_RELEVANT_MD_SYSTEM_PROMPT

# ── Budget constants (mirrors Claude Code) ────────────────────────────────────
MAX_FILES_RETURNED = 5
MAX_FILE_BYTES = 4 * 1024        # 4 KB per file
MAX_SESSION_BYTES = 60 * 1024    # 60 KB total per session
FRONTMATTER_SCAN_LINES = 30      # only read this many lines for frontmatter
MAX_TOPIC_FILES = 200            # hard cap on directory scan
CANDIDATE_PRE_FILTER_COUNT = 20  # 先用关键词选出20个候选，再让LLM评分

@dataclass
class TopicFile:
    name:str
    path: Path
    description: str = ""
    file_type: str = "topic"
    mtime: float = 0.0

    # @property
    # def name(self) -> str:
    #     return self.path.name


# ── Frontmatter parsing ───────────────────────────────────────────────────────

def _parse_frontmatter(lines: list[str]) -> tuple[str,str, str]:
    """
    Extract (description, type) from YAML-style frontmatter.

    Expected:
      ---
      type: topic
      description: One-line summary
      ---

    Falls back to ("", "topic") if no frontmatter found.
    """
    if not lines or lines[0].strip() != "---":
        return "","", "topic"

    description = ""
    file_type = "topic"
    name=""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        m = re.match(r"^name:\s*(.+)", stripped)
        if m:
            name = m.group(1).strip()
        m = re.match(r"^description:\s*(.+)", stripped)
        if m:
            description = m.group(1).strip()
        m = re.match(r"^type:\s*(.+)", stripped)
        if m:
            file_type = m.group(1).strip()
    return name,description, file_type


# ── Directory scan ────────────────────────────────────────────────────────────

def scan_topic_files(app_id:str,topics_dir: Path | None = None) -> list[TopicFile]:
    """
    Scan the warm topics directory. Mirrors memoryScan.ts scanMemoryFiles().

    Reads only the first FRONTMATTER_SCAN_LINES per file (cheap).
    Returns up to MAX_TOPIC_FILES files sorted by mtime descending.
    """
    topics_dir = topics_dir or get_memory_dir(app_id)
    topics_dir.mkdir(parents=True, exist_ok=True)

    results: list[TopicFile] = []
    for path in topics_dir.glob("*.md"):
        if not path.is_file() or path.name =="CLAUDE.md":
            continue

        try:
            stat = path.stat()
            raw = path.read_text(encoding="utf-8")
            lines = raw.splitlines()[:FRONTMATTER_SCAN_LINES]
            name,description, file_type = _parse_frontmatter(lines)
            results.append(TopicFile(
                name=name,
                path=path,
                # Fall back to a humanised stem if no description found
                description=description or path.stem.replace("-", " ").replace("_", " "),
                file_type=file_type,
                mtime=stat.st_mtime,
            ))
        except OSError:
            continue

    results.sort(key=lambda f: f.mtime, reverse=True)
    return results[:MAX_TOPIC_FILES]


# ── Relevance scoring ─────────────────────────────────────────────────────────

def get_char_bigrams(text: str) -> Set[str]:
    """将文本拆分为字符级 bigram（连续两个字符）"""
    # 去除空格，保持连续性（可选：你也可以保留空格，但通常没必要）
    text = text.replace(" ", "")
    if len(text) < 2:
        return set(text)  # 单个字符退化为字符本身
    return {text[i:i+2] for i in range(len(text)-1)}

def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    if not set1 or not set2:
        return 0.0
    inter = len(set1 & set2)
    union = len(set1 | set2)
    return inter / union if union != 0 else 0.0

def _keyword_overlap(query: str, description: str) -> float:
    """
    基于字符 bigram 的 Jaccard 相似度（范围 0~1）
    直接替代原来的单词重叠计数，返回浮点数，用于排序。
    """
    q_grams = get_char_bigrams(query)
    d_grams = get_char_bigrams(description)
    return jaccard_similarity(q_grams, d_grams)


# ── Public recall API ─────────────────────────────────────────────────────────

async def find_relevant_topics(
        app_id:str,
    query: str,
    already_surfaced: set[str] | None = None,
    session_bytes_used: int = 0,
    topics_dir: Path | None = None,
) -> list[tuple[str, str]]:
    """
    Find warm topic files relevant to *query*.

    Mirrors findRelevantMemories() — returns list of (filename, content).

    Args:
        query:             User query (or first N words) for scoring.
        already_surfaced:  Filenames already injected this session (dedup).
        session_bytes_used: Bytes already consumed by warm memory this session.
        topics_dir:        Override topics directory (useful for testing).

    Returns:
        List of (filename, content) for the selected files.
    """
    already_surfaced = already_surfaced or set()
    topics = scan_topic_files(app_id,topics_dir)

    # Exclude already-surfaced files
    topics = [t for t in topics if t.name not in already_surfaced]



    # Score and sort by relevance
    scored = sorted(
        ((t, _keyword_overlap(query, t.description)) for t in topics),
        key=lambda x: x[1],
        reverse=True,
    )[:CANDIDATE_PRE_FILTER_COUNT]
    candidates = [t[0] for t in scored]

    scores: dict[str, int] = {t.name: s for t, s in scored}

    if len(topics) > MAX_FILES_RETURNED:
        # 大于要求的个数，再用llm排
        # 第二步：LLM 精排
        llm_client = AsyncLLMClient()

        scores = await _llm_score_memories(query, candidates, llm_client)

        # 按LLM分数降序排序
        candidates.sort(key=lambda t: scores.get(t.name, 0), reverse=True)



    results: list[tuple[str, str]] = []
    session_bytes = session_bytes_used

    for topic in candidates:
        if len(results) >= MAX_FILES_RETURNED:
            break
        if scores.get(topic.name, 0) == 0 and results:
            break  # 已有一个结果且当前分数为0，停止
        if session_bytes >= MAX_SESSION_BYTES:
            break
        try:
            content = topic.path.read_text(encoding="utf-8")
        except OSError:
            continue

        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            content = encoded[:MAX_FILE_BYTES].decode("utf-8", errors="ignore")

        session_bytes += len(content.encode("utf-8"))
        results.append((topic.name, content))

    return results


async def _llm_score_memories(query: str, topics, llm_client) -> dict:
    """调用LLM返回文件名到分数的映射，失败时回退到关键词分数"""
    if not topics:
        return {}

    # 构建用户消息中的文件列表
    files_list = "\n".join(
        f"- {t.name}：{t.description or '无描述'}" for t in topics
    )
    user_prompt = FIND_RELEVANT_MD_USER_PROMPT.format(query=query, files_list=files_list)
    system_prompt = FIND_RELEVANT_MD_SYSTEM_PROMPT

    try:
        content = await llm_client.invoke(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,   # 确定性输出
            max_tokens=1024,
        )

        # 解析JSON
        content = content.strip("` \n")
        if content.startswith("json"):
            content = content[4:]
        data = json.loads(content)
        return data.get("scores", {})
    except Exception as e:
        # 降级：使用关键词分数
        print(f"LLM评分失败，回退到关键词评分: {e}")
        return {t.name: _keyword_overlap(query, t.description) for t in topics}


def format_warm_memory_prompt(relevant: list[tuple[str, str]]) -> str:
    """Format retrieved topic files as a system-prompt section."""
    if not relevant:
        return ""
    parts = ["# Relevant Memory Topics"]
    for name, content in relevant:
        parts.append(f"\n## {name}\n{content.strip()}")
    return "\n".join(parts)
