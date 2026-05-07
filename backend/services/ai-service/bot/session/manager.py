import json
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
import re

from pydantic import BaseModel, ConfigDict, Field

from bot.utils.log_utils import log
from shared.constants import get_bot_session_dir


def _sanitize_app_id(app_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(app_id or "main").strip())
    return normalized or "main"


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    role_id: str | None = None  # user_id agent_id
    content: str = ""
    images: list[str] | None = None
    create_time: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_history_message(cls, message: dict[str, Any]) -> "Message":
        payload = dict(message or {})
        payload.setdefault("role", "assistant")
        payload.setdefault("content", "")
        payload.setdefault("role_id", payload.get("role_id") or payload.get("name") or payload.get("tool_call_id"))
        return cls(**payload)

    def to_history_message(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        role = payload.get("role")
        allowed_keys = {"role", "content"}
        if role == "assistant":
            allowed_keys.add("tool_calls")
        elif role == "tool":
            allowed_keys.update({"tool_call_id", "name"})
        return {key: value for key, value in payload.items() if key in allowed_keys}


class Session(BaseModel):
    session_id: str
    messages: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        """序列化为字典，用于保存文件"""
        return self.model_dump()

    def add_message(self, role: str,role_id:str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg_dict = {
            "role": role,
            "role_id":role_id,
            "content": content,
            **kwargs
        }
        self.messages.append(Message(**msg_dict))
        self.updated_at = datetime.now(UTC).isoformat()

    def add_user_message(self,user_id,content,**kwargs):
        self.add_message("user",user_id,content,**kwargs)

    def add_agent_message(self,agent_id,content,**kwargs):
        self.add_message("agent",agent_id,content,**kwargs)

    def replace_history(self, history: list[dict[str, Any]]) -> None:
        self.messages = [Message.from_history_message(item) for item in history]
        self.updated_at = datetime.now(UTC).isoformat()

    def to_history(self) -> list[dict[str, Any]]:
        return [message.to_history_message() for message in self.messages]

PROJECT_DIR = Path(__file__).parent.parent



class SessionManager:

    def __init__(self, app_id: str = "main", /, **data: Any):
        super().__init__(**data)
        self.app_id = _sanitize_app_id(app_id)
        self.session_dir = get_bot_session_dir(self.app_id)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _session_file(self, session_id: str) -> Path:
        return self.session_dir / f"session_{session_id}.jsonl"

    def _turn_snapshot_file(self, turn_id: str) -> Path:
        return self.session_dir / f"turn_{turn_id}__snapshot.json"

    def _chat_history_file(self, session_id: str) -> Path:
        return self.session_dir / f"chat_history_{session_id}.jsonal"

    def _get_lock(self) -> threading.Lock:
        """✅ 获取真实锁对象（修复 PrivateAttr 包装问题）"""
        return self.__dict__["_lock"]

    def get_or_create_session(self, session_id=None) ->Session:
        if session_id in self._cache:
            log.debug(f"从缓存加载session:{session_id}")
            return self._cache[session_id]
        session_file = self._session_file(session_id) if session_id else None
        if not session_id:
            raise ValueError("session_id is required")
        elif session_id and not session_file.exists():
            session = Session(session_id=session_id)
        else:
            log.debug(f"从磁盘加载session:{session_id}")
            with self._lock:
                try:
                    with open(session_file, encoding="utf-8") as f:
                        data = json.load(f)
                    session = Session(**data)
                except Exception as e:
                    log.error(f"从磁盘加载session{session_id}失败，新建session")
                    session = Session(session_id=session_id)
        self._cache[session_id] = session
        return session

    def flush_to_disk(self, session_id: str) -> None:
        """保存会话到磁盘（线程安全）  在后面添加"""
        with self._lock:
            session = self._cache.get(session_id)
            if not session:
                log.warning(f"无法保存，缓存中不存在 session: {session_id}")
                return

            session_file = self._session_file(session_id)
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
                log.debug(f"session 已保存到磁盘: {session_id}")
            except IOError as e:
                log.error(f"保存 session 失败: {session_id}, 错误: {str(e)}")

    def load_history(self, session_id: str) -> list[dict[str, Any]]:
        session = self.get_or_create_session(session_id)
        return session.to_history()

    def save_history(self, session_id: str, history: list[dict[str, Any]]) -> Session:
        session = self.get_or_create_session(session_id)
        session.replace_history(history)
        self._cache[session_id] = session
        self.flush_to_disk(session_id)
        return session

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

if __name__ == '__main__':
    # 初始化管理器
    manager = SessionManager()

    # 获取/创建会话
    session1 = manager.get_or_create_session()  # 自动生成ID
    # session2 = manager.get_or_create_session("my-session-123")  # 指定ID
    # print(session1, session2)
    # print(manager._cache)
    # # 保存会话
    # manager.flush_to_disk(session2.session_id)


    # def test_thread():
    #     for _ in range(5):
    #         s = manager.get_or_create_session()
    #
    # # 10个线程同时操作，不会出现冲突
    # threads = [threading.Thread(target=test_thread) for _ in range(10)]
    # for t in threads:
    #     t.start()
    # for t in threads:
    #     t.join()
