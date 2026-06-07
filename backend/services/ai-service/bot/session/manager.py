import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import aiofiles

from shared.config.log_config import log
from shared.constants import get_current_session_dir, get_session_dir


PROJECT_DIR = Path(__file__).parent.parent

MAX_FILE_BYTES = 1 * 1024 * 1024
_CHAT_HISTORY_PREFIX = "chat_history_"
_TURN_SNAPSHOT_PREFIX = "last_chat_snapshot_"
_SESSION_INDEX_FILE = "session_index.json"

class SessionManager:

    def __init__(self, app_id: str,session_id:str):
        self.app_id = app_id
        self.session_id = session_id
        self.session_dir = get_current_session_dir(self.app_id,session_id)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _turn_snapshot_file(self, turn_id: str) -> Path:
        return self.session_dir / f"turn_{turn_id}_snapshot.json"

    def _chat_history_file(self, index: int = -1) -> Path:
        """index=-1 → chat_history_{sid}.jsonl, index>=0 → chat_history_{sid}_{index}.jsonl"""
        if index >= 0:
            return self.session_dir / f"{_CHAT_HISTORY_PREFIX}{self.session_id}_{index}.jsonl"
        return self.session_dir / f"{_CHAT_HISTORY_PREFIX}{self.session_id}.jsonl"

    def _turn_chat_message_snapshot_file(self) -> Path:
        return self.session_dir / f"{_TURN_SNAPSHOT_PREFIX}{self.session_id}.jsonl"

    def _tool_log_file(self, ) -> Path:
        return self.session_dir / f"tool_log_{self.session_id}.jsonl"

    def _memory_log_file(self) -> Path:
        return self.session_dir / f"memory_log_{self.session_id}.jsonl"

    async def save_turn_chat_message_snapshot(self,turn_chat_message: list[dict[str, Any]]) -> str:
        """保留最后一轮的chat_message，给会话重新打开时，直接从此chat_message直接继续输入给大模型"""
        snapshot_file = self._turn_chat_message_snapshot_file()
        async with self._lock:
            async with aiofiles.open(snapshot_file, "w", encoding="utf-8") as file:
                await file.write(json.dumps(turn_chat_message, ensure_ascii=False, indent=2))

        return str(snapshot_file)

    async def get_turn_chat_message_snapshot(self) -> list:
        snapshot_file = Path(self._turn_chat_message_snapshot_file())
        chat_message = []
        async with self._lock:
            if not snapshot_file.exists():
                return chat_message  # 文件不存在直接返回空

            try:
                async with aiofiles.open(snapshot_file, "r", encoding="utf-8") as file:
                    content = await file.read()
                    if content.strip():  # 防止空文件
                        chat_message = json.loads(content)
            except (json.JSONDecodeError, Exception):
                # 文件损坏 → 返回空列表
                chat_message = []

        return chat_message

    async def _get_latest_write_file(self,  line_bytes: int) -> Path:
        """
        获取当前要写入的最新文件：
        - 检查最后一个文件是否够大, 不够就新建下一个数字文件
        """
        index = -1

        # 循环找能放下本条消息的文件
        while True:
            current_file = self._chat_history_file(index)
            current_size = current_file.stat().st_size if current_file.exists() else 0
            # 能放下 → 用这个
            if current_size + line_bytes <= MAX_FILE_BYTES:
                return current_file
            # 放不下 → 下一个编号
            index += 1

    async def append_chat_history_message(self, message: dict[str, Any], user_id: str) -> None:
        """追加单条聊天记录，自动滚动到新文件"""

        # 1. 简化 assistant tool_calls（只保留 arguments key）
        if message.get("role") == "assistant" and "tool_calls" in message:
            tool_calls = message["tool_calls"]
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if call.get("type") == "function" and "function" in call:
                        func = call["function"]
                        arguments_str = func.get("arguments", "")
                        if arguments_str:
                            try:
                                args_dict = json.loads(arguments_str)
                                simplified_args = {k: None for k in args_dict}
                                func["arguments"] = json.dumps(simplified_args, ensure_ascii=False)
                            except json.JSONDecodeError:
                                pass

        # 2. 加入公共字段
        message["user_id"] = user_id
        message["create_time"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        serialized = json.dumps(message, ensure_ascii=False) + "\n"
        line_bytes = len(serialized.encode("utf-8"))

        async with self._lock:
            # 获取当前应该写入的最新文件
            file_path = await self._get_latest_write_file( line_bytes)

            # 写入文件
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(serialized)



    async def append_tool_log(self,  entry: dict[str, Any]) -> None:
        """追加一条工具调用记录到 tool_log_{session_id}.jsonl。"""
        serialized = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        async with self._lock:
            with open(self._tool_log_file(), "a", encoding="utf-8") as f:
                f.write(serialized)

    async def append_memory_log(self,  entry: dict[str, Any]) -> None:
        """追加一条记忆检索/写入记录到 memory_log_{session_id}.jsonl。"""
        serialized = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        async with self._lock:
            with open(self._memory_log_file(), "a", encoding="utf-8") as f:
                f.write(serialized)



    async def save_turn_snapshot(self, turn_id: str, snapshot: dict[str, Any]) -> Path:
        snapshot_file = self._turn_snapshot_file(turn_id)
        async with self._lock:
            with open(snapshot_file, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=2)
        return snapshot_file

    async def upsert_session_index(self, first_message: str) -> None:
        """将当前 session 写入 session_index.json，用于快速列出会话历史。"""
        index_file = get_session_dir(self.app_id) / _SESSION_INDEX_FILE
        async with self._lock:
            entries: list[dict] = []
            if index_file.exists():
                try:
                    content = index_file.read_text(encoding="utf-8")
                    entries = json.loads(content) if content.strip() else []
                except Exception:
                    entries = []

            now = datetime.now(timezone(timedelta(hours=8))).isoformat()
            # 更新或追加
            found = False
            for e in entries:
                if e.get("session_id") == self.session_id:
                    e["first_message"] = first_message[:50]
                    e["create_time"] = now
                    found = True
                    break
            if not found:
                entries.append({
                    "session_id": self.session_id,
                    "first_message": first_message[:50],
                    "create_time": now,
                })

            # 只保留最近 100 条
            entries = entries[-100:]

            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)

    @staticmethod
    def read_session_index(app_id: str) -> list[dict]:
        """读取 session 索引列表，按时间倒序。"""
        index_file = get_session_dir(app_id) / _SESSION_INDEX_FILE
        if not index_file.exists():
            return []
        try:
            entries = json.loads(index_file.read_text(encoding="utf-8"))
            if isinstance(entries, list):
                entries.sort(key=lambda e: e.get("create_time", ""), reverse=True)
                return entries
        except Exception:
            pass
        return []
