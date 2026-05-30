import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
import re

from bot.utils.log_utils import log
from shared.constants import get_session_dir


def _sanitize_app_id(app_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(app_id or "main").strip())
    return normalized or "main"


PROJECT_DIR = Path(__file__).parent.parent

MAX_FILE_BYTES = 1 * 1024 * 1024  # 单文件最大 1MB

_CHAT_HISTORY_PREFIX = "chat_history_"


class SessionManager:

    def __init__(self, app_id: str = "main"):
        self.app_id = _sanitize_app_id(app_id)
        self.session_dir = get_session_dir(self.app_id)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _turn_snapshot_file(self, turn_id: str) -> Path:
        return self.session_dir / f"turn_{turn_id}__snapshot.json"

    def _chat_history_file(self, session_id: str, index: int = -1) -> Path:
        """index=-1 → chat_history_{sid}.jsonl, index>=0 → chat_history_{sid}_{index}.jsonl"""
        if index >= 0:
            return self.session_dir / f"{_CHAT_HISTORY_PREFIX}{session_id}_{index}.jsonl"
        return self.session_dir / f"{_CHAT_HISTORY_PREFIX}{session_id}.jsonl"

    def _chat_history_file_glob(self, session_id: str) -> str:
        return f"{_CHAT_HISTORY_PREFIX}{session_id}*.jsonl"

    def _rotate_files(self, session_id: str) -> None:
        """删除该 session 的所有旧轮转文件，在 save_history 重写前调用。"""
        with self._lock:
            for old_file in sorted(self.session_dir.glob(self._chat_history_file_glob(session_id))):
                try:
                    old_file.unlink()
                except Exception as exc:
                    log.warning("rotate_files: failed to delete {} error={}", old_file, exc)

    def save_history(self, session_id: str, history: list[dict[str, Any]],  user_id: str = "") -> None:
        """完整写入历史记录，超出 1MB 自动轮转到 _N.jsonl。"""
        self._rotate_files(session_id)
        now = datetime.utcnow().isoformat()
        file_index = -1
        with self._lock:
            current_file = self._chat_history_file(session_id, file_index)
            writer = open(current_file, "w", encoding="utf-8")
            try:
                for idx, msg in enumerate(history):
                    record = dict(msg)

                    record["user_id"] = user_id
                    record["create_time"] = now
                    line = json.dumps(record, ensure_ascii=False) + "\n"
                    if writer.tell() > 0 and writer.tell() + len(line.encode("utf-8")) > MAX_FILE_BYTES:
                        writer.close()
                        file_index += 1
                        writer = open(self._chat_history_file(session_id, file_index), "w", encoding="utf-8")
                    writer.write(line)
            finally:
                writer.close()

    def append_chat_history_message(self, session_id: str, message: dict[str, Any]) -> Path:
        """运行时追加单条消息到基础文件（会被 save_history 在请求结束时覆盖）。"""
        history_file = self._chat_history_file(session_id)
        serialized = json.dumps(message, ensure_ascii=False) + "\n"
        with self._lock:
            with open(history_file, "a", encoding="utf-8") as file:
                file.write(serialized)
        return history_file

    def append_tool_log(self, session_id: str, entry: dict[str, Any]) -> None:
        """追加一条工具调用记录到 tool_log_{session_id}.jsonl。"""
        serialized = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with open(self._tool_log_file(session_id), "a", encoding="utf-8") as f:
                f.write(serialized)

    def append_memory_log(self, session_id: str, entry: dict[str, Any]) -> None:
        """追加一条记忆检索/写入记录到 memory_log_{session_id}.jsonl。"""
        serialized = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with open(self._memory_log_file(session_id), "a", encoding="utf-8") as f:
                f.write(serialized)

    def _tool_log_file(self, session_id: str) -> Path:
        return self.session_dir / f"tool_log_{session_id}.jsonl"

    def _memory_log_file(self, session_id: str) -> Path:
        return self.session_dir / f"memory_log_{session_id}.jsonl"

    def save_turn_snapshot(self, turn_id: str, snapshot: dict[str, Any]) -> Path:
        snapshot_file = self._turn_snapshot_file(turn_id)
        with self._lock:
            with open(snapshot_file, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=2)
        return snapshot_file
