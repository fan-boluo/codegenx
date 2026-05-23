"""
Unit tests for AgentRuntime optimization (session pool integration).
Run from project root:
  python -m pytest backend/services/ai-service/test/test_runtime_optimization.py -v
  或
  python -m unittest backend.services.ai-service.test.test_runtime_optimization -v
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Setup paths
test_file = Path(__file__).resolve()
ai_service_root = test_file.parents[1]
backend_root = ai_service_root.parents[1]
for path in (ai_service_root, ai_service_root / 'bot', backend_root):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

# Fake monitor modules for lightweight testing
monitor_module = types.ModuleType("monitor")
sys.modules["monitor"] = monitor_module

alert_evaluator = types.ModuleType("monitor.alert_evaluator")
alert_evaluator.get_monitor_alert_evaluator = lambda: None
sys.modules["monitor.alert_evaluator"] = alert_evaluator

health_checker = types.ModuleType("monitor.health_checker")
health_checker.get_health_checker = lambda: None
sys.modules["monitor.health_checker"] = health_checker

maintenance_service = types.ModuleType("monitor.maintenance_service")
maintenance_service.get_monitor_maintenance_service = lambda: None
sys.modules["monitor.maintenance_service"] = maintenance_service

monitor_pipeline = types.ModuleType("monitor.monitor_pipeline")
monitor_pipeline.get_monitor_pipeline = lambda: None
sys.modules["monitor.monitor_pipeline"] = monitor_pipeline

monitor_query_service = types.ModuleType("monitor.monitor_query_service")
monitor_query_service.get_monitor_query_service = lambda: None
sys.modules["monitor.monitor_query_service"] = monitor_query_service

monitor_store = types.ModuleType("monitor.monitor_store")
monitor_store.get_monitor_store = lambda: None
sys.modules["monitor.monitor_store"] = monitor_store

telemetry_schema = types.ModuleType("monitor.telemetry_schema")
telemetry_schema.SessionTelemetry = object
telemetry_schema.TurnTelemetry = object
telemetry_schema.SpanRecord = object
telemetry_schema.TelemetryStatus = type("TelemetryStatus", (), {"RUNNING": "running"})
sys.modules["monitor.telemetry_schema"] = telemetry_schema

infra_module = types.ModuleType("infra")
sys.modules["infra"] = infra_module
infra_mysql = types.ModuleType("infra.mysql")
sys.modules["infra.mysql"] = infra_mysql
infra_mysql_session = types.ModuleType("infra.mysql.session")
infra_mysql_session.warm_up_mysql_pool = lambda: None
sys.modules["infra.mysql.session"] = infra_mysql_session

infra_qdrant = types.ModuleType("infra.qdrant")
sys.modules["infra.qdrant"] = infra_qdrant
infra_qdrant_client = types.ModuleType("infra.qdrant.client")
infra_qdrant_client.warm_up_qdrant_client = lambda: None
sys.modules["infra.qdrant.client"] = infra_qdrant_client

from bot.agent.runtime import AgentRuntime
from bot.agent.session_pool import SessionPool
from bot.agent.runtime_schema import AgentState, RuntimeSessionState
from shared.schema.ai_service import AiServiceGenerateRequest


class MockAgentConfig:
    """Mock configuration."""

    def __init__(self):
        self.max_sessions = 100
        self.session_idle_timeout_seconds = 3600
        self.session_cleanup_interval_seconds = 300
        self.max_tool_iterations = 40
        self.session_stop_grace_seconds = 2.0
        self.session_worker_idle_seconds = 3600


class MockConfig:
    """Mock config loader."""

    def get_default_agent(self):
        return MockAgentConfig()


class MockMessageBus:
    """Mock message bus."""

    async def consume_inbound(self):
        # Simulate no messages
        await asyncio.sleep(10)

    async def publish_outbound(self, event):
        pass

    def subscribe_request(self, request_id):
        return AsyncMock()

    def unsubscribe_request(self, request_id, subscriber):
        pass


class MockHookRunner:
    """Mock hook runner."""

    async def dispatch(self, hook_name, *args, **kwargs):
        pass


class TestAgentRuntimeOptimization(unittest.TestCase):
    """Test AgentRuntime with SessionPool optimization."""

    def setUp(self):
        """Setup test fixtures."""
        self.mock_config = MockConfig()
        self.mock_message_bus = MockMessageBus()
        self.mock_hook_runner = MockHookRunner()

    async def async_test_session_pool_integration(self):
        """Test that AgentRuntime properly integrates SessionPool."""
        # Verify pool attributes
        pool = SessionPool(max_sessions=100, idle_timeout_seconds=3600)
        self.assertIsNotNone(pool)
        self.assertEqual(pool.max_sessions, 100)
        self.assertEqual(pool.idle_timeout_seconds, 3600)
        self.assertEqual(pool.cleanup_interval_seconds, 300)

    async def async_test_session_pool_start_stop(self):
        """Test SessionPool lifecycle."""
        pool = SessionPool(max_sessions=10, idle_timeout_seconds=2)

        # Start pool
        await pool.start()
        self.assertIsNotNone(pool._cleanup_task)
        self.assertFalse(pool._cleanup_task.done())

        # Stop pool
        await pool.stop()
        self.assertIsNone(pool._cleanup_task)

    async def async_test_session_state_creation_with_pool(self):
        """Test creating session states with pool."""
        pool = SessionPool(max_sessions=10)
        await pool.start()
        try:
            # Create mock session state
            request = AiServiceGenerateRequest(
                request_id="test_req_1",
                session_id="test_session_1",
                app_id=1,
                message="test message",
            )

            def create_session():
                return RuntimeSessionState(
                    session_id="test_session_1",
                    request=request,
                    runtime=None,  # Runtime not needed for this test
                )

            # Get or create session
            session, is_new = await pool.get_or_create("test_session_1", create_session)
            self.assertTrue(is_new)
            self.assertEqual(session.session_id, "test_session_1")
            self.assertEqual(session.request.request_id, "test_req_1")

            # Get again - should not be new
            session2, is_new2 = await pool.get_or_create("test_session_1", create_session)
            self.assertFalse(is_new2)
            self.assertIs(session, session2)
        finally:
            await pool.stop()

    async def async_test_enqueue_session_request_triggers_processor(self):
        """Test that enqueuing a request starts the session processor."""
        runtime = object.__new__(AgentRuntime)
        runtime._shutdown_event = asyncio.Event()
        runtime._shutdown_event.clear()
        runtime._execute_request = AsyncMock()
        runtime.stop_grace_seconds = 0.0
        runtime._publish_stopped_request = AsyncMock()
        runtime.session_pool = AsyncMock()
        runtime.message_bus = AsyncMock()

        request = AiServiceGenerateRequest(
            request_id="req_enqueue",
            session_id="session_1",
            app_id=1,
            message="hello",
        )
        session_state = RuntimeSessionState(
            session_id="session_1",
            request=request,
            runtime=runtime,
        )
        session_state.session_manager = MagicMock()

        await runtime._enqueue_session_request(session_state, request)
        self.assertTrue(session_state.worker_task is not None)
        await session_state.worker_task
        runtime._execute_request.assert_awaited_once_with(session_state)
        self.assertFalse(session_state.processing)
        self.assertEqual(session_state.pending_requests, [])

    async def async_test_stop_request_removes_pending_requests(self):
        """Test stop_request drops pending requests and keeps remaining ones."""
        runtime = object.__new__(AgentRuntime)
        runtime.stop_grace_seconds = 0.0
        runtime._publish_stopped_request = AsyncMock()
        runtime._request_id = lambda request: str(request.request_id or "")

        active_request = AiServiceGenerateRequest(
            request_id="active_req",
            session_id="session_2",
            app_id=1,
            message="active",
        )
        pending_request = AiServiceGenerateRequest(
            request_id="pending_req",
            session_id="session_2",
            app_id=1,
            message="pending",
        )

        session_state = RuntimeSessionState(
            session_id="session_2",
            request=active_request,
            runtime=runtime,
        )
        session_state.pending_requests = [pending_request]
        session_state.active_tasks = {}
        session_state.stop_signal = asyncio.Event()
        session_state.stop_reason = ""

        runtime.session_pool = AsyncMock()
        runtime.session_pool.get = AsyncMock(return_value=session_state)

        result = await runtime.stop_request(
            session_id="session_2",
            request_id="pending_req",
            reason="user-stop",
            grace_seconds=0.0,
        )

        self.assertEqual(result["accepted"], True)
        self.assertEqual(result["droppedRequestCount"], 1)
        self.assertEqual(result["activeRequestIds"], [])
        self.assertEqual(result["droppedRequestIds"], ["pending_req"])
        self.assertEqual(session_state.pending_requests, [])
        runtime._publish_stopped_request.assert_awaited_once()

    async def async_test_multiple_concurrent_sessions(self):
        """Test handling multiple concurrent sessions."""
        pool = SessionPool(max_sessions=50)
        await pool.start()
        try:

            async def create_session_task(session_num: int):
                request = AiServiceGenerateRequest(
                    request_id=f"req_{session_num}",
                    session_id=f"session_{session_num}",
                    app_id=1,
                    message=f"message {session_num}",
                )

                def factory():
                    return RuntimeSessionState(
                        session_id=f"session_{session_num}",
                        request=request,
                        runtime=None,
                    )

                session, is_new = await pool.get_or_create(f"session_{session_num}", factory)
                self.assertTrue(is_new)
                return session

            # Create 20 concurrent sessions
            tasks = [create_session_task(i) for i in range(20)]
            sessions = await asyncio.gather(*tasks)

            self.assertEqual(len(sessions), 20)

            # Verify all sessions in pool
            count = await pool.active_count()
            self.assertEqual(count, 20)
        finally:
            await pool.stop()

    async def async_test_session_cleanup_on_idle(self):
        """Test that idle sessions are cleaned up."""
        pool = SessionPool(
            max_sessions=10,
            idle_timeout_seconds=1,
            cleanup_interval_seconds=0.5,
        )
        await pool.start()
        try:
            # Create session with old last_activity_at
            request = AiServiceGenerateRequest(
                request_id="test_req_1",
                session_id="test_session_1",
                app_id=1,
                message="test",
            )

            def create_session():
                session = RuntimeSessionState(
                    session_id="test_session_1", request=request, runtime=None
                )
                session.last_activity_at = time.time() - 2  # 2 seconds old
                return session

            session, _ = await pool.get_or_create("test_session_1", create_session)
            count = await pool.active_count()
            self.assertEqual(count, 1)

            # Wait for cleanup
            await asyncio.sleep(2)

            # Session should be cleaned up
            count = await pool.active_count()
            self.assertEqual(count, 0)
        finally:
            await pool.stop()

    async def async_test_session_pool_stats(self):
        """Test pool statistics."""
        pool = SessionPool(max_sessions=100, idle_timeout_seconds=3600)
        await pool.start()
        try:
            # Create a few sessions
            for i in range(5):
                request = AiServiceGenerateRequest(
                    request_id=f"req_{i}",
                    session_id=f"session_{i}",
                    app_id=1,
                    message="test",
                )

                def factory(i=i):
                    return RuntimeSessionState(
                        session_id=f"session_{i}", request=request, runtime=None
                    )

                await pool.get_or_create(f"session_{i}", factory)

            stats = pool.stats()
            self.assertEqual(stats["total_sessions"], 5)
            self.assertEqual(stats["active_sessions"], 5)
            self.assertEqual(stats["max_sessions"], 100)
            self.assertEqual(stats["idle_timeout_seconds"], 3600)
        finally:
            await pool.stop()

    async def async_test_lru_session_eviction(self):
        """Test LRU eviction prevents unbounded memory growth."""
        pool = SessionPool(max_sessions=5, idle_timeout_seconds=3600)
        await pool.start()
        try:
            # Fill pool to capacity
            for i in range(5):
                request = AiServiceGenerateRequest(
                    request_id=f"req_{i}",
                    session_id=f"session_{i}",
                    app_id=1,
                    message="test",
                )

                def factory(i=i):
                    return RuntimeSessionState(
                        session_id=f"session_{i}", request=request, runtime=None
                    )

                await pool.get_or_create(f"session_{i}", factory)
                await asyncio.sleep(0.01)

            count = await pool.active_count()
            self.assertEqual(count, 5)

            # Access session_2 to make it recent
            await pool.get("session_2")

            # Add new session - should evict session_0
            await asyncio.sleep(0.01)
            request = AiServiceGenerateRequest(
                request_id="req_new",
                session_id="session_new",
                app_id=1,
                message="test",
            )

            def factory():
                return RuntimeSessionState(
                    session_id="session_new", request=request, runtime=None
                )

            await pool.get_or_create("session_new", factory)

            # session_0 should be gone
            session_0 = await pool.get("session_0")
            self.assertIsNone(session_0)

            # session_2 should still exist
            session_2 = await pool.get("session_2")
            self.assertIsNotNone(session_2)

            # Total count should still be 5
            count = await pool.active_count()
            self.assertEqual(count, 5)
        finally:
            await pool.stop()

    async def async_test_concurrent_access_safety(self):
        """Test thread-safe concurrent access to pool."""
        pool = SessionPool(max_sessions=50)
        await pool.start()
        try:

            async def worker(worker_id: int, num_ops: int):
                results = []
                for op in range(num_ops):
                    session_id = f"session_{worker_id % 10}_{op}"
                    request = AiServiceGenerateRequest(
                        request_id=f"req_{worker_id}_{op}",
                        session_id=session_id,
                        app_id=1,
                        message="test",
                    )

                    def factory(sid=session_id):
                        return RuntimeSessionState(
                            session_id=sid, request=request, runtime=None
                        )

                    session, _ = await pool.get_or_create(session_id, factory)
                    results.append(session)
                    await asyncio.sleep(0.001)
                return results

            # Run 10 concurrent workers
            tasks = [worker(i, 5) for i in range(10)]
            all_sessions = await asyncio.gather(*tasks)

            # All sessions should have been created
            total_created = sum(len(sessions) for sessions in all_sessions)
            self.assertEqual(total_created, 50)  # 10 workers * 5 ops each

            # Pool should not exceed max_sessions
            count = await pool.active_count()
            self.assertLessEqual(count, 50)
        finally:
            await pool.stop()

    def test_enqueue_session_request_triggers_processor(self):
        """Run async test for enqueue session request."""
        asyncio.run(self.async_test_enqueue_session_request_triggers_processor())

    def test_stop_request_removes_pending_requests(self):
        """Run async test for stop_request pending request removal."""
        asyncio.run(self.async_test_stop_request_removes_pending_requests())

    def test_session_pool_integration(self):
        """Run async test."""
        asyncio.run(self.async_test_session_pool_integration())

    def test_session_pool_start_stop(self):
        """Run async test."""
        asyncio.run(self.async_test_session_pool_start_stop())

    def test_session_state_creation_with_pool(self):
        """Run async test."""
        asyncio.run(self.async_test_session_state_creation_with_pool())

    def test_multiple_concurrent_sessions(self):
        """Run async test."""
        asyncio.run(self.async_test_multiple_concurrent_sessions())

    def test_session_cleanup_on_idle(self):
        """Run async test."""
        asyncio.run(self.async_test_session_cleanup_on_idle())

    def test_session_pool_stats(self):
        """Run async test."""
        asyncio.run(self.async_test_session_pool_stats())

    def test_lru_session_eviction(self):
        """Run async test."""
        asyncio.run(self.async_test_lru_session_eviction())

    def test_concurrent_access_safety(self):
        """Run async test."""
        asyncio.run(self.async_test_concurrent_access_safety())


if __name__ == "__main__":
    unittest.main()
