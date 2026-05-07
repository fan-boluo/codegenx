from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from bot.agent.context import ContextAssembler, TurnReducer
from bot.agent.hook.registry import register_all_hooks
from bot.agent.hook.runner import HookRunner
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import get_tool_registry
from bot.bus import MessageBus, RuntimeTurnEvent, RuntimeTurnRequest
from bot.memory.memory_manager import get_memory_manager
from bot.session.manager import SessionManager
from bot.skill.skill_loader import SkillLoader
from bot.utils.config import AgentConfig, load_config
from infra.mysql.session import warm_up_mysql_pool
from infra.qdrant.client import warm_up_qdrant_client
from monitor.alert_evaluator import get_monitor_alert_evaluator
from monitor.health_checker import get_health_checker
from monitor.maintenance_service import get_monitor_maintenance_service
from monitor.monitor_pipeline import get_monitor_pipeline
from monitor.monitor_query_service import get_monitor_query_service
from monitor.monitor_store import get_monitor_store
from shared.config.log_config import log
from shared.constants import get_bot_code_dir


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class TurnContext(BaseModel):
    build_tool: list[dict[str, Any]] = Field(default_factory=list)
    skill: list[dict[str, str]] = Field(default_factory=list)
    memory: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    user_input: str = ""


class AgentEvent(BaseModel):
    event_type: str
    data: Any = None
    state: AgentState


class TurnStoppedError(Exception):
    pass


@dataclass
class RuntimeTurnState:
    request: RuntimeTurnRequest
    turn_number: int
    context: TurnContext
    code_dir: str = ""
    safe_paths: list[str] = field(default_factory=list)
    workspace_metadata: dict[str, Any] = field(default_factory=dict)
    knowledge_cache: dict[str, Any] = field(default_factory=dict)
    prompt_template: str = ""
    plan_summary: str = ""
    snapshot_path: str = ""
    state: AgentState = AgentState.IDLE
    llm_usage: dict[str, Any] = field(default_factory=dict)
    tool_metrics: list[dict[str, Any]] = field(default_factory=list)
    memory_metrics: dict[str, Any] = field(default_factory=dict)
    recovery_state: dict[str, Any] = field(default_factory=dict)
    transition_reason: str = ""
    current_prompt_tokens: int = 0
    projected_total_tokens: int = 0
    error_text: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    active_span_refs: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeSessionState:
    app_id: str
    user_id: str
    session_id: str
    trace_id: str
    request_id: str
    requested_code_gen_type: str | None
    client_version: str
    session_manager: SessionManager
    runtime: AgentRuntime
    turn_counter: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tool_calls: int = 0
    total_memory_hits: int = 0
    sum_llm_latency_ms: int = 0
    sum_first_token_ms: int = 0
    max_llm_latency_ms: int = 0
    min_llm_latency_ms: int = 999999
    total_errors: int = 0
    last_recovery_kind: str = ""
    recovery_count: int = 0
    started_at: float = 0.0
    last_activity_at: float = 0.0
    state: AgentState = AgentState.IDLE
    session_span: Any = None
    audit_context: dict[str, Any] = field(default_factory=dict)
    active_turns: dict[str, RuntimeTurnState] = field(default_factory=dict)
    active_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    worker_task: asyncio.Task | None = None
    queue: asyncio.Queue[RuntimeTurnRequest] = field(default_factory=asyncio.Queue)
    stop_signal: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str = ""
    closed: bool = False

    def touch(self) -> None:
        self.last_activity_at = time.time()


class AgentRuntime:
    CONTINUATION_MESSAGE = (
        "Output limit hit. Continue directly from where you stopped. "
        "Do not restart, recap, or repeat prior content."
    )
    EXCLUDED_TOOL_NAMES = {"compact"}

    def __init__(
        self,
        hook_runner: HookRunner | None = None,
        context_assembler: ContextAssembler | None = None,
        tool_executor: ToolExecutor | None = None,
        turn_reducer: TurnReducer | None = None,
        message_bus: MessageBus | None = None,
    ):
        self.agent_config = self._resolve_agent_config()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = 3
        self.stop_grace_seconds = max(0.0, float(self.agent_config.session_stop_grace_seconds or 2.0))
        self.message_bus = message_bus or MessageBus()
        self.context_assembler = context_assembler or ContextAssembler()
        self.turn_reducer = turn_reducer or TurnReducer()
        self.tool_executor = tool_executor or ToolExecutor(get_tool_registry(), safe_paths=[str(get_bot_code_dir("main"))])
        self._tool_catalog = [
            tool for tool in self.tool_executor.tools_handler.tools if tool.name not in self.EXCLUDED_TOOL_NAMES
        ]
        self.hook_runner = hook_runner or HookRunner()
        if hook_runner is None:
            register_all_hooks(self.hook_runner)
        self._dispatcher_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._session_states: dict[str, RuntimeSessionState] = {}
        self._session_lock = asyncio.Lock()

    def _resolve_agent_config(self) -> AgentConfig:
        try:
            return load_config().get_default_agent()
        except Exception as exc:
            log.warning("Failed to load runtime config, using defaults: {}", exc)
            return AgentConfig()

    async def start(self) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._shutdown_event.clear()
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop(), name="agent-runtime-dispatcher")

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None

        async with self._session_lock:
            sessions = list(self._session_states.values())
            self._session_states.clear()

        for session_state in sessions:
            if session_state.worker_task is not None:
                session_state.worker_task.cancel()
                try:
                    await session_state.worker_task
                except asyncio.CancelledError:
                    pass

    async def submit_turn(self, request: RuntimeTurnRequest) -> AsyncGenerator[AgentEvent, None]:
        await self.start()
        subscriber = self.message_bus.subscribe_turn(request.turn_id)
        try:
            await self.message_bus.publish_inbound(request)
            while True:
                item = await subscriber.get()
                if not isinstance(item, RuntimeTurnEvent):
                    continue
                event = AgentEvent(event_type=item.event_type, data=item.data, state=AgentState(item.state))
                yield event
                if event.event_type in {"TurnCompleted", "TurnStopped", "Error"}:
                    await self._wait_for_turn_cleanup(request.session_id, request.turn_id)
                    break
        finally:
            self.message_bus.unsubscribe_turn(request.turn_id, subscriber)

    async def stop_session(
        self,
        *,
        session_id: str,
        reason: str = "user-stop",
        grace_seconds: float | None = None,
    ) -> dict[str, Any]:
        session_state: RuntimeSessionState | None = None
        active_tasks: list[tuple[str, asyncio.Task[Any]]] = []
        dropped_requests: list[RuntimeTurnRequest] = []
        stop_reason = str(reason or "user-stop")

        async with self._session_lock:
            session_state = self._session_states.get(session_id)
            if session_state is None or session_state.closed:
                return {
                    "accepted": False,
                    "sessionId": session_id,
                    "stoppedTurnCount": 0,
                    "droppedTurnCount": 0,
                    "activeTurnIds": [],
                    "droppedTurnIds": [],
                }

            session_state.stop_signal.set()
            session_state.stop_reason = stop_reason
            session_state.touch()
            active_tasks = list(session_state.active_tasks.items())
            while True:
                try:
                    queued_request = session_state.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(queued_request, RuntimeTurnRequest):
                    dropped_requests.append(queued_request)

        for queued_request in dropped_requests:
            await self._publish_stopped_request(queued_request, reason=stop_reason)

        timeout_seconds = self.stop_grace_seconds if grace_seconds is None else max(0.0, float(grace_seconds))
        if active_tasks:
            done, pending = await asyncio.wait([task for _, task in active_tasks], timeout=timeout_seconds)
            if pending:
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        async with self._session_lock:
            current_state = self._session_states.get(session_id)
            if current_state is not None and not current_state.closed and not current_state.active_tasks:
                current_state.stop_signal.clear()
                current_state.stop_reason = ""

        return {
            "accepted": bool(active_tasks or dropped_requests),
            "sessionId": session_id,
            "stoppedTurnCount": len(active_tasks),
            "droppedTurnCount": len(dropped_requests),
            "activeTurnIds": [turn_id for turn_id, _ in active_tasks],
            "droppedTurnIds": [request.turn_id for request in dropped_requests],
        }

    async def _dispatch_loop(self) -> None:
        while not self._shutdown_event.is_set():
            request = await self.message_bus.consume_inbound()
            if not isinstance(request, RuntimeTurnRequest):
                continue
            session_state = await self._get_or_create_session_state(request)
            await session_state.queue.put(request)

    async def _get_or_create_session_state(self, request: RuntimeTurnRequest) -> RuntimeSessionState:
        async with self._session_lock:
            session_state = self._session_states.get(request.session_id)
            if session_state is not None and not session_state.closed:
                session_state.user_id = request.user_id
                session_state.trace_id = request.trace_id
                session_state.request_id = request.request_id
                session_state.requested_code_gen_type = request.requested_code_gen_type
                session_state.client_version = request.client_version
                session_state.touch()
                return session_state

            app_id = str(request.app_id or "main")
            session_manager = SessionManager(app_id)
            session_state = RuntimeSessionState(
                app_id=app_id,
                user_id=str(request.user_id or "").strip(),
                session_id=request.session_id,
                trace_id=request.trace_id,
                request_id=request.request_id,
                requested_code_gen_type=request.requested_code_gen_type,
                client_version=request.client_version,
                session_manager=session_manager,
                runtime=self,
                started_at=time.time(),
                last_activity_at=time.time(),
            )
            session_state.worker_task = asyncio.create_task(
                self._session_worker(session_state),
                name=f"agent-session-{request.session_id}",
            )
            self._session_states[request.session_id] = session_state
            return session_state

    async def _wait_for_turn_cleanup(self, session_id: str, turn_id: str) -> None:
        while True:
            async with self._session_lock:
                session_state = self._session_states.get(session_id)
                if session_state is None or turn_id not in session_state.active_tasks:
                    return
            await asyncio.sleep(0)

    async def _session_worker(self, session_state: RuntimeSessionState) -> None:
        session_state.state = AgentState.RUNNING
        while not self._shutdown_event.is_set() and not session_state.closed:
            try:
                request = await asyncio.wait_for(
                    session_state.queue.get(),
                    timeout=max(1, int(self.agent_config.session_worker_idle_seconds or 1800)),
                )
            except asyncio.TimeoutError:
                await self._close_session_state(session_state, end_reason="idle-timeout")
                return
            except asyncio.CancelledError:
                await self._close_session_state(session_state, end_reason="runtime-stop")
                raise

            turn_state = await self._build_turn_state(session_state, request)
            session_state.active_turns[request.turn_id] = turn_state
            turn_task = asyncio.create_task(
                self._run_turn_task(session_state, turn_state),
                name=f"agent-turn-{request.turn_id}",
            )
            session_state.active_tasks[request.turn_id] = turn_task
            try:
                await turn_task
            except Exception as exc:
                log.exception("Turn crashed for session {} turn {}", session_state.session_id, request.turn_id)
                await self._finalize_worker_turn_error(session_state, turn_state, exc)
            except asyncio.CancelledError:
                turn_task.cancel()
                with suppress(asyncio.CancelledError):
                    await turn_task
                await self._finalize_cancelled_turn(session_state, turn_state, reason="runtime-stop")
                await self._close_session_state(session_state, end_reason="runtime-stop")
                raise
            finally:
                session_state.active_tasks.pop(request.turn_id, None)
                if not session_state.closed:
                    session_state.stop_signal.clear()
                    session_state.stop_reason = ""
                session_state.active_turns.pop(request.turn_id, None)
                session_state.touch()
                session_state.session_manager.save_history(session_state.session_id, turn_state.context.chat_history)

    async def _close_session_state(self, session_state: RuntimeSessionState, *, end_reason: str) -> None:
        if session_state.closed:
            return
        session_state.closed = True
        if end_reason == "runtime-stop" and session_state.active_turns:
            session_state.state = AgentState.STOPPED
        elif session_state.total_errors > 0:
            session_state.state = AgentState.FAILED
        else:
            session_state.state = AgentState.COMPLETED
        if session_state.worker_task is not None and session_state.worker_task.cancelled():
            session_state.worker_task = None
        await self.hook_runner.dispatch("OnSessionEnd", session_state, end_reason=end_reason)
        async with self._session_lock:
            self._session_states.pop(session_state.session_id, None)

    async def _build_turn_state(self, session_state: RuntimeSessionState, request: RuntimeTurnRequest) -> RuntimeTurnState:
        session_state.turn_counter += 1
        turn_context = TurnContext(
            user_input=request.user_input,
        )
        return RuntimeTurnState(
            request=request,
            turn_number=session_state.turn_counter,
            context=turn_context,
            state=AgentState.IDLE,
        )

    async def _run_turn_task(self, session_state: RuntimeSessionState, turn_state: RuntimeTurnState) -> None:

        async for _ in self._execute_turn(session_state, turn_state):
            pass

    async def _execute_turn(
        self,
        session_state: RuntimeSessionState,
        turn_state: RuntimeTurnState,
    ) -> AsyncGenerator[AgentEvent, None]:
        await self.hook_runner.dispatch("OnSessionStart", session_state, turn=turn_state)
        request = turn_state.request
        turn_state.state = AgentState.RUNNING
        turn_state.started_at = time.time()

        try:
            self._raise_if_stop_requested(session_state)

            await self.hook_runner.dispatch("OnTurnStart", turn_state, session=session_state)
            on_turn_start = AgentEvent(event_type="OnTurnStart", state=AgentState.RUNNING)
            await self._publish_runtime_event(session_state, turn_state, on_turn_start)
            yield on_turn_start

            tool_iterations = 0
            last_tool_signature: str | None = None
            consecutive_same_tool_calls = 0
            llm_response = {"content": "", "tool_calls": [], "finish_reason": None}

            self.context_assembler.ensure_user_message(turn_state.context)
            while turn_state.state == AgentState.RUNNING:
                self._raise_if_stop_requested(session_state)
                messages = await self.context_assembler.assemble(turn_state.context)
                turn_state.current_prompt_tokens = self._estimate_message_tokens(messages)
                turn_state.projected_total_tokens = session_state.total_prompt_tokens + session_state.total_completion_tokens + turn_state.current_prompt_tokens
                await self.hook_runner.dispatch(
                    "PreLLMCall",
                    turn_state,
                    session=session_state,
                    messages=messages,
                    prompt_tokens=turn_state.current_prompt_tokens,
                    projected_total_tokens=turn_state.projected_total_tokens,
                    tool_catalog=turn_state.context.build_tool,
                )
                await self._publish_runtime_event(
                    session_state,
                    turn_state,
                    AgentEvent(
                        event_type="LLM_Thinking_Start",
                        data={"prompt_tokens": turn_state.current_prompt_tokens, "message_count": len(messages)},
                        state=AgentState.RUNNING,
                    ),
                )

                from bot.llm.async_client import AsyncLLMClient

                llm_client = AsyncLLMClient()
                llm_response = {"content": "", "tool_calls": [], "finish_reason": None}
                async for chunk in llm_client.invoke_stream(messages, turn_state.context.build_tool):
                    self._raise_if_stop_requested(session_state)
                    if chunk["type"] == "content":
                        llm_response["content"] += chunk["data"]
                        event = AgentEvent(event_type="LLM_Response_Chunk", data=chunk["data"], state=AgentState.RUNNING)
                        await self._publish_runtime_event(session_state, turn_state, event)
                        yield event
                    elif chunk["type"] == "tool_calls":
                        llm_response["tool_calls"] = chunk["data"]
                    elif chunk["type"] == "response_info":
                        llm_response["finish_reason"] = (chunk.get("data") or {}).get("finish_reason")

                completion_tokens = self._estimate_completion_tokens(llm_response)
                usage = {
                    "prompt_tokens": turn_state.current_prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": turn_state.current_prompt_tokens + completion_tokens,
                }
                turn_state.llm_usage = usage
                session_state.total_prompt_tokens += usage["prompt_tokens"]
                session_state.total_completion_tokens += usage["completion_tokens"]
                await self.hook_runner.dispatch(
                    "PostLLMCall",
                    turn_state,
                    session=session_state,
                    response=llm_response,
                    usage=usage,
                    messages=messages,
                )

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": llm_response.get("content", ""),
                }
                tool_calls = llm_response.get("tool_calls", []) or []
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ]
                turn_state.context.chat_history.append(assistant_message)

                if not tool_calls:
                    turn_state.state = AgentState.COMPLETED
                    break

                for tool_call in tool_calls:
                    self._raise_if_stop_requested(session_state)
                    tool_iterations += 1
                    if tool_iterations > self.max_tool_iterations:
                        raise RuntimeError(f"Agent exceeded max tool iterations ({self.max_tool_iterations}) for one turn")

                    signature = self._tool_call_signature(tool_call)
                    if signature == last_tool_signature:
                        consecutive_same_tool_calls += 1
                    else:
                        last_tool_signature = signature
                        consecutive_same_tool_calls = 1
                    if consecutive_same_tool_calls >= self.max_same_tool_calls:
                        raise RuntimeError(
                            f"Agent repeated the same tool call {consecutive_same_tool_calls} times without making progress: {tool_call.get('name')}"
                        )

                    await self.hook_runner.dispatch("PreToolUse", turn_state, session=session_state, tool_call=tool_call)
                    start_event = AgentEvent(event_type="ToolExecutionStart", data=tool_call, state=AgentState.RUNNING)
                    await self._publish_runtime_event(session_state, turn_state, start_event)
                    yield start_event
                    result = await self.tool_executor.execute(tool_call, turn_state, session_state)
                    self._raise_if_stop_requested(session_state)
                    await self.hook_runner.dispatch("PostToolUse", turn_state, session=session_state, tool_call=tool_call, result=result)

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "name": tool_call.get("name", ""),
                        "content": self._build_tool_history_result(result),
                    }
                    turn_state.context.chat_history.append(tool_message)
                    end_event = AgentEvent(
                        event_type="ToolExecutionEnd",
                        data={"tool_id": tool_call.get("id"), "result": tool_message["content"]},
                        state=AgentState.RUNNING,
                    )
                    await self._publish_runtime_event(session_state, turn_state, end_event)
                    yield end_event

            await self.turn_reducer.reduce(turn_state.context)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            final_state = turn_state.state if turn_state.state != AgentState.RUNNING else AgentState.COMPLETED
            turn_state.state = final_state
            completed_event = AgentEvent(event_type="TurnCompleted", state=final_state)
            await self._publish_runtime_event(session_state, turn_state, completed_event)
            yield completed_event
        except TurnStoppedError as exc:
            turn_state.state = AgentState.STOPPED
            turn_state.transition_reason = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            stopped_event = self._build_stopped_event(turn_state, reason=str(exc))
            await self._publish_runtime_event(session_state, turn_state, stopped_event)
            yield stopped_event
        except asyncio.CancelledError:
            reason = self._stop_reason(session_state)
            turn_state.state = AgentState.STOPPED
            turn_state.transition_reason = reason
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            stopped_event = self._build_stopped_event(turn_state, reason=reason)
            await self._publish_runtime_event(session_state, turn_state, stopped_event)
            yield stopped_event
        except Exception as exc:
            turn_state.state = AgentState.FAILED
            turn_state.error_text = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnError", turn_state, session=session_state, error=exc)
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            error_event = AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED)
            await self._publish_runtime_event(session_state, turn_state, error_event)
            yield error_event

    async def _publish_stopped_request(self, request: RuntimeTurnRequest, *, reason: str) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=request.session_id,
                turn_id=request.turn_id,
                event_type="TurnStopped",
                state=AgentState.STOPPED.value,
                data={"reason": reason, "turn_id": request.turn_id},
            )
        )

    def _build_stopped_event(self, turn_state: RuntimeTurnState, *, reason: str) -> AgentEvent:
        return AgentEvent(
            event_type="TurnStopped",
            data={"reason": reason, "turn_id": turn_state.request.turn_id},
            state=AgentState.STOPPED,
        )

    def _stop_reason(self, session_state: RuntimeSessionState) -> str:
        return str(session_state.stop_reason or "user-stop")

    def _raise_if_stop_requested(self, session_state: RuntimeSessionState) -> None:
        if session_state.stop_signal.is_set():
            raise TurnStoppedError(self._stop_reason(session_state))

    async def _publish_runtime_event(
        self,
        session_state: RuntimeSessionState,
        turn_state: RuntimeTurnState,
        event: AgentEvent,
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=session_state.session_id,
                turn_id=turn_state.request.turn_id,
                event_type=event.event_type,
                state=event.state.value,
                data=event.data,
            )
        )

    def _build_tool_history_result(self, result: Any) -> str:
        if isinstance(result, dict):
            if result.get("error"):
                return str(result["error"])
            if isinstance(result.get("data"), str):
                return result.get("data") or ""
        return str(result)

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        return json.dumps(
            {
                "name": tool_call.get("name"),
                "arguments": tool_call.get("arguments", {}) or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        normalized = str(text or "")
        if not normalized:
            return 0
        return max(1, len(normalized) // 4)

    def _estimate_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        return self._estimate_text_tokens(json.dumps(messages, ensure_ascii=False, default=str))

    def _estimate_completion_tokens(self, llm_response: dict[str, Any]) -> int:
        parts = [str(llm_response.get("content", "") or "")]
        tool_calls = llm_response.get("tool_calls", []) or []
        if tool_calls:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
        return self._estimate_text_tokens("\n".join(part for part in parts if part))

    async def _finalize_worker_turn_error(self, session_state: RuntimeSessionState, turn_state: RuntimeTurnState, exc: Exception) -> None:
        if turn_state.finished_at <= 0:
            turn_state.state = AgentState.FAILED
            turn_state.error_text = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnError", turn_state, session=session_state, error=exc)
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)

        error_event = AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED)
        await self._publish_runtime_event(session_state, turn_state, error_event)

    async def _finalize_cancelled_turn(self, session_state: RuntimeSessionState, turn_state: RuntimeTurnState, *, reason: str) -> None:
        if turn_state.finished_at > 0:
            return

        turn_state.state = AgentState.STOPPED
        turn_state.transition_reason = reason
        turn_state.finished_at = time.time()
        await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
        await self._publish_runtime_event(session_state, turn_state, self._build_stopped_event(turn_state, reason=reason))

    @classmethod
    async def startup_process_runtime(cls) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "config_loaded": False,
            "mysql_pool_ready": False,
            "qdrant_ready": False,
            "tool_count": 0,
            "skill_count": 0,
        }

        load_config()
        summary["config_loaded"] = True

        try:
            await warm_up_mysql_pool()
            summary["mysql_pool_ready"] = True
        except Exception as exc:
            log.warning("AgentRuntime startup failed to warm MySQL pool: {}", exc)

        try:
            await warm_up_qdrant_client()
            summary["qdrant_ready"] = True
        except Exception as exc:
            log.warning("AgentRuntime startup failed to warm Qdrant client: {}", exc)

        summary["tool_count"] = len(get_tool_registry().tools)
        summary["skill_count"] = len(SkillLoader().load_all_skills() or [])

        try:
            get_memory_manager().warm_up()
            summary["memory_manager_ready"] = True
        except Exception as exc:
            summary["memory_manager_ready"] = False
            log.warning("AgentRuntime startup failed to warm MemoryManager: {}", exc)

        for initializer_name, initializer in (
            ("monitor_pipeline", get_monitor_pipeline),
            ("monitor_store", get_monitor_store),
            ("monitor_alert_evaluator", get_monitor_alert_evaluator),
            ("health_checker", get_health_checker),
            ("monitor_query_service", get_monitor_query_service),
            ("monitor_maintenance_service", get_monitor_maintenance_service),
        ):
            try:
                initializer()
                summary[f"{initializer_name}_ready"] = True
            except Exception as exc:
                summary[f"{initializer_name}_ready"] = False
                log.warning("AgentRuntime startup failed to initialize {}: {}", initializer_name, exc)

        return summary
