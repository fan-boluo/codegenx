"""chat_message 表 DAO（async SQLAlchemy，raw SQL）。

职责：聊天消息的追加 / 水位增量读 / 会话最近消息查询 / 保留期清理。

存储约定：
- content 列 = 整条消息 dict 的 JSON 序列化（含 tool_calls / tool_call_id 等
  结构字段），读侧 json.loads 还原，保证与原 jsonl 文件时代完全同构；
- 超长消息外置（P1-13）：content 超过 _BLOB_THRESHOLD_BYTES 时，原文写本地
  blob 文件（.data/{user}/{app}/message_blobs/{session}/{seq}.json），行内
  content 只存 {"payload_ref": <路径>} 桩；读侧 _parse_content 透明还原，
  blob 文件丢失时返回占位 dict（记忆提取/历史展示不中断）；
- role 列冗余存储（user/assistant/tool），供 SQL 过滤与统计；
- seq 会话内单调递增：INSERT 前 SELECT MAX(seq) FOR UPDATE 串行化同会话
  并发写入（InnoDB 在 uk_session_seq 索引上加间隙锁），也是记忆提取水位；
- assistant 消息的 tool_calls.arguments 存储时简化为 {"参数名": null}
  （与原 jsonl 行为一致：上下文里保留完整参数，落库只留结构）；
- content_tokens 为 len//4 估算（本服务无本地 tokenizer）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from db.mysql.session import session_maker
from shared import log

# 超长消息外置阈值（字节）：普通消息直存，超限写 blob 防 MySQL 行膨胀
_BLOB_THRESHOLD_BYTES = 32 * 1024


def _blob_path(user_id, app_id, session_id: str, seq: int) -> Path:
    from shared.constants import DATA_ROOT_DIR
    return (
        DATA_ROOT_DIR / str(user_id) / str(app_id)
        / "message_blobs" / session_id / f"{seq}.json"
    )


class ChatMessageStore:
    """chat_message 表 DAO（单例，见 get_chat_message_store）。"""

    # === 写入 ===

    async def append_message(
        self, user_id: int | str, app_id: int | str, session_id: str, message: dict
    ) -> int:
        """追加一条聊天消息，返回分配的会话内 seq。不改动传入的 message 对象。"""
        stored = _storage_view(message)
        content_json = json.dumps(stored, ensure_ascii=False, default=str)
        content_bytes = len(content_json.encode("utf-8"))

        # P1-13 超长外置：先算 seq 再写 blob（路径含 seq）；写失败回退直存
        async with session_maker() as session:
            # MAX(seq) FOR UPDATE：同会话并发追加在 uk_session_seq 上串行化
            row = (
                await session.execute(
                    text(
                        "SELECT COALESCE(MAX(seq), 0) FROM chat_message "
                        "WHERE session_id = :s FOR UPDATE"
                    ),
                    {"s": session_id},
                )
            ).first()
            seq = int(row[0] or 0) + 1
            if content_bytes > _BLOB_THRESHOLD_BYTES:
                ref_path = _blob_path(user_id, app_id, session_id, seq)
                try:
                    ref_path.parent.mkdir(parents=True, exist_ok=True)
                    ref_path.write_text(content_json, encoding="utf-8")
                    # content_bytes/tokens 记原文体量（水位统计与漏斗口径一致）
                    content_json = json.dumps({"payload_ref": str(ref_path)}, ensure_ascii=False)
                except OSError as exc:
                    log.error("超长消息 blob 写盘失败，回退直存: {}", exc)
            role = str(message.get("role") or "user")
            await session.execute(
                text(
                    "INSERT INTO chat_message "
                    "(message_uid, user_id, session_id, app_id, seq, role, "
                    "content, content_bytes, content_tokens) "
                    "VALUES (:uid, :u, :s, :a, :q, :r, :c, :cb, :ct)"
                ),
                {
                    "uid": str(uuid.uuid4()),
                    "u": int(user_id),
                    "s": session_id,
                    "a": str(app_id),
                    "q": seq,
                    "r": role,
                    "c": content_json,
                    "cb": content_bytes,
                    "ct": content_bytes // 4,  # 估算 token（无本地 tokenizer）
                },
            )
            await session.commit()
        return seq

    # === 读取 ===

    async def read_since(
        self, session_id: str, after_seq: int = 0, limit: int | None = None
    ) -> list[tuple[int, dict]]:
        """按 seq 升序读取水位之后的消息，返回 [(seq, message_dict), ...]。

        供记忆提取增量消费；解析失败的行跳过（坏行不阻断水位推进）。
        """
        sql = (
            "SELECT seq, content FROM chat_message "
            "WHERE session_id = :s AND seq > :a ORDER BY seq ASC"
        )
        params: dict = {"s": session_id, "a": int(after_seq)}
        if limit is not None:
            sql += " LIMIT :l"
            params["l"] = int(limit)
        async with session_maker() as session:
            rows = (await session.execute(text(sql), params)).all()
        result: list[tuple[int, dict]] = []
        for seq, content in rows:
            message = _parse_content(content)
            if message is not None:
                result.append((int(seq), message))
        return result

    async def get_recent(
        self, user_id: int | str, session_id: str, limit: int = 50
    ) -> list[dict]:
        """读取会话最近 N 条消息（按 seq 升序返回），供前端会话历史展示。

        每条消息补 create_time / seq / user_id（与原 jsonl 时代响应字段对齐）。
        """
        async with session_maker() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT seq, user_id, content, created_at FROM chat_message "
                            "WHERE session_id = :s AND user_id = :u "
                            "ORDER BY seq DESC LIMIT :l"
                        ),
                        {"s": session_id, "u": int(user_id), "l": int(limit)},
                    )
                )
                .all()
            )
        result: list[dict] = []
        for seq, row_user_id, content, created_at in reversed(rows):
            message = _parse_content(content)
            if message is None:
                continue
            message["seq"] = int(seq)
            message["user_id"] = str(row_user_id)
            message["create_time"] = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or "")
            result.append(message)
        return result

    # === 清理 ===

    async def delete_before(self, cutoff: datetime) -> int:
        """物理删除 cutoff 之前的消息（保留期清理），返回删除行数。"""
        async with session_maker() as session:
            cur = await session.execute(
                text("DELETE FROM chat_message WHERE created_at < :c"),
                {"c": cutoff},
            )
            await session.commit()
            return int(cur.rowcount or 0)


# === 内部 ===

def _storage_view(message: dict) -> dict:
    """存储视图：assistant 消息的 tool_calls.arguments 简化为 {"参数名": null}。

    与原 SessionManager.append_chat_history_message 行为一致 —— 上下文中的
    完整参数不落库；返回浅拷贝，不改动调用方的 message 对象。
    """
    tool_calls = message.get("tool_calls")
    if message.get("role") != "assistant" or not isinstance(tool_calls, list):
        return message
    stored = dict(message)
    simplified_calls = []
    for call in tool_calls:
        if isinstance(call, dict) and call.get("type") == "function" and isinstance(call.get("function"), dict):
            func = call["function"]
            arguments_str = func.get("arguments", "")
            if arguments_str:
                try:
                    args_dict = json.loads(arguments_str)
                    simplified_args = {k: None for k in args_dict}
                    func_view = {**func, "arguments": json.dumps(simplified_args, ensure_ascii=False)}
                except json.JSONDecodeError:
                    func_view = func
                simplified_calls.append({**call, "function": func_view})
            else:
                simplified_calls.append(call)
        else:
            simplified_calls.append(call)
    stored["tool_calls"] = simplified_calls
    return stored


def _parse_content(raw) -> dict | None:
    """content 列 JSON → message dict；坏行返回 None 并记日志。

    P1-13：payload_ref 桩在此透明还原为原文；blob 文件丢失返回占位
    dict（提取/展示拿到的是空正文而非异常）。
    """
    if isinstance(raw, dict):  # 防御：驱动直接返回反序列化结果
        data = raw
    elif not raw:
        return None
    else:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning("chat_message.content 解析失败，跳过该行")
            return None
    if not isinstance(data, dict):
        return None
    ref = data.get("payload_ref")
    if not ref:
        return data
    try:
        return json.loads(Path(str(ref)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("超长消息 blob 读取失败（返回占位）: {} | {}", ref, exc)
        return {"payload_ref": str(ref), "content_missing": True, "role": "user", "content": ""}


# === 全局单例 ===

_global_chat_message_store: ChatMessageStore | None = None


def get_chat_message_store() -> ChatMessageStore:
    """全局聊天消息存储单例（在线写入与离线提取/清理共用）。"""
    global _global_chat_message_store
    if _global_chat_message_store is None:
        _global_chat_message_store = ChatMessageStore()
    return _global_chat_message_store
