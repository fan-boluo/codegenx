"""
warm 层存储 —— 话题记忆（warm_*.jsonl 滚动文件，json 事实源）。

职责：
  - append          追加条目，单文件超 20MB 滚动到下一序号（warm_meta 记录滚动状态）
  - scan_entries    按 id 增量扫描（ULID 字典序 = 追加序，供 Qdrant 同步用）
  - mark_invalid    批量软删除（重写命中文件，tmp+replace 原子替换）
  - record_access   召回命中登记（内存缓冲，条数阈值触发批量回写，避免高频重写大文件）
  - archive_before  按截止时间归档：json 条目移入按月 zip，源文件重写，返回归档 id 列表

不负责：向量化（embedding.py）、检索打分（retriever.py）、生命周期调度（lifecycle.py）。
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from shared import log

from codegenx.ai_service.memory.models import MemoryEntry, STATUS_ACTIVE
from codegenx.ai_service.memory.paths import (
    WARM_FILE_MAX_BYTES,
    get_archive_dir,
    get_warm_file_path,
    get_warm_meta_path,
    list_warm_files,
    parse_warm_seq,
)

ACCESS_FLUSH_THRESHOLD = 64  # 访问回写缓冲触发条数


class WarmStore:
    """单个 app 的 warm 层文件存储（进程内单例，见 get_warm_store）。"""

    def __init__(self, user_id: str, app_id: str) -> None:
        # 记忆按 用户/项目 两级隔离，所有路径调用都带 user_id
        self.user_id = user_id
        self.app_id = app_id
        # id → (last_accessed_at, 访问增量)
        self._access_buffer: dict[str, tuple[str, int]] = {}
        self._meta_lock = False  # 单进程写场景，append 顺序由调用方（writer 串行）保证

    # ── meta / 滚动 ────────────────────────────────────────────────────────────

    def _load_meta(self) -> dict:
        path = get_warm_meta_path(self.user_id, self.app_id)
        if not path.exists():
            return {"seq": 1}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"seq": 1}

    def _save_meta(self, meta: dict) -> None:
        path = get_warm_meta_path(self.user_id, self.app_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ── 追加 ───────────────────────────────────────────────────────────────────

    def append(self, entry: MemoryEntry) -> Path:
        """追加一条 warm 记忆，超滚动阈值时切换下一序号文件。"""
        entry.layer = "warm"
        meta = self._load_meta()
        seq = int(meta.get("seq", 1))
        target = get_warm_file_path(self.user_id, self.app_id, seq)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and target.stat().st_size >= WARM_FILE_MAX_BYTES:
            seq += 1
            meta["seq"] = seq
            target = get_warm_file_path(self.user_id, self.app_id, seq)

        with open(target, "a", encoding="utf-8") as fh:
            fh.write(entry.to_json_line() + "\n")
        self._save_meta(meta)
        return target

    # ── 扫描 ───────────────────────────────────────────────────────────────────

    def scan_entries(
        self,
        after_id: str = "",
        status: str | None = STATUS_ACTIVE,
    ) -> list[MemoryEntry]:
        """按追加顺序扫描；after_id 之后（不含）的条目，status=None 表示不过滤。"""
        result: list[MemoryEntry] = []
        for path in list_warm_files(self.user_id, self.app_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                log.warning("warm 文件读取失败 {}:{}", path, exc)
                continue
            for line in lines:
                entry = MemoryEntry.from_json_line(line)
                if entry is None:
                    continue
                if after_id and entry.id <= after_id:
                    continue
                if status is not None and entry.status != status:
                    continue
                result.append(entry)
        return result

    def find_by_ids(self, ids: list[str]) -> dict[str, MemoryEntry]:
        wanted = set(ids)
        found: dict[str, MemoryEntry] = {}
        for path in list_warm_files(self.user_id, self.app_id):
            if not wanted:
                break
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                entry = MemoryEntry.from_json_line(line)
                if entry is not None and entry.id in wanted:
                    found[entry.id] = entry
                    wanted.discard(entry.id)
        return found

    # ── 软删除（批量重写） ──────────────────────────────────────────────────────

    def mark_invalid(self, ids: list[str], reason: str) -> int:
        """批量置 invalid：重写命中文件（tmp+replace），返回命中条数。"""
        targets = set(ids)
        if not targets:
            return 0
        hit = 0
        for path in list_warm_files(self.user_id, self.app_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            rewritten = False
            out: list[str] = []
            for line in lines:
                entry = MemoryEntry.from_json_line(line)
                if entry is not None and entry.id in targets:
                    entry.mark_invalid(reason)
                    out.append(entry.to_json_line())
                    hit += 1
                    rewritten = True
                else:
                    out.append(line)
            if rewritten:
                self._atomic_write(path, out)
        return hit

    # ── 访问时间回写（缓冲） ────────────────────────────────────────────────────

    def record_access(self, entry_ids: list[str]) -> None:
        """召回命中登记：先入内存缓冲，达阈值批量回写。"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for eid in entry_ids:
            prev = self._access_buffer.get(eid)
            if prev:
                self._access_buffer[eid] = (now, prev[1] + 1)
            else:
                self._access_buffer[eid] = (now, 1)
        if len(self._access_buffer) >= ACCESS_FLUSH_THRESHOLD:
            self.flush_access()

    def flush_access(self) -> int:
        """把缓冲的访问信息回写 jsonl（重写命中文件），返回回写条数。"""
        if not self._access_buffer:
            return 0
        buffered = self._access_buffer
        self._access_buffer = {}
        written = 0
        for path in list_warm_files(self.user_id, self.app_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            rewritten = False
            out: list[str] = []
            for line in lines:
                entry = MemoryEntry.from_json_line(line)
                if entry is not None and entry.id in buffered:
                    ts, delta = buffered[entry.id]
                    entry.last_accessed_at = ts
                    entry.access_count += delta
                    out.append(entry.to_json_line())
                    written += 1
                    rewritten = True
                else:
                    out.append(line)
            if rewritten:
                self._atomic_write(path, out)
        if written:
            log.debug("warm 访问信息回写 {} 条", written)
        return written

    # ── 归档 ───────────────────────────────────────────────────────────────────

    def archive_before(self, cutoff_iso: str) -> list[str]:
        """把 created_at 早于 cutoff 的条目移入按月 zip，返回归档 id 列表。

        调用方（lifecycle）拿到 id 后负责删除 Qdrant 点位。
        """
        archive_dir = get_archive_dir(self.user_id, self.app_id)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived_ids: list[str] = []
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

        for path in list_warm_files(self.user_id, self.app_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            keep: list[str] = []
            archive_by_month: dict[str, list[str]] = {}
            for line in lines:
                entry = MemoryEntry.from_json_line(line)
                if entry is None:
                    keep.append(line)  # 坏行原样保留，不做数据丢失决策
                    continue
                if entry.created_at and entry.created_at < cutoff_iso:
                    month = entry.created_at[:7].replace("-", "")  # YYYYMM
                    entry.status = "archived"
                    entry.invalid_at = now_iso
                    entry.invalid_reason = "archive:90d"
                    archive_by_month.setdefault(month, []).append(entry.to_json_line())
                    archived_ids.append(entry.id)
                else:
                    keep.append(line)

            if archive_by_month:
                for month, payload in archive_by_month.items():
                    zip_path = archive_dir / f"warm_{month}.zip"
                    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr(f"warm_{month}.jsonl", "\n".join(payload) + "\n")
                # 源文件重写为仅保留条目
                if keep:
                    self._atomic_write(path, keep)
                else:
                    path.unlink(missing_ok=True)
                    seq = parse_warm_seq(path)
                    if seq is not None and seq == 1:
                        # 保留空的首文件，避免扫描/追加路径判空
                        path.touch()
        return archived_ids

    # ── 内部 ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _atomic_write(path: Path, lines: list[str]) -> None:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(path)


@lru_cache(maxsize=256)
def get_warm_store(user_id: str, app_id: str) -> WarmStore:
    """按 用户/项目 维度的进程内单例（访问缓冲需要共享）。"""
    return WarmStore(user_id, app_id)
