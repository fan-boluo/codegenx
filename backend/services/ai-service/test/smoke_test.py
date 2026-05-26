from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient


def _bootstrap_paths() -> tuple[Path, Path]:
    test_file = Path(__file__).resolve()
    ai_service_root = test_file.parents[1]
    backend_root = ai_service_root.parents[1]
    for path in (ai_service_root, backend_root):
        normalized = str(path)
        if normalized not in sys.path:
            sys.path.insert(0, normalized)
    return ai_service_root, backend_root


AI_SERVICE_ROOT, BACKEND_ROOT = _bootstrap_paths()
TEST_TEMP_ROOT = BACKEND_ROOT.parent / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)

if "pydantic_settings" not in sys.modules:
    from pydantic import BaseModel, ConfigDict

    pydantic_settings_stub = ModuleType("pydantic_settings")

    class _BaseSettings(BaseModel):
        model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    pydantic_settings_stub.BaseSettings = _BaseSettings
    pydantic_settings_stub.SettingsConfigDict = dict
    sys.modules["pydantic_settings"] = pydantic_settings_stub

if "openai" not in sys.modules:
    openai_stub = ModuleType("openai")

    class _AsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            self.chat = type("ChatNamespace", (), {"completions": None})()

    openai_stub.AsyncOpenAI = _AsyncOpenAI
    sys.modules["openai"] = openai_stub

import context.context as context_module
import engine as engine_module
import memory.hot as hot_memory_module
import memory.session as session_memory_module
import memory.warm as warm_memory_module
import task.task_manager as task_manager_module
import tools.grep as grep_module
import tools.memory as memory_tool_module
from engine import QueryEngine
from llm.llm_recovery import LLMMessageEnd, LLMTextToken, LLMToolUse
from schemas.event import ErrorEvent, ResultEvent, StreamTextEvent, ToolResultEvent
from tools.grep import GrepTool
from tools.memory import MemorySearchTool, MemoryWriteShortTermTool
from tools.task import TaskCreateTool
from tools.tool_handler import ToolRegistry


def _collect_events(generator) -> list[object]:
    async def _consume() -> list[object]:
        return [event async for event in generator]

    return asyncio.run(_consume())


def _load_app_module():
    module_name = "ai_service_app_smoke_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    monitor_package = ModuleType("monitor")
    monitor_package.__path__ = []  # type: ignore[attr-defined]

    maintenance_module = ModuleType("monitor.maintenance_service")
    query_service_module = ModuleType("monitor.monitor_query_service")

    class _MonitorStub:
        async def cleanup_history(self, **kwargs):
            return {"cleaned": 0, **kwargs}

        async def get_overview(self):
            return {}

        async def list_sessions(self, query):
            return {"records": [], "query": getattr(query, "model_dump", lambda **_: {})()}

        async def get_session_detail(self, session_id):
            return None

        async def get_turn_detail(self, session_id, turn_id):
            return None

        async def list_alerts(self, query):
            return {"records": [], "query": getattr(query, "model_dump", lambda **_: {})()}

        async def get_monitor_config(self):
            return {}

    monitor_stub = _MonitorStub()
    maintenance_module.get_monitor_maintenance_service = lambda: monitor_stub
    query_service_module.get_monitor_query_service = lambda: monitor_stub

    sys.modules.setdefault("monitor", monitor_package)
    sys.modules["monitor.maintenance_service"] = maintenance_module
    sys.modules["monitor.monitor_query_service"] = query_service_module

    spec = importlib.util.spec_from_file_location(module_name, AI_SERVICE_ROOT / "app.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load ai-service app module")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


class FakeLLMProvider:
    def __init__(self, rounds: list[list[object]]) -> None:
        self.rounds = rounds
        self.calls: list[dict[str, object]] = []

    async def _invoke_llm_with_recovery(self, messages, available_tools, *, stop_signal=None):
        self.calls.append(
            {
                "messages": list(messages),
                "available_tools": list(available_tools),
            }
        )
        round_index = len(self.calls) - 1
        for event in self.rounds[round_index]:
            if stop_signal is not None and stop_signal.is_set():
                raise asyncio.CancelledError("Session stopped")
            yield event


class BlockingLLMProvider:
    async def _invoke_llm_with_recovery(self, messages, available_tools, *, stop_signal=None):
        while stop_signal is None or not stop_signal.is_set():
            await asyncio.sleep(0.01)
        raise asyncio.CancelledError("Session stopped")
        yield  # pragma: no cover


@contextmanager
def patched_agent_storage(root: Path):
    def code_dir(app_id: str | int) -> Path:
        path = root / "code" / str(app_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def runtime_dir(app_id: str | int) -> Path:
        path = root / "runtime" / str(app_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def hot_memory_path() -> Path:
        path = root / "memory" / "MEMORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def topics_dir() -> Path:
        path = root / "memory" / "topics"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_memory_path(session_id: str) -> Path:
        path = root / "sessions" / str(session_id) / "MEMORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    with ExitStack() as stack:
        stack.enter_context(patch.object(context_module, "get_bot_code_dir", new=code_dir))
        stack.enter_context(patch.object(grep_module, "get_bot_code_dir", new=code_dir))
        stack.enter_context(patch.object(task_manager_module, "get_bot_code_dir", new=code_dir))
        stack.enter_context(patch.object(memory_tool_module, "get_bot_runtime_app_dir", new=runtime_dir))
        stack.enter_context(patch.object(hot_memory_module, "get_hot_memory_path", new=hot_memory_path))
        stack.enter_context(patch.object(warm_memory_module, "get_topics_dir", new=topics_dir))
        stack.enter_context(patch.object(session_memory_module, "get_session_memory_path", new=session_memory_path))
        stack.enter_context(patch.object(context_module.SkillLoader, "load_all_skills", return_value=[]))
        yield


@contextmanager
def managed_temp_root():
    root = TEST_TEMP_ROOT / f"ai-service-smoke-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class QueryEngineSmokeTest(unittest.TestCase):
    def test_query_engine_supports_grep_and_task_tools(self) -> None:
        with managed_temp_root() as root:
            with patched_agent_storage(root):
                app_id = "smoke-grep"
                code_dir = root / "code" / app_id / "src"
                code_dir.mkdir(parents=True, exist_ok=True)
                target_file = code_dir / "demo.py"
                target_file.write_text("hello_refactor_marker = True\n", encoding="utf-8")

                llm_provider = FakeLLMProvider(
                    [
                        [
                            LLMToolUse(
                                tool_name="grep",
                                tool_input={"pattern": "hello_refactor_marker", "path": "src", "glob": "*.py"},
                                tool_use_id="grep-1",
                            ),
                            LLMToolUse(
                                tool_name="task_create",
                                tool_input={"subject": "Add smoke task"},
                                tool_use_id="task-1",
                            ),
                            LLMMessageEnd(stop_reason="tool_use"),
                        ],
                        [
                            LLMTextToken(text="grep and task flow ok"),
                            LLMMessageEnd(stop_reason="end_turn"),
                        ],
                    ]
                )

                registry = ToolRegistry(tools=[GrepTool(), TaskCreateTool()], include_modules=())
                engine = QueryEngine(
                    session_id="session-grep",
                    app_id=app_id,
                    user_id="user-grep",
                    tool_registry=registry,
                    llm_provider=llm_provider,
                )
                engine.bind_request(
                    user_id="user-grep",
                    trace_id="trace-grep",
                    request_id="request-grep",
                    metadata={"workspace_mode": "smoke"},
                )

                events = _collect_events(engine.submit_message("find the marker and create a task"))

        tool_results = [event for event in events if isinstance(event, ToolResultEvent)]
        final_result = next(event for event in reversed(events) if isinstance(event, ResultEvent))

        self.assertTrue(any("demo.py:1:" in event.content for event in tool_results))
        self.assertTrue(any("Add smoke task" in event.content for event in tool_results))
        self.assertEqual(final_result.result, "grep and task flow ok")

        first_call_messages = llm_provider.calls[0]["messages"]
        self.assertEqual(first_call_messages[0]["role"], "system")
        self.assertIn("workspace_mode: smoke", first_call_messages[0]["content"])
        self.assertIn("## Available Tools", first_call_messages[0]["content"])

        second_call_messages = llm_provider.calls[1]["messages"]
        self.assertTrue(any(message.get("role") == "tool" for message in second_call_messages))

    def test_query_engine_uses_file_backed_memory_tools(self) -> None:
        with managed_temp_root() as root:
            with patched_agent_storage(root):
                app_id = "smoke-memory"
                llm_provider = FakeLLMProvider(
                    [
                        [
                            LLMToolUse(
                                tool_name="write_short_term",
                                tool_input={"content": "remember blue theme", "memory_type": "fact", "importance": 0.9},
                                tool_use_id="memory-write-1",
                            ),
                            LLMMessageEnd(stop_reason="tool_use"),
                        ],
                        [
                            LLMToolUse(
                                tool_name="memory_search",
                                tool_input={"query": "blue theme", "top_k": 3},
                                tool_use_id="memory-search-1",
                            ),
                            LLMMessageEnd(stop_reason="tool_use"),
                        ],
                        [
                            LLMTextToken(text="memory flow ok"),
                            LLMMessageEnd(stop_reason="end_turn"),
                        ],
                    ]
                )

                registry = ToolRegistry(
                    tools=[MemoryWriteShortTermTool(), MemorySearchTool()],
                    include_modules=(),
                )
                engine = QueryEngine(
                    session_id="session-memory",
                    app_id=app_id,
                    user_id="user-memory",
                    tool_registry=registry,
                    llm_provider=llm_provider,
                )
                engine.bind_request(user_id="user-memory", trace_id="trace-memory", request_id="request-memory")

                events = _collect_events(engine.submit_message("store and recall a preference"))
                memory_file = root / "runtime" / app_id / "memory_store" / "user-memory" / "short_term.jsonl"

        tool_results = [event for event in events if isinstance(event, ToolResultEvent)]
        final_result = next(event for event in reversed(events) if isinstance(event, ResultEvent))

        self.assertTrue(memory_file.exists())
        self.assertTrue(any("short_term memory saved" in event.content for event in tool_results))
        self.assertTrue(any("remember blue theme" in event.content for event in tool_results))
        self.assertEqual(final_result.result, "memory flow ok")

    def test_query_engine_stop_signal_returns_error_result(self) -> None:
        with managed_temp_root() as root:
            with patched_agent_storage(root):
                engine = QueryEngine(
                    session_id="session-stop",
                    app_id="smoke-stop",
                    user_id="user-stop",
                    tool_registry=ToolRegistry(tools=[], include_modules=()),
                    llm_provider=BlockingLLMProvider(),
                )
                engine.bind_request(user_id="user-stop", trace_id="trace-stop", request_id="request-stop")

                async def exercise() -> list[object]:
                    async def consume() -> list[object]:
                        return [event async for event in engine.submit_message("wait here")]

                    task = asyncio.create_task(consume())
                    await asyncio.sleep(0.05)
                    engine.request_stop()
                    return await task

                events = asyncio.run(exercise())

        error_event = next(event for event in events if isinstance(event, ErrorEvent))
        final_result = next(event for event in reversed(events) if isinstance(event, ResultEvent))

        self.assertEqual(error_event.message, "request stopped")
        self.assertTrue(final_result.is_error)
        self.assertEqual(final_result.subtype, "error_during_execution")


class AppRouteSmokeTest(unittest.TestCase):
    def test_app_stream_and_health_routes_work_with_session_store(self) -> None:
        module = _load_app_module()

        class FakeEngine:
            def __init__(self, session_id: str, app_id: str, user_id: str = "anonymous") -> None:
                self.session_id = session_id
                self.app_id = app_id
                self.user_id = user_id
                self.bound_requests: list[dict[str, object]] = []

            def bind_request(self, **kwargs) -> None:
                self.bound_requests.append(dict(kwargs))

            def request_stop(self) -> None:
                return None

            async def submit_message(self, prompt: str):
                yield StreamTextEvent(text=f"echo:{prompt}")
                yield ResultEvent(
                    subtype="success",
                    result=f"echo:{prompt}",
                    session_id=self.session_id,
                    num_turns=1,
                )

        module.session_store._store.clear()
        with patch.object(module, "QueryEngine", FakeEngine):
            with TestClient(module.app) as client:
                response = client.post(
                    "/api/ai/codegen/stream",
                    json={
                        "appId": 901,
                        "userId": "user-route",
                        "message": "hello",
                        "traceId": "trace-route",
                        "requestId": "request-route",
                        "sessionId": "session-route",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn('"type": "stream_text"', response.text)
                self.assertIn("data: [DONE]", response.text)

                internal_response = client.post(
                    "/internal/ai/codegen/stream",
                    json={
                        "appId": 901,
                        "userId": "user-route",
                        "message": "hello",
                        "traceId": "trace-route",
                        "requestId": "request-route-2",
                        "sessionId": "session-route",
                    },
                    headers={"host": "ai-service:8002"},
                )
                self.assertEqual(internal_response.status_code, 200)
                self.assertIn("event: meta", internal_response.text)
                self.assertIn("event: chunk", internal_response.text)
                self.assertIn("event: done", internal_response.text)

        self.assertEqual(module.session_store.count(), 1)
        engine = module.session_store.get("session-route")
        self.assertIsNotNone(engine)
        self.assertEqual(engine.user_id, "user-route")
        self.assertEqual(engine.bound_requests[0]["request_id"], "request-route")


if __name__ == "__main__":
    unittest.main()
