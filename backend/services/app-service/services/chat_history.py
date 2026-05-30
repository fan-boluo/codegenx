from __future__ import annotations

from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import threading

from sqlalchemy.ext.asyncio import AsyncSession

from shared.config.log_config import log
from shared.constants import (
    UserRole,
    get_session_dir,
)
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.throw_utils import ThrowUtils
from shared.orm.app import App
from shared.schema.chat_history import ChatHistoryQueryRequest

from core.auth_proxy import JWTUser

_FILE_LOCK = threading.Lock()

# AI 端 SessionManager 按 session 分文件写入: chat_history_{session_id}.jsonl
CHAT_HISTORY_FILE_GLOB = "chat_history_*.jsonl"
HISTORY_MAX_RECORDS = 50


def _extract_session_id(filename: str) -> str:
    """chat_history_{sid}.jsonl 或 chat_history_{sid}_N.jsonl → sid"""
    stem = Path(filename).stem
    core = stem[len("chat_history_"):]
    return re.sub(r"_\d+$", "", core)


@dataclass(slots=True)
class ChatHistoryRecord:
    id: int
    message: str
    message_type: str
    app_id: int
    user_id: int
    create_time: datetime
    update_time: datetime


class ChatHistoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _session_dir(self, app_id: int) -> Path:
        session_dir = get_session_dir(app_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _history_files(self, app_id: int) -> list[Path]:
        return sorted(self._session_dir(app_id).glob(CHAT_HISTORY_FILE_GLOB))

    def _utc_now(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def _record_from_payload(self, payload: dict[str, object]) -> ChatHistoryRecord:
        # 兼容旧格式 {"role": "user"/"assistant", "content": "..."}
        if "id" not in payload and "role" in payload:
            role = str(payload.get("role", "user"))
            message_type = "user" if role in ("user", "human") else "assistant"
            now = self._utc_now()
            return ChatHistoryRecord(
                id=0,
                message=str(payload.get("content", "")),
                message_type=message_type,
                app_id=0,
                user_id=0,
                create_time=now,
                update_time=now,
            )
        create_time = payload.get("create_time")
        update_time = payload.get("update_time")
        return ChatHistoryRecord(
            id=int(payload.get("id", 0)),
            message=str(payload.get("message", "")),
            message_type=str(payload.get("message_type", "")),
            app_id=int(payload.get("app_id", 0)),
            user_id=int(payload.get("user_id", 0)),
            create_time=datetime.fromisoformat(str(create_time)) if create_time else self._utc_now(),
            update_time=datetime.fromisoformat(str(update_time)) if update_time else self._utc_now(),
        )

    def _read_records_from_files(self, files: list[Path]) -> list[ChatHistoryRecord]:
        records: list[ChatHistoryRecord] = []
        with _FILE_LOCK:
            for history_file in files:
                if not history_file.exists():
                    continue
                with history_file.open("r", encoding="utf-8") as file:
                    for line in file:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(self._record_from_payload(json.loads(line)))
                        except Exception as exc:
                            log.warning("skip invalid chat history line file={} error={}", history_file, exc)
        return records

    def _group_files_by_session(self, app_id: int) -> dict[str, list[Path]]:
        """按 session_id 分组所有历史文件，每个 session 按文件 mtime 排序。"""
        groups: dict[str, list[Path]] = {}
        for file in self._history_files(app_id):
            sid = _extract_session_id(file.name)
            groups.setdefault(sid, []).append(file)
        for file_list in groups.values():
            file_list.sort(key=lambda f: f.stat().st_mtime)
        return groups

    def _recent_session_files(self, app_id: int, current_session_id: str) -> list[Path]:
        """返回当前 session + 上一个 session 的所有文件。"""
        groups = self._group_files_by_session(app_id)
        if not groups:
            return []

        # 按每个 session 最新文件的 mtime 降序排列
        sorted_sessions = sorted(
            groups.items(),
            key=lambda item: max(f.stat().st_mtime for f in item[1]),
            reverse=True,
        )

        # 将 current session 提到第一位
        current_files: list[Path] = []
        other_files: list[Path] = []
        for sid, files in sorted_sessions:
            if sid == current_session_id:
                current_files = files
            elif not other_files:
                other_files = files

        return current_files + other_files

    def _match_query(self, record: ChatHistoryRecord, query_request: ChatHistoryQueryRequest) -> bool:
        if query_request.id is not None and record.id != query_request.id:
            return False
        if query_request.message and query_request.message not in record.message:
            return False
        if query_request.message_type and record.message_type != query_request.message_type:
            return False
        if query_request.app_id is not None and record.app_id != query_request.app_id:
            return False
        if query_request.user_id is not None and record.user_id != query_request.user_id:
            return False
        if query_request.last_create_time is not None and record.create_time >= query_request.last_create_time:
            return False
        return True

    async def delete_by_app_id(self, app_id: int) -> bool:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用ID不能为空")
        history_files = self._history_files(app_id)
        with _FILE_LOCK:
            for history_file in history_files:
                if history_file.exists():
                    history_file.unlink()
        return True

    async def list_app_chat_history(
        self,
        app_id: int,
        session_id: str,
        login_user: Any,
    ) -> list[ChatHistoryRecord]:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用ID不能为空")
        ThrowUtils.throw_if(not session_id, ErrorCode.PARAMS_ERROR, "会话ID不能为空")
        app = await self.db.get(App, app_id)
        ThrowUtils.throw_if(app is None, ErrorCode.NOT_FOUND_ERROR, "应用不存在")
        is_admin = login_user.user_role == UserRole.ADMIN.value
        is_creator = app.user_id == login_user.user_id
        ThrowUtils.throw_if(not is_admin and not is_creator, ErrorCode.NO_AUTH_ERROR, "无权查看该应用的对话历史")

        files = self._recent_session_files(app_id, session_id)
        records = self._read_records_from_files(files)
        records.sort(key=lambda r: r.create_time, reverse=True)
        return records[:HISTORY_MAX_RECORDS]

    async def list_app_chat_history_by_page_with_query(
        self,
        query_request: ChatHistoryQueryRequest,
    ) -> list[ChatHistoryRecord]:
        records = [item for item in self._read_records_from_files(self._history_files(query_request.app_id)) if self._match_query(item, query_request)]
        records.sort(key=lambda r: (r.create_time, r.id), reverse=True)
        start = (query_request.page_num - 1) * query_request.page_size
        end = start + query_request.page_size
        return records[start:end]
