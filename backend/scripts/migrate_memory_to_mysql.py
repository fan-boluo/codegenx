# -*- coding: utf-8 -*-
"""
P0-4 迁移脚本：旧 json 记忆文件（hot.json + warm_*.jsonl）→ MySQL agent_memory。

用法（backend 目录下）:
    python scripts/migrate_memory_to_mysql.py [--dry-run] [--drop-old-collection]

迁移规则（docs/Agent记忆系统设计方案v2.md §11 P0-4 / §0.3 #6）：
  - 只迁移 status=active 的旧条目（invalid/archived 历史不迁，计数上报）
  - 类型→层按 v2 分层归属（P0-6）：
      user_preference      → hot user_preference
      resource_path        → hot external_resource（上移）
      user_correction      → warm user_correction
      project_background   → warm project_background
    其余旧类型拒绝迁移（宁缺毋滥，计数上报）
  - 溯源统一 source_type=3（人工录入/迁移）、confidence=1.00（§4.3 允许人工录入进 hot）
  - hot slot_key = topic 规整化（slug），缺省用 ULID；warm slot_key = memory_id
  - ULID/created_at/hit_count/last_hit_at 原样保留（衰减与断点语义不回退）
  - warm 行 vector_synced_at=NULL：每日 sync_check 自动从 MySQL 全量重建
    agent_memory_warm collection；旧 warm_memories collection 不再使用
    （--drop-old-collection 时顺手删除）
  - 校验（§11 P0-4）：迁移前后条数一致 + 随机抽样内容比对

注意：source_type=3 的行即迁移行（在线提取为 1/2），校验按此口径统计。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from sqlalchemy import text  # noqa: E402

from db.mysql.session import session_maker  # noqa: E402
from shared import log  # noqa: E402
from shared.constants import DATA_ROOT_DIR  # noqa: E402
from codegenx.ai_service.memory.models import (  # noqa: E402
    LAYER_HOT,
    LAYER_WARM,
    SOURCE_MANUAL,
    estimate_text_tokens,
    new_ulid,
)

# 旧类型 → v2 (memory_type, layer)
_TYPE_MAP: dict[str, tuple[str, int]] = {
    "user_preference": ("user_preference", LAYER_HOT),
    "resource_path": ("external_resource", LAYER_HOT),
    "user_correction": ("user_correction", LAYER_WARM),
    "project_background": ("project_background", LAYER_WARM),
}

_SLOT_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")


def _to_mysql_dt(ts: str) -> str | None:
    """旧 ISO（含 +00:00 时区）→ MySQL DATETIME 字面量（UTC 去时区）。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _hot_slot_key(topic: str, memory_id: str) -> str:
    slug = _SLOT_SANITIZE_RE.sub("_", (topic or "").strip().lower()).strip("_")[:64]
    return slug or memory_id


def _load_old_entries(memory_dir: Path) -> list[dict]:
    """读取 hot.json + warm_*.jsonl 的原始条目（dict，坏行跳过）。"""
    raw: list[dict] = []
    hot_path = memory_dir / "hot.json"
    if hot_path.exists():
        try:
            data = json.loads(hot_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                raw.extend(d for d in data if isinstance(d, dict))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [warn] hot.json 读取失败: {exc}")
    for path in sorted(memory_dir.glob("warm_*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        raw.append(item)
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            print(f"  [warn] {path.name} 读取失败: {exc}")
    return raw


def _build_row(app_id: str, user_id: str, old: dict) -> tuple[dict, str | None]:
    """旧条目 → agent_memory 行参数；不可迁移返回 (原样, 原因)。"""
    content = str(old.get("content", "") or "").strip()
    if not content:
        return old, "empty_content"
    mapping = _TYPE_MAP.get(str(old.get("memory_type", "") or "").strip())
    if mapping is None:
        return old, "unknown_type"

    memory_type, layer = mapping
    memory_id = str(old.get("id") or "") or new_ulid()
    topic = str(old.get("topic", "") or "").strip()[:32]
    created = _to_mysql_dt(str(old.get("created_at", "") or ""))
    updated = _to_mysql_dt(str(old.get("updated_at", "") or "")) or created
    last_hit = _to_mysql_dt(str(old.get("last_accessed_at", "") or ""))
    try:
        hits = int(old.get("access_count") or 0)
    except (TypeError, ValueError):
        hits = 0

    row = {
        "a": app_id,
        "u": user_id,
        "mid": memory_id,
        "layer": layer,
        "t": memory_type,
        "slot": _hot_slot_key(topic, memory_id) if layer == LAYER_HOT else memory_id,
        "summary": content[:500],
        "content": json.dumps({"text": content, "topic": topic}, ensure_ascii=False),
        "tc": estimate_text_tokens(content) + 4,
        "sid": str(old.get("source_session_id", "") or "")[:64],
        "hits": max(0, hits),
        "last_hit": last_hit,
        "created": created,
        "updated": updated,
    }
    return row, None


_INSERT_SQL = text(
    "INSERT INTO agent_memory "
    "(app_id, user_id, memory_id, memory_layer, memory_type, subject, slot_key, active_slot, "
    " summary, content, token_cost, source_type, confidence, status, session_id, task_id, "
    " hit_count, last_hit_at, vector_synced_at, created_at, updated_at) "
    "VALUES (:a, :u, :mid, :layer, :t, 'self', :slot, 1, "
    " :summary, :content, :tc, :src, 1.00, 1, :sid, NULL, "
    " :hits, :last_hit, :synced, COALESCE(:created, NOW()), COALESCE(:updated, NOW()))"
)


def _finalize_row(row: dict, layer: int) -> dict:
    row = dict(row)
    row["src"] = SOURCE_MANUAL
    row["synced"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if layer == LAYER_HOT else None
    return row


async def migrate_tenant(app_id: str, user_id: str, dry_run: bool) -> dict:
    """迁移单个 (user, app)，返回统计。"""
    memory_dir = DATA_ROOT_DIR / user_id / app_id / "memory"
    stats = {
        "source_active": 0, "inserted": 0, "skipped_status": 0,
        "skipped_type": 0, "skipped_empty": 0, "dup": 0, "verify_fail": 0,
    }
    olds = _load_old_entries(memory_dir)
    rows: list[dict] = []
    for old in olds:
        if str(old.get("status", "active")) != "active":
            stats["skipped_status"] += 1
            continue
        stats["source_active"] += 1
        row, reason = _build_row(app_id, user_id, old)
        if reason == "unknown_type":
            stats["skipped_type"] += 1
            continue
        if reason == "empty_content":
            stats["skipped_empty"] += 1
            continue
        rows.append(_finalize_row(row, row["layer"]))

    if dry_run:
        stats["inserted"] = len(rows)
        return stats

    inserted_rows: list[dict] = []
    async with session_maker() as session:
        for row in rows:
            try:
                await session.execute(_INSERT_SQL, row)
                inserted_rows.append(row)
            except Exception as exc:  # noqa: BLE001 — 唯一键冲突=重复迁移，单行容错
                if "Duplicate entry" in str(exc):
                    stats["dup"] += 1
                else:
                    print(f"  [error] 插入失败 memory_id={row['mid']}: {exc}")
        await session.commit()
    stats["inserted"] = len(inserted_rows)

    # ── 校验：条数 + 抽样内容比对 ─────────────────────────────────────────────
    if inserted_rows:
        async with session_maker() as session:
            count_row = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM agent_memory "
                        "WHERE app_id = :a AND user_id = :u AND source_type = 3"
                    ),
                    {"a": app_id, "u": user_id},
                )
            ).first()
            stats["db_migrated_count"] = int(count_row[0] or 0)

            samples = random.sample(inserted_rows, min(5, len(inserted_rows)))
            for row in samples:
                db_row = (
                    await session.execute(
                        text("SELECT summary, content FROM agent_memory WHERE memory_id = :m"),
                        {"m": row["mid"]},
                    )
                ).first()
                if db_row is None:
                    stats["verify_fail"] += 1
                    continue
                summary_ok = str(db_row[0]) == row["summary"]
                try:
                    text_ok = json.loads(str(db_row[1])).get("text") == json.loads(row["content"]).get("text")
                except json.JSONDecodeError:
                    text_ok = False
                if not (summary_ok and text_ok):
                    stats["verify_fail"] += 1
    return stats


async def drop_old_collection() -> None:
    """删除 v1 的 warm_memories collection（Qdrant 瘦身重建后不再使用）。"""
    try:
        from db.qdrant.client import get_qdrant_client_manager
        client = get_qdrant_client_manager().client
        exists = await asyncio.to_thread(client.collection_exists, "warm_memories")
        if exists:
            await asyncio.to_thread(client.delete_collection, "warm_memories")
            print("已删除旧 collection: warm_memories")
        else:
            print("旧 collection warm_memories 不存在，跳过")
    except Exception as exc:  # noqa: BLE001 — Qdrant 不可用不阻断迁移
        print(f"[warn] 删除旧 collection 失败（可后续手动清理）: {exc}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="旧 json 记忆 → MySQL agent_memory 迁移")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--drop-old-collection", action="store_true", help="迁移后删除旧 warm_memories collection")
    args = parser.parse_args()

    if not DATA_ROOT_DIR.exists():
        print(f"数据目录不存在: {DATA_ROOT_DIR}")
        return 1

    tenants: list[tuple[str, str]] = []
    for user_dir in sorted(DATA_ROOT_DIR.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        for app_dir in sorted(user_dir.iterdir()):
            if app_dir.is_dir() and (app_dir / "memory").is_dir():
                tenants.append((app_dir.name, user_dir.name))

    if not tenants:
        print("未发现任何旧记忆目录（.data/{user}/{app}/memory/）")
        return 0

    print(f"发现 {len(tenants)} 个租户目录，dry_run={args.dry_run}\n")
    totals = {
        "source_active": 0, "inserted": 0, "skipped_status": 0,
        "skipped_type": 0, "skipped_empty": 0, "dup": 0, "verify_fail": 0,
    }
    failed = False
    for app_id, user_id in tenants:
        stats = await migrate_tenant(app_id, user_id, args.dry_run)
        print(f"[{user_id}/{app_id}] {json.dumps(stats, ensure_ascii=False)}")
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + int(value)
        if stats.get("verify_fail") or (
            not args.dry_run and stats["inserted"] != stats["source_active"] - stats["dup"]
        ):
            failed = True

    print("\n合计:", json.dumps(totals, ensure_ascii=False))
    if args.drop_old_collection and not args.dry_run:
        await drop_old_collection()

    if failed:
        print("\nRESULT: FAIL（存在校验不一致/计数不平，请检查上方日志）")
        return 2
    print("\nRESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    log.remove()
    log.add(sys.stderr, level="WARNING")
    sys.exit(asyncio.run(main()))
