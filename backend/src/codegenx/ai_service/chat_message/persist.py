"""SessionPersistence —— 聊天消息落盘服务（P2 服务化，docs/SystemApp架构设计.md §4.3）。

原 SessionManager（每会话一个实例、只持 3 个 id 字符串 + Lock）改为全局无状态服务：
方法保留，实例消失，ids 进签名（user_id=/app_id=/session_id= 关键字传参，防维度错配）。
会话目录仍是 .data/{userId}/{appId}/session/{sessionId}，按用户隔离。
"""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

from shared import log
from shared.constants import get_current_session_dir
from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.hook import HookContext, HookEvent, on


PROJECT_DIR = Path(__file__).parent.parent

_TURN_SNAPSHOT_PREFIX = "last_chat_snapshot_"


class SessionPersistence(BaseComponent):
    """纯落盘服务：聊天快照 / 工具日志 / 记忆日志 / turn 快照。

    锁表说明：同会话的读写需串行（原实例级 asyncio.Lock 语义），
    按 (user_id, app_id, session_id) 建锁；条目极小且与会话数同阶，
    属基础设施注册表（类比 SessionPool），不是会话业务状态。
    """

    name = ComponentType.SESSION_PERSISTENCE

    def __init__(self, system_app=None) -> None:
        BaseComponent.__init__(self, system_app)
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def init_app(self, system_app) -> None:
        self.system_app = system_app


    def _lock(self, user_id: str, app_id: str, session_id: str) -> asyncio.Lock:
        key = (user_id, app_id, session_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _session_dir(self, user_id: str, app_id: str, session_id: str) -> Path:
        session_dir = get_current_session_dir(user_id, app_id, session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _turn_snapshot_file(self, user_id: str, app_id: str, session_id: str, turn_id: str) -> Path:
        return self._session_dir(user_id, app_id, session_id) / f"turn_{turn_id}_snapshot.json"

    def _turn_chat_message_snapshot_file(self, user_id: str, app_id: str, session_id: str) -> Path:
        return self._session_dir(user_id, app_id, session_id) / f"{_TURN_SNAPSHOT_PREFIX}{session_id}.jsonl"

    def _tool_log_file(self, user_id: str, app_id: str, session_id: str) -> Path:
        return self._session_dir(user_id, app_id, session_id) / f"tool_log_{session_id}.jsonl"

    def _memory_log_file(self, user_id: str, app_id: str, session_id: str) -> Path:
        return self._session_dir(user_id, app_id, session_id) / f"memory_log_{session_id}.jsonl"

    async def save_turn_chat_message_snapshot(
        self, turn_chat_message: list[dict[str, Any]], *, user_id: str, app_id: str, session_id: str
    ) -> str:
        """保留最后一轮的chat_message，给会话重新打开时，直接从此chat_message直接继续输入给大模型"""
        snapshot_file = self._turn_chat_message_snapshot_file(user_id, app_id, session_id)
        async with self._lock(user_id, app_id, session_id):
            async with aiofiles.open(snapshot_file, "w", encoding="utf-8") as file:
                await file.write(json.dumps(turn_chat_message, ensure_ascii=False, indent=2))

        return str(snapshot_file)

    async def get_turn_chat_message_snapshot(
        self, *, user_id: str, app_id: str, session_id: str
    ) -> list:
        snapshot_file = Path(self._turn_chat_message_snapshot_file(user_id, app_id, session_id))
        chat_message = []
        async with self._lock(user_id, app_id, session_id):
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
        log.debug("从上一轮快照加载聊天历史上下文：{} 条",len(chat_message))
        return chat_message

    async def append_tool_log(
        self, entry: dict[str, Any], *, user_id: str, app_id: str, session_id: str
    ) -> None:
        """追加一条工具调用记录到 tool_log_{session_id}.jsonl。"""
        serialized = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        async with self._lock(user_id, app_id, session_id):
            with open(self._tool_log_file(user_id, app_id, session_id), "a", encoding="utf-8") as f:
                f.write(serialized)

    async def append_memory_log(
        self, entry: dict[str, Any], *, user_id: str, app_id: str, session_id: str
    ) -> None:
        """追加一条记忆检索/写入记录到 memory_log_{session_id}.jsonl。"""
        serialized = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        async with self._lock(user_id, app_id, session_id):
            with open(self._memory_log_file(user_id, app_id, session_id), "a", encoding="utf-8") as f:
                f.write(serialized)

    async def save_turn_snapshot(
        self, turn_id: str, snapshot: dict[str, Any], *, user_id: str, app_id: str, session_id: str
    ) -> Path:
        snapshot_file = self._turn_snapshot_file(user_id, app_id, session_id, turn_id)
        async with self._lock(user_id, app_id, session_id):
            with open(snapshot_file, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=2)
        return snapshot_file


# ── Hook 监听器：工具日志接入事件总线（docs/Hook设计.md §4.2）────────────────


@on(HookEvent.AFTER_TOOL_CALL, name="persist_tool_log", priority=10)
async def persist_tool_log(ctx: "HookContext") -> None:
    """工具执行快照落盘（迁自 handlers.post_tool_use 前半）。"""
    session = ctx.session
    tool_call = ctx.data.get("tool_call") or {}
    result = ctx.data.get("result")
    tool_name = tool_call.get("name")
    try:
        from codegenx.ai_service.system_app import get_app

        # 过滤掉运行时注入的不可序列化对象
        tool_input = dict(tool_call.get("arguments", {}) or {})
        loggable_input = {
            k: v for k, v in tool_input.items()
            if k not in {"context", "context_compactor"}
        }
        snapshot: dict[str, Any] = {
            "request_id": session.request.request_id,
            "ts": datetime.now(UTC).isoformat(),
            "turn_id": ctx.turn.active_step_id if ctx.turn is not None else "",
            "tool": tool_name,
            "input": loggable_input,
            "result": result,
        }
        await get_app().session_io.append_tool_log(
            snapshot,
            user_id=session.user_id,
            app_id=session.app_id,
            session_id=session.session_id,
        )
    except Exception as exc:
        log.debug(f"Session log write failed for tool '{tool_name}': {exc}")


def initialize_session_io(system_app) -> SessionPersistence:
    """注册会话落盘服务组件（system_app.initialize_components 调用）。"""
    return system_app.register(SessionPersistence)
