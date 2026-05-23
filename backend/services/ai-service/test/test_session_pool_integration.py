"""
Simplified integration tests for SessionPool with AgentRuntime patterns.
This test doesn't require full runtime dependencies.

Run from ai-service directory:
  python test/test_session_pool_integration.py -v
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

from bot.agent.session_pool import SessionPool


class MockSessionState:
    """Mock RuntimeSessionState for testing without full runtime dependency."""

    def __init__(self, session_id: str, request_id: str):
        self.session_id = session_id
        self.request_id = request_id
        self.closed = False
        self.last_activity_at = time.time()
        self.worker_task = None
        self.active_tasks = {}
        self.queue = asyncio.Queue()
        self.stop_signal = asyncio.Event()
        self.stop_reason = ""

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity_at = time.time()

    async def close(self):
        """Gracefully close the session."""
        self.closed = True
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()


class TestSessionPoolIntegration(unittest.TestCase):
    """Integration tests for SessionPool with AgentRuntime patterns."""

    async def async_test_dispatch_pattern(self):
        """Test typical dispatch_loop pattern with session pool."""
        pool = SessionPool(
            max_sessions=10,
            idle_timeout_seconds=60,
            cleanup_interval_seconds=5,
        )
        await pool.start()
        try:
            # Simulate incoming requests
            incoming_requests = [
                {"request_id": f"req_{i}", "session_id": f"session_{i % 3}"}
                for i in range(10)
            ]

            created_sessions = []
            for req in incoming_requests:
                session_id = req["session_id"]

                # Factory function like in dispatch_loop
                def make_session():
                    return MockSessionState(
                        session_id=session_id,
                        request_id=req["request_id"],
                    )

                # Get or create
                session, is_new = await pool.get_or_create(session_id, make_session)
                session.touch()

                if is_new:
                    created_sessions.append(session_id)

            # Should have created 3 unique sessions
            self.assertEqual(len(set(created_sessions)), 3)

            # Pool should have 3 active sessions
            count = await pool.active_count()
            self.assertEqual(count, 3)
        finally:
            await pool.stop()

    async def async_test_session_reuse_pattern(self):
        """Test session reuse across multiple requests."""
        pool = SessionPool(max_sessions=10)
        await pool.start()
        try:
            session_id = "long_running_session"

            # Create session for first request
            req1_session, is_new1 = await pool.get_or_create(
                session_id,
                lambda: MockSessionState(session_id, "req_1"),
            )
            self.assertTrue(is_new1)

            # Add task to session
            req1_session.active_tasks["task_1"] = asyncio.create_task(
                asyncio.sleep(0.1)
            )

            # Get same session for second request
            req2_session, is_new2 = await pool.get_or_create(
                session_id,
                lambda: MockSessionState(session_id, "req_2"),
            )
            self.assertFalse(is_new2)
            self.assertIs(req1_session, req2_session)

            # Session maintains state
            self.assertEqual(len(req2_session.active_tasks), 1)

            # Wait for task
            await asyncio.gather(*req2_session.active_tasks.values())
        finally:
            await pool.stop()

    async def async_test_session_lifecycle(self):
        """Test complete session lifecycle."""
        pool = SessionPool(
            max_sessions=10,
            idle_timeout_seconds=2,
            cleanup_interval_seconds=0.5,
        )
        await pool.start()
        try:
            session_id = "lifecycle_test_session"

            # Create session
            session, _ = await pool.get_or_create(
                session_id,
                lambda: MockSessionState(session_id, "req_1"),
            )
            self.assertFalse(session.closed)

            count = await pool.active_count()
            self.assertEqual(count, 1)

            # Mark as closed (simulating OnSessionEnd hook)
            session.closed = True

            # Wait for cleanup
            await asyncio.sleep(1.5)

            # Session should be cleaned up
            count = await pool.active_count()
            self.assertEqual(count, 0)
        finally:
            await pool.stop()

    async def async_test_high_concurrency_pattern(self):
        """Test high concurrency scenario."""
        pool = SessionPool(
            max_sessions=100,
            idle_timeout_seconds=60,
            cleanup_interval_seconds=5,
        )
        await pool.start()
        try:

            async def simulate_request_processing(session_id: str, num_reqs: int):
                results = []
                for i in range(num_reqs):
                    request_id = f"req_{session_id}_{i}"

                    session, is_new = await pool.get_or_create(
                        session_id,
                        lambda: MockSessionState(session_id, request_id),
                    )

                    # Simulate request execution
                    session.touch()
                    await asyncio.sleep(0.001)
                    results.append((session_id, request_id, is_new))

                return results

            # Simulate 50 concurrent sessions, each with 2 requests
            tasks = [
                simulate_request_processing(f"session_{i}", 2) for i in range(50)
            ]
            all_results = await asyncio.gather(*tasks)

            # Flatten results
            total_reqs = sum(len(results) for results in all_results)
            self.assertEqual(total_reqs, 100)

            # Should have created 50 sessions
            count = await pool.active_count()
            self.assertEqual(count, 50)

            # Check is_new pattern (first request should have is_new=True)
            for results in all_results:
                first_req_is_new = results[0][2]
                second_req_is_new = results[1][2]
                self.assertTrue(first_req_is_new)
                self.assertFalse(second_req_is_new)
        finally:
            await pool.stop()

    async def async_test_pool_under_pressure(self):
        """Test pool behavior under load (max capacity reached)."""
        pool = SessionPool(
            max_sessions=20,
            idle_timeout_seconds=60,
            cleanup_interval_seconds=5,
        )
        await pool.start()
        try:

            async def create_session_flood(count: int):
                for i in range(count):
                    session_id = f"flood_session_{i}"
                    session, _ = await pool.get_or_create(
                        session_id,
                        lambda id=session_id: MockSessionState(id, f"req_{i}"),
                    )
                    await asyncio.sleep(0.001)

            # Try to create 100 sessions (pool max is 20)
            await create_session_flood(100)

            # Pool should not exceed max capacity
            count = await pool.active_count()
            self.assertLessEqual(count, 20)

            # Pool stats should be accurate
            stats = pool.stats()
            self.assertLessEqual(stats["active_sessions"], stats["max_sessions"])
        finally:
            await pool.stop()

    async def async_test_stop_request_pattern(self):
        """Test stop_request pattern with multiple active tasks."""
        pool = SessionPool(max_sessions=10)
        await pool.start()
        try:
            session_id = "stop_test_session"

            # Create session with multiple tasks
            session, _ = await pool.get_or_create(
                session_id,
                lambda: MockSessionState(session_id, "req_1"),
            )

            # Simulate active tasks
            async def long_task():
                await asyncio.sleep(10)

            session.active_tasks["task_1"] = asyncio.create_task(long_task())
            session.active_tasks["task_2"] = asyncio.create_task(long_task())

            # Set stop signal
            session.stop_signal.set()
            session.stop_reason = "user-stop"

            # Verify stop signal is set
            self.assertTrue(session.stop_signal.is_set())
            self.assertEqual(session.stop_reason, "user-stop")

            # Cancel tasks
            for task in session.active_tasks.values():
                task.cancel()

            # Mark session as closed
            session.closed = True

            # Verify closed
            self.assertTrue(session.closed)
        finally:
            await pool.stop()

    def test_dispatch_pattern(self):
        """Run async test."""
        asyncio.run(self.async_test_dispatch_pattern())

    def test_session_reuse_pattern(self):
        """Run async test."""
        asyncio.run(self.async_test_session_reuse_pattern())

    def test_session_lifecycle(self):
        """Run async test."""
        asyncio.run(self.async_test_session_lifecycle())

    def test_high_concurrency_pattern(self):
        """Run async test."""
        asyncio.run(self.async_test_high_concurrency_pattern())

    def test_pool_under_pressure(self):
        """Run async test."""
        asyncio.run(self.async_test_pool_under_pressure())

    def test_stop_request_pattern(self):
        """Run async test."""
        asyncio.run(self.async_test_stop_request_pattern())


if __name__ == "__main__":
    unittest.main()
