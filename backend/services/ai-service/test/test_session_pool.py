"""
Unit tests for SessionPool - resource pool with LRU and auto-cleanup.
Run from project root:
  python -m pytest backend/services/ai-service/test/test_session_pool.py -v
  或
  python -m unittest backend.services.ai-service.test.test_session_pool -v
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

# Setup paths
test_file = Path(__file__).resolve()
ai_service_root = test_file.parents[1]
backend_root = ai_service_root.parents[1]
for path in (ai_service_root, backend_root):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from pathlib import Path

# Setup paths
test_file = Path(__file__).resolve()
ai_service_root = test_file.parents[1]
backend_root = ai_service_root.parents[1]
for path in (ai_service_root, backend_root):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from bot.agent.session_pool import SessionPool


class MockSession:
    """Mock session object for testing."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.closed = False
        self.last_activity_at = time.time()
        self.worker_task = None
        self.active_tasks = {}


class TestSessionPool(unittest.TestCase):
    """Test SessionPool basic operations."""

    def setUp(self):
        """Create pool before each test."""
        self.pool = SessionPool(
            max_sessions=5,
            idle_timeout_seconds=2,
            cleanup_interval_seconds=1,
        )

    async def async_test_pool_create(self):
        """Test creating sessions in pool."""
        await self.pool.start()
        try:
            # Create first session
            session1, is_new1 = await self.pool.get_or_create(
                "session_1", lambda: MockSession("session_1")
            )
            self.assertTrue(is_new1)
            self.assertEqual(session1.session_id, "session_1")

            # Get existing session
            session1_again, is_new1_again = await self.pool.get_or_create(
                "session_1", lambda: MockSession("session_1")
            )
            self.assertFalse(is_new1_again)
            self.assertIs(session1, session1_again)

            # Create second session
            session2, is_new2 = await self.pool.get_or_create(
                "session_2", lambda: MockSession("session_2")
            )
            self.assertTrue(is_new2)
            self.assertEqual(session2.session_id, "session_2")

            # Verify count
            count = await self.pool.active_count()
            self.assertEqual(count, 2)
        finally:
            await self.pool.stop()

    async def async_test_lru_eviction(self):
        """Test LRU eviction when max capacity reached."""
        await self.pool.start()
        try:
            sessions = {}
            # Fill pool to capacity (5 sessions)
            for i in range(5):
                sid = f"session_{i}"
                session, _ = await self.pool.get_or_create(sid, lambda id=sid: MockSession(id))
                sessions[sid] = session
                await asyncio.sleep(0.01)  # Small delay to ensure ordering

            # Verify all exist
            count = await self.pool.active_count()
            self.assertEqual(count, 5)

            # Access session_2 to make it most recently used
            session_2, is_new = await self.pool.get_or_create(
                "session_2", lambda: MockSession("session_2")
            )
            self.assertFalse(is_new)

            # Add new session - should evict session_0 (LRU)
            await asyncio.sleep(0.01)
            session_5, is_new = await self.pool.get_or_create(
                "session_5", lambda: MockSession("session_5")
            )
            self.assertTrue(is_new)

            # session_0 should be evicted
            exists = await self.pool.exists("session_0")
            self.assertFalse(exists)

            # session_2 should still exist (was accessed recently)
            exists = await self.pool.exists("session_2")
            self.assertTrue(exists)

            # Pool count should still be 5
            count = await self.pool.active_count()
            self.assertEqual(count, 5)
        finally:
            await self.pool.stop()

    async def async_test_idle_cleanup(self):
        """Test cleanup of idle sessions."""
        await self.pool.start()
        try:
            # Create two sessions
            session_1, _ = await self.pool.get_or_create(
                "session_1", lambda: MockSession("session_1")
            )
            session_2, _ = await self.pool.get_or_create(
                "session_2", lambda: MockSession("session_2")
            )

            count = await self.pool.active_count()
            self.assertEqual(count, 2)

            # Wait for idle timeout + cleanup interval
            await asyncio.sleep(3.5)

            # After cleanup, idle sessions should be removed
            count = await self.pool.active_count()
            self.assertEqual(count, 0)
        finally:
            await self.pool.stop()

    async def async_test_closed_session_cleanup(self):
        """Test cleanup of closed sessions."""
        await self.pool.start()
        try:
            # Create session
            session, _ = await self.pool.get_or_create(
                "session_1", lambda: MockSession("session_1")
            )
            session.touch = lambda: None  # Mock touch method

            count = await self.pool.active_count()
            self.assertEqual(count, 1)

            # Mark as closed
            session.closed = True

            # After cleanup, closed sessions should be removed
            await asyncio.sleep(1.5)

            count = await self.pool.active_count()
            self.assertEqual(count, 0)
        finally:
            await self.pool.stop()

    async def async_test_remove_session(self):
        """Test manual session removal."""
        await self.pool.start()
        try:
            # Create session
            session, _ = await self.pool.get_or_create(
                "session_1", lambda: MockSession("session_1")
            )

            count = await self.pool.active_count()
            self.assertEqual(count, 1)

            # Remove session
            await self.pool.remove("session_1")

            count = await self.pool.active_count()
            self.assertEqual(count, 0)

            # Should not exist
            exists = await self.pool.exists("session_1")
            self.assertFalse(exists)
        finally:
            await self.pool.stop()

    async def async_test_get_nonexistent_session(self):
        """Test getting non-existent session returns None."""
        await self.pool.start()
        try:
            session = await self.pool.get("nonexistent")
            self.assertIsNone(session)
        finally:
            await self.pool.stop()

    async def async_test_stats(self):
        """Test stats reporting."""
        pool = self.pool
        pool.session_pool_size = 5
        
        # Create sessions
        await pool.start()
        try:
            session_1, _ = await pool.get_or_create(
                "session_1", lambda: MockSession("session_1")
            )
            session_2, _ = await pool.get_or_create(
                "session_2", lambda: MockSession("session_2")
            )

            stats = pool.stats()
            self.assertEqual(stats["total_sessions"], 2)
            self.assertEqual(stats["active_sessions"], 2)
            self.assertEqual(stats["max_sessions"], 5)
            self.assertEqual(stats["idle_timeout_seconds"], 2)
        finally:
            await pool.stop()

    async def async_test_graceful_stop(self):
        """Test graceful shutdown of pool."""
        await self.pool.start()

        # Create sessions
        session_1, _ = await self.pool.get_or_create(
            "session_1", lambda: MockSession("session_1")
        )
        session_2, _ = await self.pool.get_or_create(
            "session_2", lambda: MockSession("session_2")
        )

        # Stop pool
        await self.pool.stop()

        # Sessions should be marked closed
        self.assertTrue(session_1.closed)
        self.assertTrue(session_2.closed)

        # No sessions should exist
        count = await self.pool.active_count()
        self.assertEqual(count, 0)

    async def async_test_concurrent_access(self):
        """Test concurrent access to pool."""
        await self.pool.start()
        try:

            async def create_and_access(session_id: str, num_ops: int):
                for _ in range(num_ops):
                    session, _ = await self.pool.get_or_create(
                        session_id, lambda id=session_id: MockSession(id)
                    )
                    session.last_activity_at = time.time()
                    await asyncio.sleep(0.001)

            # Run 10 concurrent tasks
            tasks = [
                create_and_access(f"session_{i}", 5) for i in range(10)
            ]
            await asyncio.gather(*tasks)

            count = await self.pool.active_count()
            self.assertGreater(count, 0)
        finally:
            await self.pool.stop()

    def test_pool_create(self):
        """Run async test."""
        asyncio.run(self.async_test_pool_create())

    def test_lru_eviction(self):
        """Run async test."""
        asyncio.run(self.async_test_lru_eviction())

    def test_idle_cleanup(self):
        """Run async test."""
        asyncio.run(self.async_test_idle_cleanup())

    def test_closed_session_cleanup(self):
        """Run async test."""
        asyncio.run(self.async_test_closed_session_cleanup())

    def test_remove_session(self):
        """Run async test."""
        asyncio.run(self.async_test_remove_session())

    def test_get_nonexistent_session(self):
        """Run async test."""
        asyncio.run(self.async_test_get_nonexistent_session())

    def test_stats(self):
        """Run async test."""
        asyncio.run(self.async_test_stats())

    def test_graceful_stop(self):
        """Run async test."""
        asyncio.run(self.async_test_graceful_stop())

    def test_concurrent_access(self):
        """Run async test."""
        asyncio.run(self.async_test_concurrent_access())


if __name__ == "__main__":
    unittest.main()
