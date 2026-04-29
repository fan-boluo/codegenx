from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[2]
AI_SERVICE_ROOT = BACKEND_ROOT / "services" / "ai-service"
AI_SERVICE_SERVICES_ROOT = AI_SERVICE_ROOT / "services"
for candidate in (str(AI_SERVICE_SERVICES_ROOT), str(AI_SERVICE_ROOT), str(BACKEND_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _load_ai_service_app_module():
    module_name = "ai_service_app_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, AI_SERVICE_ROOT / "app.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load ai-service app module")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FakeAgentService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def stream_message(self, **kwargs):
        self.calls.append(kwargs)
        yield "alpha"
        yield "beta"


class FakeEventAgentService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def stream_events(self, **kwargs):
        from bot.agent.runtime import AgentEvent, AgentState

        self.calls.append(kwargs)
        yield AgentEvent(event_type="LLM_Thinking_Start", data={"prompt_tokens": 12}, state=AgentState.RUNNING)
        yield AgentEvent(event_type="LLM_Response_Chunk", data="alpha", state=AgentState.RUNNING)
        yield AgentEvent(event_type="TurnCompleted", state=AgentState.COMPLETED)


class AiServiceAgentStreamContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_ai_service_app_module()
        self.fake_agent = FakeAgentService()
        self.original_agent = self.module.agent_service
        self.module.agent_service = self.fake_agent
        self.client = TestClient(self.module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.module.agent_service = self.original_agent

    def test_public_stream_uses_agent_adapter_without_codegen_type(self) -> None:
        response = self.client.post(
            "/api/ai/codegen/stream",
            json={
                "appId": 101,
                "message": "生成一个官网首页",
                "traceId": "trace-101",
                "requestId": "req-101",
                "sessionId": "session-101",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "alphabeta")
        self.assertEqual(len(self.fake_agent.calls), 1)
        self.assertEqual(self.fake_agent.calls[0]["app_id"], 101)
        self.assertEqual(self.fake_agent.calls[0]["user_message"], "生成一个官网首页")
        self.assertEqual(self.fake_agent.calls[0]["trace_id"], "trace-101")
        self.assertEqual(self.fake_agent.calls[0]["request_id"], "req-101")
        self.assertEqual(self.fake_agent.calls[0]["session_id"], "session-101")
        self.assertIsNone(self.fake_agent.calls[0]["requested_code_gen_type"])

    def test_internal_stream_wraps_agent_chunks_as_sse(self) -> None:
        response = self.client.post(
            "/internal/ai/codegen/stream",
            json={
                "appId": 202,
                "message": "继续完善页面",
                "traceId": "trace-1",
                "requestId": "req-1",
                "sessionId": "session-1",
            },
            headers={"host": "ai-service:8002"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: meta", response.text)
        self.assertIn('"traceId":"trace-1"', response.text)
        self.assertIn("event: chunk", response.text)
        self.assertIn('"content":"alpha","index":0', response.text)
        self.assertIn('"content":"beta","index":1', response.text)
        self.assertIn("event: done", response.text)
        self.assertEqual(len(self.fake_agent.calls), 1)
        self.assertEqual(self.fake_agent.calls[0]["trace_id"], "trace-1")
        self.assertEqual(self.fake_agent.calls[0]["request_id"], "req-1")
        self.assertEqual(self.fake_agent.calls[0]["session_id"], "session-1")

    def test_internal_stream_passthroughs_agent_events_when_supported(self) -> None:
        original_agent = self.module.agent_service
        fake_event_agent = FakeEventAgentService()
        self.module.agent_service = fake_event_agent
        try:
            response = self.client.post(
                "/internal/ai/codegen/stream",
                json={
                    "appId": 203,
                    "message": "继续完善页面",
                    "traceId": "trace-evt",
                    "requestId": "req-evt",
                    "sessionId": "session-evt",
                },
                headers={"host": "ai-service:8002"},
            )
        finally:
            self.module.agent_service = original_agent

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: LLM_Thinking_Start", response.text)
        self.assertIn('"eventType": "LLM_Thinking_Start"', response.text)
        self.assertIn("event: chunk", response.text)
        self.assertIn('"content":"alpha","index":0', response.text)
        self.assertEqual(len(fake_event_agent.calls), 1)

    def test_route_endpoints_are_removed(self) -> None:
        public_response = self.client.post("/api/ai/codegen/route", json={"initPrompt": "生成官网"})
        internal_response = self.client.post("/internal/ai/codegen/route", json={"initPrompt": "生成官网"})

        self.assertEqual(public_response.status_code, 404)
        self.assertEqual(internal_response.status_code, 404)


class AgentAdapterServiceTest(unittest.TestCase):
    def test_adapter_reuses_context_for_same_app_and_session(self) -> None:
        from agent_adapter_service import AgentAdapterService
        from bot.agent.runtime import AgentEvent, AgentState

        service = AgentAdapterService()
        runtime = service._get_runtime("301")
        seen_sessions: list[str] = []

        async def fake_run_turn(context):
            seen_sessions.append(context.session_id)
            yield AgentEvent(event_type="OnTurnStart", state=AgentState.RUNNING)
            yield AgentEvent(event_type="LLM_Response_Chunk", data=context.user_input, state=AgentState.RUNNING)
            yield AgentEvent(event_type="TurnCompleted", state=AgentState.COMPLETED)

        runtime.run_turn = fake_run_turn

        async def exercise() -> tuple[str, str]:
            first_chunks: list[str] = []
            async for chunk in service.stream_message(
                app_id=301,
                session_id="session-301",
                user_message="first",
                trace_id="trace-301-a",
                request_id="req-301-a",
            ):
                first_chunks.append(chunk)

            second_chunks: list[str] = []
            async for chunk in service.stream_message(
                app_id=301,
                session_id="session-301",
                user_message="second",
                trace_id="trace-301-b",
                request_id="req-301-b",
            ):
                second_chunks.append(chunk)

            return "".join(first_chunks), "".join(second_chunks)

        first_result, second_result = asyncio.run(exercise())

        self.assertEqual(first_result, "first")
        self.assertEqual(second_result, "second")
        self.assertEqual(len(seen_sessions), 2)
        self.assertEqual(seen_sessions[0], seen_sessions[1])
        self.assertEqual(seen_sessions[0], service.get_session_id(301, "session-301"))

    def test_adapter_keeps_separate_contexts_for_different_sessions(self) -> None:
        from agent_adapter_service import AgentAdapterService

        service = AgentAdapterService()

        first = service._get_context(302, "session-a")
        second = service._get_context(302, "session-b")

        self.assertNotEqual(first.session_id, second.session_id)


class AgentRuntimeGuardTest(unittest.TestCase):
    def test_runtime_stops_repeated_tool_loop(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, *args, **kwargs):
                return None

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(
                    tools=[SimpleNamespace(name="read_file", description="read", parameters={})]
                )

            async def execute(self, tool_call, context):
                return {"content": "stub"}

        class RepeatingToolLLMClient:
            async def invoke_stream(self, messages, tools):
                yield {
                    "type": "tool_calls",
                    "data": [{"id": "call-1", "name": "read_file", "arguments": {"path": "index.html"}}],
                }

        runtime = AgentRuntime(
            app_id="401",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="401", session_id="session-1", turn_id="turn-1", user_input="生成首页")

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=RepeatingToolLLMClient()):
                events: list[tuple[str, object]] = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.FAILED)
        self.assertTrue(any(event_type == "Error" for event_type, _ in events))
        self.assertIn("repeated the same tool call", str(events[-1][1]))

    def test_runtime_recovers_after_transient_llm_timeout(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, *args, **kwargs):
                return None

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            async def compact_history(self, context, focus=None, reason="threshold"):
                context.metadata["compact_called"] = True

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(tools=[])

        class FlakyLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke_stream(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("upstream timeout while streaming response")
                yield {"type": "content", "data": "recovered"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        runtime = AgentRuntime(
            app_id="402",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="402", session_id="session-2", turn_id="turn-2", user_input="继续生成")
        llm_client = FlakyLLMClient()

        async def immediate_sleep(_delay):
            return None

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=llm_client), patch(
                "bot.agent.runtime.asyncio.sleep",
                new=immediate_sleep,
            ):
                events: list[tuple[str, object]] = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertTrue(any(event_type == "RecoveryDecision" for event_type, _ in events))
        self.assertEqual(context.recovery_state["transport_attempts"], 1)
        self.assertEqual(context.history[-1]["content"], "recovered")

    def test_runtime_recovers_from_max_tokens_with_continuation(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, *args, **kwargs):
                return None

        class FakeContextAssembler:
            async def assemble(self, context):
                return list(context.history)

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            async def compact_history(self, context, focus=None, reason="threshold"):
                context.metadata["compact_called"] = True

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(tools=[])

        class TruncatingLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke_stream(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    yield {"type": "content", "data": "partial"}
                    yield {"type": "response_info", "data": {"finish_reason": "max_tokens"}}
                    return
                yield {"type": "content", "data": " completion"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        runtime = AgentRuntime(
            app_id="403",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="403", session_id="session-3", turn_id="turn-3", user_input="生成长内容")
        llm_client = TruncatingLLMClient()

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=llm_client):
                events: list[tuple[str, object]] = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertEqual(context.recovery_state["continuation_attempts"], 1)
        self.assertTrue(any(event_type == "RecoveryDecision" for event_type, _ in events))
        self.assertTrue(any(message.get("content") == runtime.CONTINUATION_MESSAGE for message in context.history if message.get("role") == "user"))
        self.assertEqual(context.history[-1]["content"], " completion")

    def test_runtime_emits_compaction_events_from_compactor_state(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, *args, **kwargs):
                return None

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                state = context.metadata.setdefault("context_compaction", {"events": []})
                state.setdefault("events", []).append(
                    {"reason": "micro-pre-llm", "history_size": 42}
                )

            async def finalize_turn(self, context):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(tools=[])

        class SingleReplyLLMClient:
            async def invoke_stream(self, messages, tools):
                yield {"type": "content", "data": "ok"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        runtime = AgentRuntime(
            app_id="404",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="404", session_id="session-4", turn_id="turn-4", user_input="继续")

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=SingleReplyLLMClient()):
                events: list[tuple[str, object]] = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertTrue(any(event_type == "Compaction" for event_type, _ in events))
        self.assertEqual(context.metadata["compaction_state"]["has_compacted"], False)

    def test_runtime_syncs_canonical_turn_state_fields(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, event_name, context, **kwargs):
                if event_name == "OnSessionStart":
                    context.plan_state = "[>] Review runtime alignment"
                if event_name == "PostToolUse":
                    context.metrics.append({"tool_name": "read_file", "execution_time": 0.1})
                return {}

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(
                    tools=[SimpleNamespace(name="read_file", description="read", parameters={})]
                )

            async def execute(self, tool_call, context):
                return {"content": "README contents"}

        class SingleToolLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke_stream(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    yield {
                        "type": "tool_calls",
                        "data": [{"id": "call-state", "name": "read_file", "arguments": {"path": "README.md"}}],
                    }
                    yield {"type": "response_info", "data": {"finish_reason": "stop"}}
                    return
                yield {"type": "content", "data": "done"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        runtime = AgentRuntime(
            app_id="405",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="405", session_id="session-5", turn_id="turn-5", user_input="读取 README")

        async def exercise() -> None:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=SingleToolLLMClient()):
                async for _event in runtime.run_turn(context):
                    pass

        asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertEqual(context.plan_state, "[>] Review runtime alignment")
        self.assertEqual(context.tool_iteration_count, 1)
        self.assertEqual(context.last_tool_result["tool_name"], "read_file")
        self.assertEqual(context.last_llm_usage["completion_tokens"], 1)
        self.assertEqual(context.metrics[0]["tool_name"], "read_file")
        self.assertTrue(context.runtime_flags["session_initialized"])


class AgentConfigGuardTest(unittest.TestCase):
    def test_default_config_can_be_instantiated(self) -> None:
        from bot.utils.config import Config

        config = Config()

        self.assertEqual(config.agents, [])
        self.assertTrue(config.memory.search.enabled)
        self.assertTrue(config.memory.store.enabled)


class ContextCompactionGuardTest(unittest.TestCase):
    def test_finalize_turn_skips_compaction_for_small_history(self) -> None:
        from bot.agent.context_compaction import ContextCompactor
        from bot.agent.runtime import TurnContext

        compactor = ContextCompactor()
        context = TurnContext(
            app_id="501",
            session_id="session-1",
            turn_id="turn-1",
            user_input="生成一个简单页面",
            history=[
                {"role": "user", "content": "生成一个简单页面"},
                {"role": "assistant", "content": "已生成完成"},
            ],
            metadata={"turn_summary": "完成简单页面生成"},
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "bot.agent.context_compaction.get_bot_context_dir",
            return_value=Path(temp_dir),
        ):
            asyncio.run(compactor.finalize_turn(context))
            transcripts_dir = Path(temp_dir) / "transcripts"

        self.assertEqual(len(context.history), 2)
        self.assertFalse(transcripts_dir.exists())

    def test_write_transcript_reuses_identical_history_snapshot(self) -> None:
        from bot.agent.context_compaction import ContextCompactor
        from bot.agent.runtime import TurnContext

        compactor = ContextCompactor()
        context = TurnContext(
            app_id="502",
            session_id="session-2",
            turn_id="turn-2",
            user_input="检查 transcript 去重",
            history=[{"role": "user", "content": "检查 transcript 去重"}],
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "bot.agent.context_compaction.get_bot_context_dir",
            return_value=Path(temp_dir),
        ):
            first = compactor._write_transcript(context)
            second = compactor._write_transcript(context)
            transcript_files = list((Path(temp_dir) / "transcripts").glob("*.jsonl"))

        self.assertEqual(first, second)
        self.assertEqual(len(transcript_files), 1)

    def test_write_transcript_persists_context_snapshot(self) -> None:
        from bot.agent.context_compaction import ContextCompactor
        from bot.agent.runtime import TurnContext

        compactor = ContextCompactor()
        context = TurnContext(
            app_id="503",
            session_id="session-3",
            turn_id="turn-3",
            user_input="保留完整上下文",
            history=[{"role": "user", "content": "保留完整上下文"}],
            metadata={"trace_id": "trace-3", "turn_summary": "完成摘要"},
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "bot.agent.context_compaction.get_bot_context_dir",
            return_value=Path(temp_dir),
        ):
            transcript_path = compactor._write_transcript(context)
            payload = transcript_path.read_text(encoding="utf-8")

        self.assertIn('"session_id": "session-3"', payload)
        self.assertIn('"trace_id": "trace-3"', payload)
        self.assertIn('"history"', payload)

    def test_post_tool_use_stores_history_override_for_structured_result(self) -> None:
        from bot.agent.hook.handlers import post_tool_use
        from bot.agent.runtime import TurnContext

        context = TurnContext(app_id="504", session_id="session-4", turn_id="turn-4", user_input="读取文件")
        tool_call = {"id": "read-1", "name": "read_file", "arguments": {"path": "index.html"}}
        large_result = {"content": "x" * 6000}

        asyncio.run(post_tool_use(context, tool_call=tool_call, result=large_result))

        self.assertIn("tool_result_overrides", context.metadata)
        self.assertEqual(context.metadata["tool_result_overrides"]["read-1"], "x" * 6000)
        self.assertIn("tool_result_previews", context.metadata)
        self.assertIn("[TRUNCATED FOR PREVIEW]", context.metadata["tool_result_previews"]["read-1"])


class ContextAssemblerTest(unittest.TestCase):
    def test_assemble_builds_prompt_pipeline_and_reminder(self) -> None:
        from bot.agent.context import ContextAssembler, DYNAMIC_BOUNDARY
        from bot.agent.runtime import TurnContext

        class FakeMemoryManager:
            async def search_for_prompt(self, _query, limit=5):
                return {"text": "Memory snippet", "count": 1}

        class FakePlannerState:
            rounds_since_update = 3

        class FakePlanner:
            plan_reminder_interval = 3
            state = FakePlannerState()

            def get_state(self):
                return "[>] Build prompt pipeline"

        assembler = ContextAssembler()
        assembler.memory_manager = FakeMemoryManager()
        assembler.planner = FakePlanner()
        assembler.tool_catalog = [{"name": "read_file", "description": "Read a file", "parameters": ["path"]}]
        assembler.prompt_builder = assembler.prompt_builder.__class__(assembler.tool_catalog)

        context = TurnContext(
            app_id="601",
            session_id="session-601",
            turn_id="turn-601",
            user_input="继续完善 prompt 组装",
            history=[{"role": "user", "content": "继续完善 prompt 组装"}],
            metadata={
                "skill_catalog": [{"name": "web-search", "description": "Search the web when needed"}],
                "static_memory_context": "Static memory block",
                "requested_code_gen_type": "html",
                "turn_count": 4,
            },
        )

        messages = asyncio.run(assembler.assemble(context))

        self.assertGreaterEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(DYNAMIC_BOUNDARY, messages[0]["content"])
        self.assertIn("# Available tools", messages[0]["content"])
        self.assertIn("read_file(path)", messages[0]["content"])
        self.assertIn("# Available skills", messages[0]["content"])
        self.assertIn("# Memory", messages[0]["content"])
        self.assertIn("Session plan state:", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("<system-reminder>", messages[1]["content"])
        self.assertIn("Refresh the current session plan", messages[1]["content"])

    def test_normalize_messages_merges_adjacent_plain_messages(self) -> None:
        from bot.agent.context import ContextAssembler

        assembler = ContextAssembler()
        messages = assembler.normalize_messages(
            [
                {"role": "user", "content": "first"},
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "reply-1"},
                {"role": "assistant", "content": "reply-2"},
                {"role": "user", "content": "<system-reminder>\nkeep plan fresh\n</system-reminder>"},
                {"role": "user", "content": "third"},
            ]
        )

        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[0]["content"], "first\n\nsecond")
        self.assertEqual(messages[1]["content"], "reply-1\n\nreply-2")
        self.assertTrue(messages[2]["content"].startswith("<system-reminder>"))
        self.assertEqual(messages[3]["content"], "third")

    def test_normalize_messages_synthesizes_missing_tool_results(self) -> None:
        from bot.agent.context import ContextAssembler

        assembler = ContextAssembler()
        messages = assembler.normalize_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-missing",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
                        }
                    ],
                    "internal_only": True,
                },
                {"role": "user", "content": "继续"},
            ]
        )

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[1]["role"], "tool")
        self.assertEqual(messages[1]["tool_call_id"], "call-missing")
        self.assertIn("Missing tool result for read_file", messages[1]["content"])
        self.assertNotIn("internal_only", messages[0])
        self.assertEqual(messages[2]["content"], "继续")


class ToolCapabilityTest(unittest.TestCase):
    def test_tools_handler_registers_todo_load_skill_and_task(self) -> None:
        from bot.agent.tool_handler import ToolsHandler

        handler = ToolsHandler()
        tool_names = {tool.name for tool in handler.tools}

        self.assertIn("todo", tool_names)
        self.assertIn("load_skill", tool_names)
        self.assertIn("task", tool_names)

    def test_todo_tool_updates_plan_state_via_tool_executor(self) -> None:
        from bot.agent.plan.planner import Planner
        from bot.agent.runtime import TurnContext
        from bot.agent.tool_executor import ToolExecutor
        from bot.agent.tool_handler import ToolsHandler

        planner = Planner()
        context = TurnContext(
            app_id="701",
            session_id="session-701",
            turn_id="turn-701",
            user_input="更新计划",
            plan_state=planner.get_state(),
            metadata={"planner": planner},
        )
        executor = ToolExecutor(ToolsHandler())

        result = asyncio.run(
            executor.execute(
                {
                    "name": "todo",
                    "arguments": {
                        "items": [
                            {"content": "Inspect prompt pipeline", "status": "completed"},
                            {"content": "Implement todo tool", "status": "in_progress", "activeForm": "Implementing todo tool"},
                        ]
                    },
                },
                context,
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("[>] Implement todo tool", result["data"])
        self.assertIn("[>] Implement todo tool", context.plan_state)

    def test_load_skill_tool_returns_skill_body(self) -> None:
        from bot.agent.runtime import TurnContext
        from bot.agent.tool_executor import ToolExecutor
        from bot.agent.tool_handler import ToolsHandler

        context = TurnContext(app_id="702", session_id="session-702", turn_id="turn-702", user_input="加载技能")
        executor = ToolExecutor(ToolsHandler())

        result = asyncio.run(
            executor.execute(
                {"name": "load_skill", "arguments": {"name": "web-search"}},
                context,
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("# Web Search", result["data"])

    def test_compact_tool_uses_runtime_context_compactor(self) -> None:
        from bot.agent.runtime import TurnContext
        from bot.agent.tool_executor import ToolExecutor
        from bot.agent.tool_handler import ToolsHandler

        class FakeContextCompactor:
            async def compact_history(self, context, focus=None, reason="threshold"):
                context.history = [{"role": "user", "content": "compacted continuation"}]
                context.metadata["context_compaction"] = {
                    "has_compacted": True,
                    "last_summary": "compacted continuation",
                    "recent_files": [],
                    "events": [{"reason": reason, "focus": focus}],
                    "last_transcript_hash": "hash-1",
                    "last_transcript_path": "D:/tmp/transcript.jsonl",
                }

        context = TurnContext(
            app_id="703",
            session_id="session-703",
            turn_id="turn-703",
            user_input="压缩上下文",
            history=[{"role": "user", "content": "压缩上下文"}, {"role": "assistant", "content": "long response"}],
            metadata={"context_compactor": FakeContextCompactor()},
        )
        executor = ToolExecutor(ToolsHandler())

        result = asyncio.run(
            executor.execute(
                {"name": "compact", "arguments": {"focus": "Preserve latest task"}},
                context,
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"], "Context compacted for continued work.")
        self.assertEqual(context.history, [{"role": "user", "content": "compacted continuation"}])
        self.assertEqual(result["details"]["summary"], "compacted continuation")


class SubagentToolTest(unittest.TestCase):
    def test_subagent_context_filters_child_tools(self) -> None:
        from bot.agent.subagent_runner import SubagentContext
        from bot.agent.tool_handler import ToolsHandler

        handler = ToolsHandler()

        unrestricted = SubagentContext(prompt="inspect")
        unrestricted_names = {tool.name for tool in unrestricted.get_tools(handler)}
        self.assertNotIn("task", unrestricted_names)
        self.assertNotIn("compact", unrestricted_names)

        restricted = SubagentContext(prompt="inspect", allowed_tools=["read_file"])
        restricted_names = [tool.name for tool in restricted.get_tools(handler)]
        self.assertEqual(restricted_names, ["read_file"])

    def test_task_tool_runs_isolated_subagent(self) -> None:
        from bot.agent.runtime import TurnContext
        from bot.agent.tool_executor import ToolExecutor
        from bot.agent.tool_handler import ToolsHandler

        captured_messages: list[dict[str, object]] = []
        captured_tool_names: list[str] = []

        class RecordingLLMClient:
            async def invoke_stream(self, messages, tools):
                captured_messages.extend(messages)
                captured_tool_names.extend(tool["function"]["name"] for tool in tools)
                yield {"type": "content", "data": "subagent summary"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        context = TurnContext(
            app_id="705",
            session_id="session-705",
            turn_id="turn-705",
            user_input="父任务",
            plan_state="[>] Investigate runtime task delegation",
            history=[
                {"role": "user", "content": "父任务"},
                {"role": "assistant", "content": "parent private history"},
            ],
            metadata={
                "trace_id": "trace-705",
            },
        )
        executor = ToolExecutor(ToolsHandler())

        async def exercise():
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=RecordingLLMClient()):
                return await executor.execute(
                    {
                        "name": "task",
                        "arguments": {
                            "prompt": "Inspect the runtime wiring and summarize findings.",
                            "description": "inspect runtime",
                            "max_turns": 4,
                            "allowed_tools": ["read_file", "load_skill"],
                        },
                    },
                    context,
                )

        result = asyncio.run(exercise())

        self.assertTrue(result["success"])
        self.assertEqual(result["data"], "subagent summary")
        self.assertEqual(set(captured_tool_names), {"read_file", "load_skill"})
        serialized_messages = json.dumps(captured_messages, ensure_ascii=False)
        self.assertNotIn("parent private history", serialized_messages)
        self.assertIn("Inspect the runtime wiring and summarize findings.", serialized_messages)
        self.assertIn("Investigate runtime task delegation", serialized_messages)

class HookControlTest(unittest.TestCase):
    def test_runtime_stops_turn_when_pre_llm_hook_blocks(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, event_name, context, **kwargs):
                if event_name == "PreLLMCall":
                    return {
                        "hook_control": {
                            "action": "block",
                            "reason": "preflight check failed",
                            "message": "Turn blocked by hook policy",
                            "state": "stopped",
                        }
                    }
                return {}

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            async def compact_history(self, context, focus=None, reason="threshold"):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(tools=[])

        class FailIfCalledLLMClient:
            async def invoke_stream(self, messages, tools):
                raise AssertionError("LLM should not be called when hook_control blocks the turn")

        runtime = AgentRuntime(
            app_id="803",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="803", session_id="session-803", turn_id="turn-803", user_input="先做预检")

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=FailIfCalledLLMClient()):
                events = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.STOPPED)
        self.assertTrue(any(event_type == "HookControl" for event_type, _ in events))
        self.assertFalse(any(event_type == "LLM_Thinking_Start" for event_type, _ in events))
        self.assertEqual(context.history[-1]["content"], "Turn blocked by hook policy")

    def test_runtime_injects_pre_llm_hook_reminder_into_messages(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, event_name, context, **kwargs):
                if event_name == "PreLLMCall":
                    return {
                        "hook_control": {
                            "action": "inject",
                            "reason": "preflight reminder",
                            "message": "Check pending migrations before coding.",
                        }
                    }
                return {}

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            async def compact_history(self, context, focus=None, reason="threshold"):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(tools=[])

        captured_messages: list[dict[str, object]] = []

        class RecordingLLMClient:
            async def invoke_stream(self, messages, tools):
                captured_messages.extend(messages)
                yield {"type": "content", "data": "ok"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        runtime = AgentRuntime(
            app_id="804",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="804", session_id="session-804", turn_id="turn-804", user_input="继续")

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=RecordingLLMClient()):
                events = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertTrue(any(event_type == "HookControl" for event_type, _ in events))
        self.assertTrue(any(message.get("content") == "<system-reminder>\nCheck pending migrations before coding.\n</system-reminder>" for message in captured_messages))
        self.assertEqual(context.history[-1]["content"], "ok")

    def test_runtime_overrides_pre_llm_messages(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, event_name, context, **kwargs):
                if event_name == "PreLLMCall":
                    return {
                        "hook_control": {
                            "action": "override",
                            "reason": "replace prompt payload",
                            "data": {
                                "messages": [
                                    {"role": "system", "content": "Overridden system prompt"},
                                    {"role": "user", "content": "Use the replaced message set"},
                                ]
                            },
                        }
                    }
                return {}

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": "original message"}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            async def compact_history(self, context, focus=None, reason="threshold"):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class FakeToolExecutor:
            def __init__(self) -> None:
                self.tools_handler = SimpleNamespace(tools=[])

        captured_messages: list[dict[str, object]] = []

        class RecordingLLMClient:
            async def invoke_stream(self, messages, tools):
                captured_messages.extend(messages)
                yield {"type": "content", "data": "override ok"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        runtime = AgentRuntime(
            app_id="805",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=FakeToolExecutor(),
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="805", session_id="session-805", turn_id="turn-805", user_input="继续")

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=RecordingLLMClient()):
                events = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertTrue(any(event_type == "HookControl" for event_type, _ in events))
        self.assertEqual(captured_messages[0]["content"], "Overridden system prompt")
        self.assertEqual(captured_messages[1]["content"], "Use the replaced message set")
        self.assertEqual(context.history[-1]["content"], "override ok")

    def test_runtime_overrides_pre_tool_call_before_execution(self) -> None:
        from bot.agent.runtime import AgentRuntime, AgentState, TurnContext

        class FakeHookRunner:
            async def dispatch(self, event_name, context, **kwargs):
                if event_name == "PreToolUse":
                    return {
                        "hook_control": {
                            "action": "override",
                            "reason": "rewrite tool call",
                            "data": {
                                "tool_call": {
                                    "name": "read_file",
                                    "arguments": {"path": "README.md"},
                                }
                            },
                        },
                        "permission_decision": {"behavior": "allow", "reason": "override allowed"},
                    }
                return {}

        class FakeContextAssembler:
            async def assemble(self, context):
                return [{"role": "user", "content": context.user_input}]

        class FakeContextCompactor:
            async def prepare_for_llm(self, context):
                return None

            async def finalize_turn(self, context):
                return None

            async def compact_history(self, context, focus=None, reason="threshold"):
                return None

            def persist_large_output(self, context, tool_call_id, history_content):
                return history_content

        class FakeTurnReducer:
            async def reduce(self, context):
                return None

        class RecordingToolExecutor:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []
                self.tools_handler = SimpleNamespace(
                    tools=[SimpleNamespace(name="write_file", description="write", parameters={}), SimpleNamespace(name="read_file", description="read", parameters={})]
                )

            async def execute(self, tool_call, context):
                self.calls.append({"name": tool_call.get("name"), "arguments": dict(tool_call.get("arguments", {}))})
                return {"content": f"executed {tool_call.get('name')}"}

        class SingleToolLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke_stream(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    yield {
                        "type": "tool_calls",
                        "data": [{"id": "call-override", "name": "write_file", "arguments": {"path": "danger.txt", "content": "x"}}],
                    }
                    yield {"type": "response_info", "data": {"finish_reason": "stop"}}
                    return

                yield {"type": "content", "data": "tool rewrite completed"}
                yield {"type": "response_info", "data": {"finish_reason": "stop"}}

        fake_executor = RecordingToolExecutor()
        runtime = AgentRuntime(
            app_id="806",
            hook_runner=FakeHookRunner(),
            context_assembler=FakeContextAssembler(),
            tool_executor=fake_executor,
            turn_reducer=FakeTurnReducer(),
        )
        runtime.context_compactor = FakeContextCompactor()

        context = TurnContext(app_id="806", session_id="session-806", turn_id="turn-806", user_input="改写工具")

        async def exercise() -> list[tuple[str, object]]:
            with patch("bot.llm.async_client.AsyncLLMClient", return_value=SingleToolLLMClient()):
                events = []
                async for event in runtime.run_turn(context):
                    events.append((event.event_type, event.data))
                return events

        events = asyncio.run(exercise())

        self.assertEqual(context.state, AgentState.COMPLETED)
        self.assertTrue(any(event_type == "HookControl" for event_type, _ in events))
        self.assertEqual(fake_executor.calls, [{"name": "read_file", "arguments": {"path": "README.md"}}])
        self.assertEqual(context.history[-1]["content"], "tool rewrite completed")


if __name__ == "__main__":
    unittest.main()