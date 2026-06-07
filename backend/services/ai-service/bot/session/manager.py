import asyncio
import json
import re
from datetime import datetime, timezone,timedelta
from pathlib import Path
from typing import Any

import aiofiles

from shared.config.log_config import log
from shared.constants import get_current_session_dir


PROJECT_DIR = Path(__file__).parent.parent

MAX_FILE_BYTES = 1 * 1024 * 1024  # 单文件最大 1MB
# 聊天中的
_CHAT_HISTORY_PREFIX = "chat_history_"
# turn结束更新记录最新一轮的chat_message，方便后续再打开会话历史加载
_TURN_SNAPSHOT_PREFIX = "last_chat_snapshot_"

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
