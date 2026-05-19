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

from .paths import get_topics_dir

# ── Budget constants (mirrors Claude Code) ────────────────────────────────────
MAX_FILES_RETURNED = 5
MAX_FILE_BYTES = 4 * 1024        # 4 KB per file
MAX_SESSION_BYTES = 60 * 1024    # 60 KB total per session
FRONTMATTER_SCAN_LINES = 30      # only read this many lines for frontmatter
MAX_TOPIC_FILES = 200            # hard cap on directory scan


@dataclass
class TopicFile:
    path: Path
    description: str = ""
    file_type: str = "topic"
    mtime: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name


# ── Frontmatter parsing ───────────────────────────────────────────────────────

def _parse_frontmatter(lines: list[str]) -> tuple[str, str]:
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
        return "", "topic"

    description = ""
    file_type = "topic"
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        m = re.match(r"^description:\s*(.+)", stripped)
        if m:
            description = m.group(1).strip()
        m = re.match(r"^type:\s*(.+)", stripped)
        if m:
            file_type = m.group(1).strip()
    return description, file_type


# ── Directory scan ────────────────────────────────────────────────────────────

def scan_topic_files(topics_dir: Path | None = None) -> list[TopicFile]:
    """
    Scan the warm topics directory. Mirrors memoryScan.ts scanMemoryFiles().

    Reads only the first FRONTMATTER_SCAN_LINES per file (cheap).
    Returns up to MAX_TOPIC_FILES files sorted by mtime descending.
    """
    topics_dir = topics_dir or get_topics_dir()
    if not topics_dir.exists():
        return []

    results: list[TopicFile] = []
    for path in topics_dir.glob("*.md"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            raw = path.read_text(encoding="utf-8")
            lines = raw.splitlines()[:FRONTMATTER_SCAN_LINES]
            description, file_type = _parse_frontmatter(lines)
            results.append(TopicFile(
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

_STOPWORDS = frozenset(
    "the a an is are was were be to of and or in on at for with "
    "this that it i you what how can do does did will would could "
    "should have has had not no".split()
)


def _keyword_overlap(query: str, description: str) -> int:
    """
    Simple keyword overlap score.

    Production uses a Sonnet sideQuery with JSON schema output.
    Replace this function with an LLM call to `selectRelevantMemories()`
    for semantic rather than lexical matching.
    """
    q_words = set(re.findall(r"\w+", query.lower())) - _STOPWORDS
    d_words = set(re.findall(r"\w+", description.lower())) - _STOPWORDS
    return len(q_words & d_words)


# ── Public recall API ─────────────────────────────────────────────────────────

def find_relevant_topics(
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
    topics = scan_topic_files(topics_dir)

    # Exclude already-surfaced files
    topics = [t for t in topics if t.name not in already_surfaced]

    # Score and sort by relevance
    scored = sorted(
        ((t, _keyword_overlap(query, t.description)) for t in topics),
        key=lambda x: x[1],
        reverse=True,
    )

    results: list[tuple[str, str]] = []
    session_bytes = session_bytes_used

    for topic, score in scored:
        if len(results) >= MAX_FILES_RETURNED:
            break
        # If we have at least one result and this file has no overlap, stop
        if score == 0 and results:
            break
        if session_bytes >= MAX_SESSION_BYTES:
            break
        try:
            content = topic.path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Per-file byte cap
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            content = encoded[:MAX_FILE_BYTES].decode("utf-8", errors="ignore")

        session_bytes += len(content.encode("utf-8"))
        results.append((topic.name, content))

    return results


def format_warm_memory_prompt(relevant: list[tuple[str, str]]) -> str:
    """Format retrieved topic files as a system-prompt section."""
    if not relevant:
        return ""
    parts = ["# Relevant Memory Topics"]
    for name, content in relevant:
        parts.append(f"\n## {name}\n{content.strip()}")
    return "\n".join(parts)
