"""
记忆条目数据模型 —— json 事实源的统一结构。

一条记忆（MemoryEntry）同时落两处：
  - json/jsonl 文件（事实源，负责持久与审计）
  - Qdrant warm_memories collection（检索层，只读加速，可随时重建）

ULID 作为主键：48bit 毫秒时间戳 + 80bit 随机，字典序即时间序，
增量扫描（watermark 推进）依赖该性质按 id 比较。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict

# ── 记忆状态 ──────────────────────────────────────────────────────────────────
STATUS_ACTIVE = "active"      # 正常可检索
STATUS_INVALID = "invalid"    # 软删除（冲突被覆盖 / 30 天未访问）
STATUS_ARCHIVED = "archived"  # 已归档（json 移入 zip，Qdrant 物理删除）

# ── 记忆类型与权重（rerank 的类型因子）───────────────────────────────────────
MEMORY_TYPE_WEIGHTS: dict[str, float] = {
    "user_correction": 1.0,     # 用户纠正：最高优先
    "user_preference": 0.8,     # 用户偏好
    "project_background": 0.6,  # 项目背景
    "resource_path": 0.5,       # 资源路径
}


def _now_iso() -> str:
    """UTC ISO-8601 带时区，秒级精度即可（展示与生命周期判断用）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def estimate_text_tokens(text: str) -> int:
    """粗略 token 估算（字符数÷4），与 utils.context_utils.rough_tokens 同口径。"""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ── ULID ─────────────────────────────────────────────────────────────────────
_B32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32

# 同毫秒单调递增：保证「追加顺序 = ULID 字典序」，增量扫描（id 比较）的前提
_ulid_lock = threading.Lock()
_ulid_last_value = 0


def new_ulid(ts_ms: int | None = None) -> str:
    """生成 26 位 ULID（同毫秒内单调递增，多线程安全）。"""
    global _ulid_last_value
    with _ulid_lock:
        if ts_ms is None:
            ts_ms = int(time.time() * 1000)
        ts_part = ts_ms & ((1 << 48) - 1)
        value = (ts_part << 80) | secrets.randbits(80)
        if value <= _ulid_last_value and (value >> 80) <= (_ulid_last_value >> 80):
            value = _ulid_last_value + 1
        _ulid_last_value = value
    chars = []
    for _ in range(26):
        chars.append(_B32_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


_B32_INDEX = {ch: i for i, ch in enumerate(_B32_ALPHABET)}


def ulid_to_uuid(ulid: str) -> str:
    """ULID（128bit）→ UUID 字符串，确定性可逆映射。

    Qdrant 点位 ID 只接受无符号整数或 UUID，json 侧的 ULID 主键
    在向量库边界统一转成 UUID（payload 里仍保留原始 ULID）。
    """
    value = 0
    for ch in ulid:
        value = (value << 5) | _B32_INDEX[ch]
    return str(uuid.UUID(int=value))


@dataclass
class MemoryEntry:
    """一条跨会话记忆。字段与 init/memory_schema.sql 的检索 payload 对齐。"""
    id: str = field(default_factory=new_ulid)
    layer: str = "warm"                     # hot / warm（hot 也用同结构存 hot.json）
    topic: str = ""                         # 话题标签，召回展示用
    memory_type: str = "project_background" # MEMORY_TYPE_WEIGHTS 的 key
    content: str = ""                       # 记忆正文（一句话事实）
    status: str = STATUS_ACTIVE
    source_session_id: str = ""
    # 兼容旧 embedding 调用方的 user 维度（当前单租户部署留空）
    user_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_accessed_at: str = field(default_factory=_now_iso)
    access_count: int = 0
    invalid_at: str = ""                    # 置为 invalid/archived 的时间
    invalid_reason: str = ""

    # ── 序列化 ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "MemoryEntry | None":
        line = line.strip()
        if not line:
            return None
        try:
            return cls.from_dict(json.loads(line))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    # ── 派生属性 ──────────────────────────────────────────────────────────────

    @property
    def type_weight(self) -> float:
        return MEMORY_TYPE_WEIGHTS.get(self.memory_type, 0.3)

    def touch(self) -> None:
        """召回命中后更新访问信息（jsonl 回写由 warm_store 缓冲处理）。"""
        self.access_count += 1
        self.last_accessed_at = _now_iso()

    def mark_invalid(self, reason: str) -> None:
        self.status = STATUS_INVALID
        self.invalid_at = _now_iso()
        self.invalid_reason = reason
        self.updated_at = _now_iso()
