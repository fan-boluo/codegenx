from __future__ import annotations

from typing import Any
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import threading

from sqlalchemy.ext.asyncio import AsyncSession

from shared.config.log_config import log
from shared.constants import (
    CHAT_HISTORY_ARCHIVE_PREFIX,
    CHAT_HISTORY_CACHE_TURNS,
    CHAT_HISTORY_FILE_NAME,
    CHAT_HISTORY_MAX_BYTES,
    UserRole,
    get_session_dir,
)
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.throw_utils import ThrowUtils
from shared.orm.app import App
from shared.schema.chat_history import ChatHistoryQueryRequest

from core.auth_proxy import JWTUser

_FILE_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_CHAT_CACHE: dict[int, deque[ChatHistoryRecord]] = {}


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

    def _history_file(self, app_id: int) -> Path:
        return self._session_dir(app_id) / CHAT_HISTORY_FILE_NAME

    def _history_archive_files(self, app_id: int) -> list[Path]:
        session_dir = self._session_dir(app_id)
        return sorted(
            file_path
            for file_path in session_dir.glob(f"{CHAT_HISTORY_ARCHIVE_PREFIX}*.jsonl")
            if file_path.name != CHAT_HISTORY_FILE_NAME
        )

    def _history_files(self, app_id: int) -> list[Path]:
        history_files = self._history_archive_files(app_id)
        active_file = self._history_file(app_id)
        if active_file.exists():
            history_files.append(active_file)
        return history_files

    def _cache_records(self, app_id: int) -> list[ChatHistoryRecord]:
        with _CACHE_LOCK:
            return list(_CHAT_CACHE.get(app_id, deque()))

    def _append_cache(self, record: ChatHistoryRecord) -> None:
        with _CACHE_LOCK:
            cache = _CHAT_CACHE.setdefault(record.app_id, deque(maxlen=CHAT_HISTORY_CACHE_TURNS))
            cache.append(record)

    def _clear_cache(self, app_id: int) -> None:
        with _CACHE_LOCK:
            _CHAT_CACHE.pop(app_id, None)

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
            id=int(payload["id"]),
            message=str(payload.get("message", "")),
            message_type=str(payload.get("message_type", "")),
            app_id=int(payload["app_id"]),
            user_id=int(payload["user_id"]),
            create_time=datetime.fromisoformat(str(create_time)) if create_time else self._utc_now(),
            update_time=datetime.fromisoformat(str(update_time)) if update_time else self._utc_now(),
        )

    def _record_to_payload(self, record: ChatHistoryRecord) -> dict[str, object]:
        return {
            "id": record.id,
            "message": record.message,
            "message_type": record.message_type,
            "app_id": record.app_id,
            "user_id": record.user_id,
            "create_time": record.create_time.isoformat(),
            "update_time": record.update_time.isoformat(),
        }

    def _read_app_records(self, app_id: int) -> list[ChatHistoryRecord]:
        records: list[ChatHistoryRecord] = []
        with _FILE_LOCK:
            for history_file in self._history_files(app_id):
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
                            log.warning("skip invalid chat history line appId={} file={} error={}", app_id, history_file, exc)
        return records

    def _read_all_records(self) -> list[ChatHistoryRecord]:
        runtime_root = get_session_dir("main").parent.parent
        if not runtime_root.exists():
            return []

        records: list[ChatHistoryRecord] = []
        for history_file in runtime_root.glob(f"*/session/{CHAT_HISTORY_FILE_NAME}"):
            try:
                app_id = int(history_file.parent.parent.name)
            except ValueError:
                continue
            records.extend(self._read_app_records(app_id))
        return records

    def _rotate_history_file_if_needed(self, app_id: int, incoming_bytes: int) -> None:
        history_file = self._history_file(app_id)
        if not history_file.exists():
            return
        if history_file.stat().st_size + incoming_bytes <= CHAT_HISTORY_MAX_BYTES:
            return

        timestamp = self._utc_now().strftime("%Y%m%d%H%M%S")
        archived_path = history_file.with_name(f"{CHAT_HISTORY_ARCHIVE_PREFIX}{timestamp}.jsonl")
        history_file.replace(archived_path)

    def cleanup_expired_files(self, retention_days: int = 3) -> int:
        """删除超过 retention_days 天的历史文件，返回删除文件数。"""
        from datetime import timedelta

        now = self._utc_now()
        cutoff = now - timedelta(days=retention_days)
        deleted = 0
        # 清理所有 app 目录下的 session 文件夹
        runtime_root = get_session_dir("main").parent.parent
        if not runtime_root.exists():
            return 0

        history_glob = f"*/session/{CHAT_HISTORY_FILE_NAME}"
        archive_glob = f"*/session/{CHAT_HISTORY_ARCHIVE_PREFIX}*.jsonl"

        with _FILE_LOCK:
            for pattern in (history_glob, archive_glob):
                for history_file in runtime_root.glob(pattern):
                    try:
                        mtime = datetime.fromtimestamp(history_file.stat().st_mtime, tz=UTC).replace(tzinfo=None)
                        if mtime < cutoff:
                            history_file.unlink()
                            deleted += 1
                    except Exception as exc:
                        log.warning("cleanup_expired_files: skip file={} error={}", history_file, exc)
        if deleted:
            log.info("cleanup_expired_files: removed {} expired history files (retention={}d)", deleted, retention_days)
        return deleted

    def _append_record(self, record: ChatHistoryRecord) -> None:
        history_file = self._history_file(record.app_id)
        payload = self._record_to_payload(record)
        serialized = json.dumps(payload, ensure_ascii=False) + "\n"
        with _FILE_LOCK:
            self._rotate_history_file_if_needed(record.app_id, len(serialized.encode("utf-8")))
            history_file = self._history_file(record.app_id)
            with history_file.open("a", encoding="utf-8") as file:
                file.write(serialized)
        self._append_cache(record)

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

    def _sort_records(self, records: list[ChatHistoryRecord], sort_field: str | None, sort_order: str | None) -> list[ChatHistoryRecord]:
        allowed_fields = {
            "id",
            "message",
            "message_type",
            "app_id",
            "user_id",
            "create_time",
            "update_time",
        }
        order_field = sort_field if sort_field in allowed_fields else "create_time"
        reverse = sort_order != "ascend"
        return sorted(
            records,
            key=lambda item: (getattr(item, order_field), item.id),
            reverse=reverse,
        )

    async def delete_by_app_id(self, app_id: int) -> bool:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用ID不能为空")
        history_files = self._history_files(app_id)
        with _FILE_LOCK:
            for history_file in history_files:
                if history_file.exists():
                    history_file.unlink()
        self._clear_cache(app_id)
        return True

    async def list_app_chat_history_by_page(
        self,
        app_id: int,
        page_size: int,
        last_create_time: datetime | None,
        login_user: Any,
    ) -> list[ChatHistoryRecord]:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用ID不能为空")
        ThrowUtils.throw_if(page_size <= 0 or page_size > 50, ErrorCode.PARAMS_ERROR, "页面大小必须在1-50之间")
        app = await self.db.get(App, app_id)
        ThrowUtils.throw_if(app is None, ErrorCode.NOT_FOUND_ERROR, "应用不存在")
        is_admin = login_user.user_role == UserRole.ADMIN.value
        is_creator = app.user_id == login_user.user_id
        ThrowUtils.throw_if(not is_admin and not is_creator, ErrorCode.NO_AUTH_ERROR, "无权查看该应用的对话历史")

        cached_records = self._cache_records(app_id)
        if cached_records and last_create_time is None and len(cached_records) >= page_size:
            records = self._sort_records(cached_records, "create_time", "descend")
            return records[:page_size]

        records = self._read_app_records(app_id)
        if last_create_time is not None:
            records = [item for item in records if item.create_time < last_create_time]
        records = self._sort_records(records, "create_time", "descend")
        return records[:page_size]

    async def list_all_chat_history_by_page(self, query_request: ChatHistoryQueryRequest) -> list[ChatHistoryRecord]:
        records = [item for item in self._read_all_records() if self._match_query(item, query_request)]
        records = self._sort_records(records, query_request.sort_field, query_request.sort_order)
        start = (query_request.page_num - 1) * query_request.page_size
        end = start + query_request.page_size
        return records[start:end]