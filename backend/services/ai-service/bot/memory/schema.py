from dataclasses import dataclass
from enum import Enum


class MemoryType(str,Enum):
    LONG = "long"
    SHORT = "short"
    USER = "user"
    SOUL = "soul"
    IDENTITY = "identity"

class MemorySource(str, Enum):
    """Memory source type (matches TS MemorySource)."""
    MEMORY = "memory"
    SESSIONS = "sessions"
    MANUAL = "manual"


@dataclass
class MemorySearchResult:
    """
    Memory search result (matches TS MemorySearchResult).

    Attributes:
        path: Relative file path
        start_line: Start line number (1-indexed)
        end_line: End line number (1-indexed)
        score: Search score (0-1)
        snippet: Text snippet,一段text的子串[:200] ...
        source: Source type (memory | sessions | manual)
        citation: Optional citation string
        id: Optional chunk id (used by BuiltinMemoryManager)
        text: Full chunk text (used by BuiltinMemoryManager, alias for snippet)
    """
    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    source: MemorySource
    citation: str | None = None
    id: str | None = None
    text: str | None = None


