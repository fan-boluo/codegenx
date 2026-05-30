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



class SessionManager:

    def __init__(self, app_id: str = "main"):
        self.app_id = _sanitize_app_id(app_id)
        self.session_dir = get_session_dir(self.app_id)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _turn_snapshot_file(self, turn_id: str) -> Path:
        return self.session_dir / f"turn_{turn_id}__snapshot.json"

    def _chat_history_file(self, session_id: str) -> Path:
        return self.session_dir / f"chat_history_{session_id}.jsonl"

    def _tool_log_file(self, session_id: str) -> Path:
        return self.session_dir / f"tool_log_{session_id}.jsonl"

    def _memory_log_file(self, session_id: str) -> Path:
        return self.session_dir / f"memory_log_{session_id}.jsonl"

    def save_history(self, session_id: str, history: list[dict[str, Any]], app_id: str = "", user_id: str = "", request_id: str = "") -> None:
        history_file = self._chat_history_file(session_id)
        now = datetime.utcnow().isoformat()
        with self._lock:
            with open(history_file, "w", encoding="utf-8") as file:
                for idx, msg in enumerate(history):
                    record = dict(msg)
                    record["id"] = idx + 1
                    record["turn_id"] = request_id
                    record["app_id"] = app_id
                    record["user_id"] = user_id
                    record["create_time"] = now
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_turn_snapshot(self, turn_id: str, snapshot: dict[str, Any]) -> Path:
        snapshot_file = self._turn_snapshot_file(turn_id)
        with self._lock:
            with open(snapshot_file, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=2)
        return snapshot_file

    def append_chat_history_message(self, session_id: str, message: dict[str, Any]) -> Path:
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




if __name__ == '__main__':
    manager = SessionManager()
    manager.save_history("test-session", [{"role": "user", "content": "hello"}])
