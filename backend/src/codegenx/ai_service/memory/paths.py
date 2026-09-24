"""
记忆系统文件布局（json 为事实源）。

记忆目录按 用户/项目 两级隔离：.data/{userId}/{appId}/memory

  {memory_dir}/
    hot.json                 # hot 层：核心约束（始终注入，token 预算 ≤2K）
    warm_meta.json           # warm 层滚动元信息（当前序号、总字节数）
    warm_000001.jsonl        # warm 层话题记忆（追加写，单文件 ≤20MB 滚动）
    warm_000002.jsonl
    archive/                 # 归档目录（按月 zip）
      warm_202601.zip

路径解析全部走本模块，避免散落的字符串拼接。
"""
from __future__ import annotations

import re
from pathlib import Path

from shared.constants import get_memory_dir

WARM_FILE_MAX_BYTES = 20 * 1024 * 1024  # 单个 jsonl 滚动阈值

_WARM_FILE_RE = re.compile(r"^warm_(\d{6})\.jsonl$")


def get_hot_store_path(user_id: str, app_id: str) -> Path:
    """hot 层事实源：{memory_dir}/hot.json"""
    return get_memory_dir(user_id, app_id) / "hot.json"


def get_hot_memory_path(user_id: str, app_id: str) -> Path:
    """旧 hot.py（MEMORY.md 时代）的兼容入口，待阶段3删除旧链路后移除。"""
    return get_hot_store_path(user_id, app_id)


def get_warm_meta_path(user_id: str, app_id: str) -> Path:
    """warm 层滚动元信息：{memory_dir}/warm_meta.json"""
    return get_memory_dir(user_id, app_id) / "warm_meta.json"


def get_warm_file_path(user_id: str, app_id: str, seq: int) -> Path:
    """warm 层数据文件：{memory_dir}/warm_{seq:06d}.jsonl"""
    return get_memory_dir(user_id, app_id) / f"warm_{seq:06d}.jsonl"


def get_archive_dir(user_id: str, app_id: str) -> Path:
    """归档目录：{memory_dir}/archive/"""
    return get_memory_dir(user_id, app_id) / "archive"


def list_warm_files(user_id: str, app_id: str) -> list[Path]:
    """按序号升序列出全部 warm 数据文件（不含 meta/archive）。"""
    root = get_memory_dir(user_id, app_id)
    if not root.exists():
        return []
    result: list[tuple[int, Path]] = []
    for path in root.iterdir():
        m = _WARM_FILE_RE.match(path.name)
        if m and path.is_file():
            result.append((int(m.group(1)), path))
    result.sort()
    return [p for _, p in result]


def parse_warm_seq(path: Path) -> int | None:
    """从文件名解析序号，非 warm 数据文件返回 None。"""
    m = _WARM_FILE_RE.match(path.name)
    return int(m.group(1)) if m else None
