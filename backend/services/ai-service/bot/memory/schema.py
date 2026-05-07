from dataclasses import dataclass, field
from enum import Enum


class MemoryType(str,Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class MemorySearchResult:
    """
    Attributes:
        id: Optional chunk id
        text: Full chunk text
        snippet: Text snippet,一段text的子串[:200] ...
        score: Search score (0-1)
        type: long/short

    """
    id: str | None
    text: str | None
    snippet: str
    score: float
    type: MemoryType
    access_count: int | None
    importance:float | None
    version: int | None
    category:str | None  # 对应的是库里面的memory_type字段
    vector:list[float] | None = field(default=None)  # 可选的向量字段，便于后续直接使用



