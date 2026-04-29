from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncGenerator
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_local_constant_module() -> None:
    existing = sys.modules.get("constant")
    expected_path = AI_SERVICE_ROOT / "constant.py"
    if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == expected_path.resolve():
        return

    spec = importlib.util.spec_from_file_location("constant", expected_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load ai-service constant module from {expected_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["constant"] = module
    spec.loader.exec_module(module)


_ensure_local_constant_module()

from bot.agent.runtime import AgentEvent, AgentRuntime, AgentState, TurnContext
from shared.config.log_config import log


class AgentAdapterService:
    def __init__(self) -> None:
        self._runtimes: dict[str, AgentRuntime] = {}
        self._contexts: dict[str, TurnContext] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _app_key(self, app_id: int) -> str:
        return str(app_id)

    def _context_key(self, app_id: int, session_id: str) -> str:
        return f"{self._app_key(app_id)}:{session_id}"

    def _get_lock(self, context_key: str) -> asyncio.Lock:
        lock = self._locks.get(context_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[context_key] = lock
        return lock

    def _get_runtime(self, app_key: str) -> AgentRuntime:
        runtime = self._runtimes.get(app_key)
        if runtime is None:
            runtime = AgentRuntime(app_id=app_key)
            self._runtimes[app_key] = runtime
        return runtime

    def _get_context(self, app_id: int, session_id: str) -> TurnContext:
        context_key = self._context_key(app_id, session_id)
        context = self._contexts.get(context_key)
        if context is None:
            context = TurnContext(
                app_id=self._app_key(app_id),
                session_id=session_id,
                turn_id="",
                user_input="",
            )
            self._contexts[context_key] = context
        return context

    def _prepare_context(
        self,
        *,
        app_id: int,
        session_id: str,
        user_message: str,
        trace_id: str,
        request_id: str,
        requested_code_gen_type: str | None,
    ) -> tuple[AgentRuntime, TurnContext, asyncio.Lock]:
        app_key = self._app_key(app_id)
        context_key = self._context_key(app_id, session_id)
        runtime = self._get_runtime(app_key)
        context = self._get_context(app_id, session_id)
        lock = self._get_lock(context_key)

        context.app_id = app_key
        context.session_id = session_id
        context.turn_id = request_id
        context.user_input = user_message
        context.state = AgentState.IDLE
        context.metadata["trace_id"] = trace_id
        context.metadata["request_id"] = request_id
        if requested_code_gen_type:
            context.metadata["requested_code_gen_type"] = requested_code_gen_type
        else:
            context.metadata.pop("requested_code_gen_type", None)

        return runtime, context, lock

    async def stream_events(
        self,
        *,
        app_id: int,
        session_id: str,
        user_message: str,
        trace_id: str,
        request_id: str,
        requested_code_gen_type: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        runtime, context, lock = self._prepare_context(
            app_id=app_id,
            session_id=session_id,
            user_message=user_message,
            trace_id=trace_id,
            request_id=request_id,
            requested_code_gen_type=requested_code_gen_type,
        )

        async with lock:
            async for event in runtime.run_turn(context):
                yield event

    async def stream_message(
        self,
        *,
        app_id: int,
        session_id: str,
        user_message: str,
        trace_id: str,
        request_id: str,
        requested_code_gen_type: str | None = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self.stream_events(
            app_id=app_id,
            session_id=session_id,
            user_message=user_message,
            trace_id=trace_id,
            request_id=request_id,
            requested_code_gen_type=requested_code_gen_type,
        ):
            if event.event_type == "LLM_Response_Chunk" and event.data:
                yield str(event.data)
                continue
            if event.event_type == "Error":
                message = str(event.data or "agent execution failed")
                raise RuntimeError(message)

    def get_session_id(self, app_id: int, session_id: str) -> str:
        return self._get_context(app_id, session_id).session_id