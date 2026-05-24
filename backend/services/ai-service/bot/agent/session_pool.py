"""Session resource pool with automatic cleanup and LRU management."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Optional

from shared.config.log_config import log


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
    ):
        """
        Initialize SessionPool.
        
        Args:
            max_sessions: Maximum number of active sessions
            idle_timeout_seconds: Seconds before idle session cleanup (default 1 hour)
            cleanup_interval_seconds: Interval between cleanup runs (default 5 minutes)
        """
        self.max_sessions = max_sessions
        self.idle_timeout_seconds = idle_timeout_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        
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
        self, session_id: str, request: Any, runtime: Any
    ) -> tuple[Any, bool]:
        """
        Get existing session or create new one.

        Args:
            session_id: Unique session identifier
            request: AiServiceGenerateRequest
            runtime: AgentRuntime instance

        Returns:
            Tuple of (session_state, is_new_session)
        """
        from agent.runtime_schema import RuntimeSessionState

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
                runtime=runtime,
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
        """Remove sessions that are idle or already closed."""
        now = time.time()
        sessions_to_remove = []

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

            for session_id in sessions_to_remove:
                await self._close_session_unsafe(session_id)
                del self._sessions[session_id]

        if sessions_to_remove:
            log.info(
                "SessionPool cleanup: removed {} idle/closed sessions",
                len(sessions_to_remove),
            )

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
