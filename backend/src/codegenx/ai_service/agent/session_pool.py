"""Session resource pool with automatic cleanup and LRU management."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Optional

from codegenx.ai_service.agent.agent_schema import AgentState
from codegenx.ai_service.hook import  HookContext, HookEvent, on
from shared import log


class SessionPool:
    """
    Manages session lifecycle with intelligent cleanup strategies.
    
    Features:
    - LRU-based session eviction
    - Configurable idle timeout
    - Graceful session closure
    - Memory-efficient session management
    """

    def __init__(
        self,
        max_sessions: int = 1000,
        idle_timeout_seconds: int = 3600,
        cleanup_interval_seconds: int = 300,
        swap_idle_seconds: int = 300,
    ):
        """
        Initialize SessionPool.

        Args:
            max_sessions: Maximum number of active sessions
            idle_timeout_seconds: Seconds before idle session cleanup (default 1 hour)
            cleanup_interval_seconds: Interval between cleanup runs (default 5 minutes)
            swap_idle_seconds: Seconds before an idle session's chat_messages are
                unloaded (swap-out, P3). Must be far smaller than idle_timeout_seconds;
                0 disables swapping.
        """
        self.max_sessions = max_sessions
        self.idle_timeout_seconds = idle_timeout_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self.swap_idle_seconds = swap_idle_seconds
        
        # OrderedDict maintains insertion order for LRU tracking
        self._sessions: OrderedDict[str, Any] = OrderedDict()
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._shutdown_event.clear()
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="session-pool-cleanup",
        )
        log.info(
            "SessionPool started: max_sessions={}, idle_timeout={}s",
            self.max_sessions,
            self.idle_timeout_seconds,
        )

    async def stop(self) -> None:
        """Stop cleanup task and close all sessions."""
        self._shutdown_event.set()
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        async with self._lock:
            session_ids = list(self._sessions.keys())
            for session_id in session_ids:
                await self._close_session_unsafe(session_id)
            self._sessions.clear()
        log.info("SessionPool stopped")

    async def get_or_create(
        self, session_id: str, request: Any
    ) -> tuple[Any, bool]:
        """
        Get existing session or create new one.

        Args:
            session_id: Unique session identifier
            request: AiServiceGenerateRequest

        Returns:
            Tuple of (session_state, is_new_session)
        """
        from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState

        async with self._lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                self._sessions.move_to_end(session_id)
                session.request = request
                session.touch()
                return session, False

            # Check if we need to evict LRU session
            if len(self._sessions) >= self.max_sessions:
                lru_session_id = next(iter(self._sessions))
                await self._close_session_unsafe(lru_session_id)
                del self._sessions[lru_session_id]
                log.warning(
                    "SessionPool reached max capacity, evicted LRU session: {}",
                    lru_session_id,
                )

            session = RuntimeSessionState(
                session_id=session_id,
                request=request,
            )
            session.touch()
            self._sessions[session_id] = session
            return session, True

    async def exists(self, session_id: str) -> bool:
        """Check if session exists and is not closed."""
        async with self._lock:
            if session_id not in self._sessions:
                return False
            session = self._sessions[session_id]
            return not getattr(session, "closed", False)

    async def get(self, session_id: str) -> Optional[Any]:
        """Get session if it exists and is not closed."""
        async with self._lock:
            if session_id not in self._sessions:
                return None
            session = self._sessions[session_id]
            if getattr(session, "closed", False):
                del self._sessions[session_id]
                return None
            return session

    async def remove(self, session_id: str) -> None:
        """Remove and close session."""
        async with self._lock:
            await self._close_session_unsafe(session_id)
            self._sessions.pop(session_id, None)

    async def active_count(self) -> int:
        """Get number of active non-closed sessions."""
        async with self._lock:
            return sum(
                1
                for session in self._sessions.values()
                if not getattr(session, "closed", False)
            )

    async def _cleanup_loop(self) -> None:
        """Periodically cleanup idle and closed sessions."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.cleanup_interval_seconds)
                await self._cleanup_inactive_sessions()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("SessionPool cleanup error: {}", exc)

    async def _cleanup_inactive_sessions(self) -> None:
        """Remove sessions that are idle or already closed; swap-out the middle band."""
        now = time.time()
        sessions_to_remove = []
        swapped_sessions = []

        async with self._lock:
            for session_id, session in list(self._sessions.items()):
                # Remove already-closed sessions
                if getattr(session, "closed", False):
                    sessions_to_remove.append(session_id)
                    continue

                # Remove idle sessions
                last_activity = getattr(session, "last_activity_at", 0.0)
                idle_duration = now - last_activity
                if idle_duration > self.idle_timeout_seconds:
                    sessions_to_remove.append(session_id)
                    continue

                # P3 swap-out（docs/SystemApp架构设计.md §7）：闲置超过 swap 阈值且
                # 无在途任务 → 卸载 chat_messages（快照已在 turn_end 落盘），会话对象
                # 留在池中保持连续性；下次请求由 runtime 按需从快照恢复
                if (
                    self.swap_idle_seconds > 0
                    and idle_duration > self.swap_idle_seconds
                    and not getattr(session, "swapped_out", False)
                    and self._is_quiescent(session)
                ):
                    cm = getattr(session, "context_manager", None)
                    if cm is not None and cm.chat_messages:
                        cm.chat_messages = []
                    session.swapped_out = True
                    swapped_sessions.append(session_id)

            for session_id in sessions_to_remove:
                await self._close_session_unsafe(session_id)
                del self._sessions[session_id]

        if sessions_to_remove:
            log.info(
                "SessionPool cleanup: removed {} idle/closed sessions",
                len(sessions_to_remove),
            )
        if swapped_sessions:
            log.info(
                "SessionPool cleanup: swapped out {} idle sessions (chat_messages unloaded)",
                len(swapped_sessions),
            )

    @staticmethod
    def _is_quiescent(session: Any) -> bool:
        """会话当前无在途工作：无活跃请求任务、worker 空闲、不在处理中。"""
        if getattr(session, "processing", False):
            return False
        active_tasks = getattr(session, "active_tasks", {})
        if any(not task.done() for task in active_tasks.values()):
            return False
        worker_task = getattr(session, "worker_task", None)
        return worker_task is None or worker_task.done()

    async def _close_session_unsafe(self, session_id: str) -> None:
        """Close session without lock (caller must hold lock)."""
        session = self._sessions.get(session_id)
        if session is None:
            return

        try:
            # Gracefully close worker task
            worker_task = getattr(session, "worker_task", None)
            if worker_task is not None and not worker_task.done():
                worker_task.cancel()
                try:
                    await asyncio.wait_for(worker_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            # Cancel all active tasks
            active_tasks = getattr(session, "active_tasks", {})
            for task in active_tasks.values():
                if not task.done():
                    task.cancel()

            session.closed = True
        except Exception as exc:
            log.warning("Error closing session {}: {}", session_id, exc)

    def stats(self) -> dict[str, Any]:
        """Get pool statistics for monitoring."""
        # Note: This is a snapshot and doesn't hold lock
        active = sum(
            1
            for s in self._sessions.values()
            if not getattr(s, "closed", False)
        )
        return {
            "total_sessions": len(self._sessions),
            "active_sessions": active,
            "max_sessions": self.max_sessions,
            "idle_timeout_seconds": self.idle_timeout_seconds,
        }


# ── Hook 监听器：会话/turn 生命周期编排（docs/Hook设计.md §4.2） ─────────────
# 会话级对象只剩 SessionContext（纯状态）；落盘/任务看板走 SystemApp 无状态服务
# 注意：@on 装饰时注册裸函数对象，监听器必须是无 self 的模块级函数


@on(HookEvent.SESSION_START, name="init_session_objects", priority=10)
async def init_session_objects(ctx: HookContext) -> None:
    """初始化会话级对象（迁自 handlers.on_session_start）：

    SessionContext（纯会话状态）、加载聊天历史快照、
    用户消息入库、更新会话索引、state→RUNNING。
    """
    session = ctx.session
    req = session.request
    if req is None:
        log.warning("on_session_start: request is None, skipping")
        return
    from codegenx.ai_service.system_app import get_app
    from codegenx.ai_service.context.session_context import SessionContext

    session_io = get_app().session_io

    # P4 §10.3：会话归属智能体（请求 metadata.agent_name；空=默认智能体）
    session.agent_name = str((getattr(req, "metadata", None) or {}).get("agent_name", "") or "")

    session.context_manager = SessionContext(
        session_id=session.session_id,
        app_id=session.app_id,
        user_id=session.user_id,
        db_name=session.db_name,
        agent_name=session.agent_name,
    )
    # 加载上次聊天时的历史记录到内存
    session.context_manager.chat_messages = await session_io.get_turn_chat_message_snapshot(
        user_id=session.user_id, app_id=session.app_id, session_id=session.session_id
    ) or []
    user_dict = {"role": "user", "content": req.message}
    # 聊天消息入库（MySQL chat_message 表；失败不阻断对话）
    try:
        from codegenx.ai_service.chat_message import get_chat_message_store
        await get_chat_message_store().append_message(
            session.user_id, str(req.app_id), session.session_id, user_dict
        )
    except Exception as exc:
        log.warning("user 消息入库失败（不影响对话）: {}", exc)

    # 更新会话索引，供快速列出历史会话
    await session_io.upsert_session_index(
        req.message,
        user_id=session.user_id, app_id=session.app_id, session_id=session.session_id,
    )

    session.state = AgentState.RUNNING
    session.started_at = datetime.utcnow()


@on(HookEvent.TURN_END, name="persist_chat_snapshot", priority=10)
async def persist_chat_snapshot(ctx: HookContext) -> None:
    """保留上下文快照（迁自 handlers.on_turn_end 前半）。"""
    session = ctx.session
    if session.context_manager is not None:
        from codegenx.ai_service.system_app import get_app

        await get_app().session_io.save_turn_chat_message_snapshot(
            session.context_manager.chat_messages,
            user_id=session.user_id,
            app_id=session.app_id,
            session_id=session.session_id,
        )
