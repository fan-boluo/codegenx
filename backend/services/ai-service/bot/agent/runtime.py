
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, AsyncGenerator

from pygments.lexers import data

from agent.runtime_schema import (
    AgentEvent,
    AgentState,
    RuntimeSessionState,
    RuntimeTurnState,
    TurnContext,
    TurnStoppedError,
)
from agent.session_pool import SessionPool
from bot.agent.context_compaction import ContextCompactor
from bot.agent.llm_recovery import LLMRecoveryMixin
from bot.agent.task.task_manager import TaskManager
from bot.agent.hook.registry import register_all_hooks
from bot.agent.hook.runner import HookRunner
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import get_tool_registry
from bot.bus import MessageBus, RuntimeTurnEvent
from bot.utils.config import AgentConfig, load_config
from monitor.telemetry_schema import TelemetryStatus, TurnTelemetry
from shared.config.log_config import log
from shared.schema.ai_service import AiServiceGenerateRequest


class AgentRuntime(LLMRecoveryMixin):
    CONTINUATION_MESSAGE = (
        "Output limit hit. Continue directly from where you stopped. "
        "Do not restart, recap, or repeat prior content."
    )
    EXCLUDED_TOOL_NAMES = {"compact"}

    def __init__(
        self,
        hook_runner: HookRunner | None = None,
        tool_executor: ToolExecutor | None = None,
        message_bus: MessageBus | None = None,
    ):
        self.config = load_config()
        self.agent_config = self.config.get_default_agent() or AgentConfig()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = 3
        self.stop_grace_seconds = max(0.0, float(self.agent_config.session_stop_grace_seconds or 2.0))
        self.max_steps = self.agent_config.max_steps or 50
        
        self.message_bus = message_bus or MessageBus()
        self.context_compactor = ContextCompactor()
        self.tool_registry = get_tool_registry()
        self.tool_executor = tool_executor or ToolExecutor(self.tool_registry)
        # self.skill_loader = SkillLoader()
        # self.skills = self.skill_loader.load_all_skills()

        log.info("共加载{}个工具", len(self.tool_registry.tools))
        # log.info("共加载{}个skill", len(self.skills) if self.skills else 0)


        self.hook_runner = hook_runner or HookRunner()
        if hook_runner is None:
            register_all_hooks(self.hook_runner)
        self._dispatcher_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        
        # Session pool with intelligent cleanup
        # Default: max 1000 sessions, idle timeout 1 hour, cleanup every 5 minutes
        self.session_pool = SessionPool(
            max_sessions=int(self.agent_config.max_sessions or 1000),
            idle_timeout_seconds=int(self.agent_config.session_idle_timeout_seconds or 3600),
            cleanup_interval_seconds=int(self.agent_config.session_cleanup_interval_seconds or 300),
        )
        
        # self.startup_backup_service()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._shutdown_event.clear()
        
        # Start session pool cleanup task
        await self.session_pool.start()
        
        self._dispatcher_task = asyncio.create_task(
            self._dispatch_loop(), name="agent-runtime-dispatcher"
        )
        log.info("启动完成 dispatch_loop,等待消息。。。")

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._dispatcher_task
            self._dispatcher_task = None

        # Stop session pool (gracefully closes all sessions)
        await self.session_pool.stop()

    # ------------------------------------------------------------------ public API

    async def submit_request(
        self, request: AiServiceGenerateRequest
    ) -> AsyncGenerator[AgentEvent, None]:
        await self.start()
        request_id = self._request_id(request)
        subscriber = self.message_bus.subscribe_request(request_id)
        try:
            await self.message_bus.publish_inbound(request)
            log.info("message_bus 输送请求：{}", request_id)
            while True:
                item = await subscriber.get()
                if not isinstance(item, RuntimeTurnEvent):
                    continue
                event = AgentEvent(
                    event_type=item.event_type, data=item.data, state=AgentState(item.state)
                )
                yield event
                if event.event_type in {"RequestCompleted", "RequestStopped", "Error"}:
                    await self._wait_for_request_cleanup(
                        str(request.session_id or ""), request_id
                    )
                    break
        finally:
            self.message_bus.unsubscribe_request(request_id, subscriber)

    async def stop_request(
        self,
        *,
        session_id: str,
        request_id: str,
        reason: str = "user-stop",
        grace_seconds: float | None = None,
    ) -> dict[str, Any]:
        active_tasks: list[tuple[str, asyncio.Task[Any]]] = []
        dropped_requests: list[AiServiceGenerateRequest] = []
        remaining_requests: list[AiServiceGenerateRequest] = []
        stop_reason = str(reason or "user-stop")
        target_request_id = str(request_id or "").strip()

        # Get session from pool
        session_state = await self.session_pool.get(session_id)
        if session_state is None or session_state.closed:
            return {
                "accepted": False,
                "sessionId": session_id,
                "stoppedRequestCount": 0,
                "droppedRequestCount": 0,
                "activeRequestIds": [],
                "droppedRequestIds": [],
                "activeTurnIds": [],
            }

        session_state.touch()
        if target_request_id in session_state.active_tasks:
            session_state.stop_signal.set()
            session_state.stop_reason = stop_reason
            active_tasks = [
                (target_request_id, session_state.active_tasks[target_request_id])
            ]

        async with session_state.request_lock:
            for queued_request in list(session_state.pending_requests):
                if self._request_id(queued_request) == target_request_id:
                    dropped_requests.append(queued_request)
                else:
                    remaining_requests.append(queued_request)
            session_state.pending_requests = remaining_requests

        # Publish stopped events for dropped requests
        for queued_request in dropped_requests:
            await self._publish_stopped_request(queued_request, reason=stop_reason)

        # Wait for active tasks with timeout
        timeout_seconds = (
            self.stop_grace_seconds if grace_seconds is None else max(0.0, float(grace_seconds))
        )
        if active_tasks:
            done, pending = await asyncio.wait(
                [task for _, task in active_tasks], timeout=timeout_seconds
            )
            if pending:
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        # Clear stop signal
        session_state = await self.session_pool.get(session_id)
        if session_state is not None and not session_state.closed:
            session_state.stop_signal.clear()
            session_state.stop_reason = ""

        # Get active turn IDs
        active_step_ids: list[str] = []
        session_state = await self.session_pool.get(session_id)
        if session_state is not None and active_tasks:
            active_step_ids = [
                t.turn_id
                for t in session_state.active_steps.values()
                if t.request_id == target_request_id
            ]

        return {
            "accepted": bool(active_tasks or dropped_requests),
            "sessionId": session_id,
            "stoppedRequestCount": len(active_tasks),
            "droppedRequestCount": len(dropped_requests),
            "activeRequestIds": [rid for rid, _ in active_tasks],
            "droppedRequestIds": [str(r.request_id or "") for r in dropped_requests],
            "activeTurnIds": active_step_ids,
        }

    # ------------------------------------------------------------------ dispatch loop

    async def _dispatch_loop(self) -> None:
        """Consume inbound requests and route to session workers."""
        while not self._shutdown_event.is_set():
            request = await self.message_bus.consume_inbound()
            log.info(
                "dispatcher loop 接收到请求：{} {}",
                request.request_id,
                str(getattr(request, "message", "") or "")[:10],
            )
            if not isinstance(request, AiServiceGenerateRequest):
                continue
            
            # Get or create session using pool (no lock needed)
            session_state = await self._get_or_create_session_state(request)
            
            # Add request to session pending queue and trigger processing
            await self._enqueue_session_request(session_state, request)
            log.debug("{} 已加入 session pending_requests", request.request_id)

    async def _get_or_create_session_state(
        self, request: AiServiceGenerateRequest
    ) -> RuntimeSessionState:
        """Get existing session or create new one using pool."""
        session_id = str(request.session_id or "")
        
        # Factory function to create new sessions
        def create_session():
            return RuntimeSessionState(session_id=session_id, request=request, runtime=self)
        
        # Get or create from pool
        session_state, is_new = await self.session_pool.get_or_create(session_id, create_session)
        
        # Initialize new sessions
        if is_new:
            session_state.request = request
            session_state.touch()
            
            # Attach per-app task board (s12) — shared across sessions for the same app_id
            # task 在上下文管理
            # try:
            #     session_state.task_manager = TaskManager(session_state.app_id)
            # except Exception as exc:
            #     log.warning(
            #         "[TaskManager] Failed to initialise for app_id {}: {}",
            #         session_state.app_id,
            #         exc,
            #     )
            
            # Dispatch OnSessionStart hook
            await self.hook_runner.dispatch("OnSessionStart", session_state)
        else:
            # Existing session: update request and touch
            session_state.request = request
            session_state.touch()
        
        return session_state

    async def _wait_for_request_cleanup(self, session_id: str, request_id: str) -> None:
        """Wait for request cleanup (simplified with pool)."""
        while True:
            session_state = await self.session_pool.get(session_id)
            if session_state is None or request_id not in session_state.active_tasks:
                return
            await asyncio.sleep(0.01)

    # ------------------------------------------------------------------ session worker

    async def _process_session_requests(self, session_state: RuntimeSessionState) -> None:
        """Process session requests in an event-driven way."""
        session_state.state = AgentState.RUNNING
        # Ensure worker_task references the running task and mark processing
        session_state.worker_task = asyncio.current_task()
        session_state.processing = True
        try:
            while not self._shutdown_event.is_set() and not session_state.closed:
                async with session_state.request_lock:
                    if not session_state.pending_requests:
                        session_state.processing = False
                        return
                    request = session_state.pending_requests.pop(0)

                log.debug("session event triggered, processing request: {}", request.request_id)
                await self._reset_request_state(session_state, request)
                request_task = asyncio.create_task(
                    self._execute_request(session_state),
                    name=f"agent-request-{session_state.request_id}",
                )
                log.debug("请求任务开始执行:{}", session_state.request_id)
                session_state.active_tasks[session_state.request_id] = request_task
                try:
                    await request_task
                except asyncio.CancelledError:
                    request_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await request_task
                    await self._close_session_state(session_state, end_reason="runtime-stop")
                    raise
                finally:
                    session_state.active_tasks.pop(session_state.request_id, None)
                    if not session_state.closed:
                        session_state.stop_signal.clear()
                        session_state.stop_reason = ""
                    session_state.touch()
                    if session_state.session_manager is not None and session_state.context is not None:
                        session_state.session_manager.save_history(
                            session_state.session_id, session_state.context.chat_history
                        )
        finally:
            async with session_state.request_lock:
                # Only clear processing/worker_task if this is the task that set them.
                current_task = asyncio.current_task()
                if session_state.worker_task is current_task:
                    session_state.processing = False
                    session_state.worker_task = None

    async def _enqueue_session_request(
        self, session_state: RuntimeSessionState, request: AiServiceGenerateRequest
    ) -> None:
        """Add request to session pending list and trigger processing."""
        async with session_state.request_lock:
            session_state.pending_requests.append(request)
            session_state.touch()
            if session_state.processing:
                return
            session_state.processing = True
            session_state.worker_task = asyncio.create_task(
                self._process_session_requests(session_state),
                name=f"agent-session-{session_state.session_id}",
            )


    async def _close_session_state(
        self, session_state: RuntimeSessionState, *, end_reason: str
    ) -> None:
        """Close session and cleanup resources (pool handles removal)."""
        if session_state.closed:
            return
        
        session_state.closed = True
        if end_reason == "runtime-stop" and session_state.active_steps:
            session_state.state = AgentState.STOPPED
        elif session_state.state != AgentState.FAILED:
            session_state.state = AgentState.COMPLETED
        
        if session_state.worker_task is not None and session_state.worker_task.cancelled():
            session_state.worker_task = None
        
        await self.hook_runner.dispatch("OnSessionEnd", session_state, end_reason=end_reason)
        
        # Pool will automatically remove closed sessions during cleanup
        log.info("Session {} closed: end_reason={}", session_state.session_id, end_reason)

    # ------------------------------------------------------------------ request state

    async def _reset_request_state(
        self, session_state: RuntimeSessionState, request: AiServiceGenerateRequest
    ) -> None:
        """Reset per-request fields and build the initial turn context (once per request)."""
        session_state.request = request
        session_state.step_counter = 0
        session_state.active_step_id = ""
        # session_state.request_id = self._request_id(request)
        # session_state.context = TurnContext(user_input=str(request.message or ""))
        # session_state.tool_iterations = 0
        # session_state.last_tool_signature = None
        # session_state.consecutive_same_tool_calls = 0

        # 记录用户信息
        now = datetime.utcnow()
        request_dict = request.model_dump()
        request_dict["started_at"] = now.isoformat()
        session_state.session_manager.append_chat_history_message(session_state.session_id, request_dict)


    # ------------------------------------------------------------------ turn building

    async def _build_turn_state(self, session_state: RuntimeSessionState) -> RuntimeTurnState:
        # turn_id的处理
        session_state.step_counter += 1
        turn_number = session_state.step_counter
        turn_id = f"{session_state.request_id}"
        
        return RuntimeTurnState(
            turn_id=turn_id,
            turn_number=turn_number,
            request_id=session_state.request_id,
            context=TurnContext(user_input=str(session_state.request.message or ""))
        )

    # ------------------------------------------------------------------ request execution

    async def _execute_request(self, session_state: RuntimeSessionState) -> None:
        """Process all turns. Always publishes a terminal event; re-raises CancelledError."""
        request_id = session_state.request_id
        # 加入聊天历史
        user_message = session_state.request.message
        context_manager = session_state.context_manager
        context_manager.add_user_message(user_message)
        # 聊天历史微压，清除工具执行结果
        context_manager.microcompact()
        try:
            # 执行turn的任务
            while True:
                self._raise_if_stop_requested(session_state)
                turn_state = await self._build_turn_state(session_state)
                session_state.active_steps[turn_state.turn_id] = turn_state
                session_state.active_step_id = turn_state.turn_id

                try:
                    await self._execute_step(session_state, turn_state)
                finally:
                    if session_state.context is not None:
                        session_state.context.chat_history = deepcopy(
                            turn_state.context.chat_history
                        )
                    session_state.active_steps.pop(turn_state.turn_id, None)
                    session_state.active_step_id = ""

                if turn_state.state == AgentState.STOPPED:
                    raise TurnStoppedError(turn_state.transition_reason or "stopped")
                if turn_state.state == AgentState.FAILED:
                    session_state.state = AgentState.FAILED
                    raise RuntimeError(turn_state.error_text or "turn failed")
                if not turn_state.requires_followup:
                    break

            await self._publish_request_event(
                session_state,
                AgentEvent(
                    event_type="RequestCompleted",
                    data={"request_id": request_id},
                    state=AgentState.COMPLETED,
                ),
            )

        except TurnStoppedError:
            await self._publish_request_event(
                session_state,
                AgentEvent(
                    event_type="RequestStopped",
                    data={"request_id": request_id, "reason": self._stop_reason(session_state)},
                    state=AgentState.STOPPED,
                ),
            )
        except asyncio.CancelledError:
            await self._publish_request_event(
                session_state,
                AgentEvent(
                    event_type="RequestStopped",
                    data={"request_id": request_id, "reason": self._stop_reason(session_state)},
                    state=AgentState.STOPPED,
                ),
            )
            raise
        except Exception as exc:
            session_state.state = AgentState.FAILED
            await self._publish_request_event(
                session_state,
                AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED),
            )

    # ------------------------------------------------------------------ turn execution

    async def _execute_step(
        self, session_state: RuntimeSessionState, turn_state: RuntimeTurnState
    ) -> None:
        turn_state.state = AgentState.RUNNING
        turn_state.started_at = time.time()

        try:
            self._raise_if_stop_requested(session_state)

            await self.hook_runner.dispatch("OnTurnStart", turn_state, session=session_state)
            await self._publish_runtime_event(
                session_state,
                turn_state,
                AgentEvent(
                    event_type="OnTurnStart",
                    data={"request_id": turn_state.request_id, "turn_id": turn_state.turn_id},
                    state=AgentState.RUNNING,
                ),
            )

            self._raise_if_stop_requested(session_state)
            await self.context_compactor.prepare_for_llm(turn_state.context)
            messages = await session_state.context_manager.assemble()
            prompt_tokens = self._estimate_message_tokens(messages)
            session_telemetry = session_state.telemetry
            projected_total_tokens = prompt_tokens
            if session_telemetry is not None:
                projected_total_tokens += int(
                    session_telemetry.total_prompt_tokens or 0
                ) + int(session_telemetry.total_completion_tokens or 0)

            await self.hook_runner.dispatch(
                "PreLLMCall",
                turn_state,
                session=session_state,
                messages=messages,
                prompt_tokens=prompt_tokens,
                projected_total_tokens=projected_total_tokens,
                tool_catalog=turn_state.context.tool,
            )
            await self._publish_runtime_event(
                session_state,
                turn_state,
                AgentEvent(
                    event_type="LLM_Thinking_Start",
                    data={
                        "request_id": turn_state.request_id,
                        "turn_id": turn_state.turn_id,
                        "prompt_tokens": prompt_tokens,
                        "message_count": len(messages),
                    },
                    state=AgentState.RUNNING,
                ),
            )

            llm_response = await self._invoke_llm_with_recovery(
                messages, turn_state, session_state
            )

            completion_tokens = self._estimate_completion_tokens(llm_response)
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "first_token": 0, # TODO 还没
                "is_error": False,
            }
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

            turn_state.requires_followup = bool(tool_calls)
            for tool_call in tool_calls:
                self._raise_if_stop_requested(session_state)
                session_state.tool_iterations += 1
                if session_state.tool_iterations > self.max_tool_iterations:
                    raise RuntimeError(
                        f"Agent exceeded max tool iterations ({self.max_tool_iterations})"
                    )

                signature = self._tool_call_signature(tool_call)
                if signature == session_state.last_tool_signature:
                    session_state.consecutive_same_tool_calls += 1
                else:
                    session_state.last_tool_signature = signature
                    session_state.consecutive_same_tool_calls = 1
                if session_state.consecutive_same_tool_calls >= self.max_same_tool_calls:
                    raise RuntimeError(
                        f"Agent repeated the same tool call "
                        f"{session_state.consecutive_same_tool_calls} times: "
                        f"{tool_call.get('name')}"
                    )

                await self.hook_runner.dispatch(
                    "PreToolUse", turn_state, session=session_state, tool_call=tool_call
                )
                await self._publish_runtime_event(
                    session_state,
                    turn_state,
                    AgentEvent(
                        event_type="ToolExecutionStart", data=tool_call, state=AgentState.RUNNING
                    ),
                )
                result = await self.tool_executor.execute(tool_call, turn_state, session_state)
                self._raise_if_stop_requested(session_state)
                await self.hook_runner.dispatch(
                    "PostToolUse",
                    turn_state,
                    session=session_state,
                    tool_call=tool_call,
                    result=result,
                )

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "name": tool_call.get("name", ""),
                    "content": self.context_compactor.persist_large_output(
                        turn_state.context,
                        tool_call.get("id", ""),
                        self._build_tool_history_result(result),
                    ),
                }
                turn_state.context.chat_history.append(tool_message)
                await self._publish_runtime_event(
                    session_state,
                    turn_state,
                    AgentEvent(
                        event_type="ToolExecutionEnd",
                        data={"tool_id": tool_call.get("id"), "result": tool_message["content"]},
                        state=AgentState.RUNNING,
                    ),
                )

            await self.context_compactor.finalize_turn(turn_state.context)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            turn_state.state = AgentState.COMPLETED
            await self._publish_runtime_event(
                session_state,
                turn_state,
                AgentEvent(
                    event_type="TurnCompleted",
                    data={
                        "request_id": turn_state.request_id,
                        "turn_id": turn_state.turn_id,
                        "requires_followup": turn_state.requires_followup,
                    },
                    state=AgentState.COMPLETED,
                ),
            )
        except TurnStoppedError as exc:
            turn_state.state = AgentState.STOPPED
            turn_state.transition_reason = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            await self._publish_runtime_event(
                session_state,
                turn_state,
                self._build_stopped_event(turn_state, reason=str(exc)),
            )
        except asyncio.CancelledError:
            reason = self._stop_reason(session_state)
            turn_state.state = AgentState.STOPPED
            turn_state.transition_reason = reason
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            await self._publish_runtime_event(
                session_state, turn_state, self._build_stopped_event(turn_state, reason=reason)
            )
        except Exception as exc:
            turn_state.state = AgentState.FAILED
            turn_state.error_text = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnError", turn_state, session=session_state, error=exc)
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            await self._publish_runtime_event(
                session_state,
                turn_state,
                AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED),
            )

    # ------------------------------------------------------------------ event helpers

    async def _publish_stopped_request(
        self, request: AiServiceGenerateRequest, *, reason: str
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=str(request.session_id or ""),
                request_id=self._request_id(request),
                turn_id="",
                event_type="RequestStopped",
                state=AgentState.STOPPED.value,
                data={"reason": reason, "request_id": self._request_id(request)},
            )
        )

    def _build_stopped_event(self, turn_state: RuntimeTurnState, *, reason: str) -> AgentEvent:
        return AgentEvent(
            event_type="TurnStopped",
            data={
                "reason": reason,
                "turn_id": turn_state.turn_id,
                "request_id": turn_state.request_id,
            },
            state=AgentState.STOPPED,
        )

    async def _publish_runtime_event(
        self,
        session_state: RuntimeSessionState,
        turn_state: RuntimeTurnState,
        event: AgentEvent,
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=session_state.session_id,
                request_id=turn_state.request_id,
                turn_id=turn_state.turn_id,
                event_type=event.event_type,
                state=event.state.value,
                data=event.data,
            )
        )

    async def _publish_request_event(
        self, session_state: RuntimeSessionState, event: AgentEvent
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=session_state.session_id,
                request_id=session_state.request_id,
                turn_id=session_state.active_step_id,
                event_type=event.event_type,
                state=event.state.value,
                data=event.data,
            )
        )

    # ------------------------------------------------------------------ utility helpers

    def _stop_reason(self, session_state: RuntimeSessionState) -> str:
        return str(session_state.stop_reason or "user-stop")

    def _raise_if_stop_requested(self, session_state: RuntimeSessionState) -> None:
        if session_state.stop_signal.is_set():
            raise TurnStoppedError(self._stop_reason(session_state))

    def _build_tool_history_result(self, result: Any) -> str:
        if isinstance(result, dict):
            if result.get("error"):
                return str(result["error"])
            if isinstance(result.get("data"), str):
                return result.get("data") or ""
        return str(result)

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        return json.dumps(
            {"name": tool_call.get("name"), "arguments": tool_call.get("arguments", {}) or {}},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        normalized = str(text or "")
        return max(1, len(normalized) // 4) if normalized else 0

    def _estimate_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        return self._estimate_text_tokens(json.dumps(messages, ensure_ascii=False, default=str))

    def _estimate_completion_tokens(self, llm_response: dict[str, Any]) -> int:
        parts = [str(llm_response.get("content", "") or "")]
        tool_calls = llm_response.get("tool_calls", []) or []
        if tool_calls:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
        return self._estimate_text_tokens("\n".join(p for p in parts if p))


    @staticmethod
    def _request_id(request: AiServiceGenerateRequest) -> str:
        return str(getattr(request, "request_id", "") or "")

    # ------------------------------------------------------------------ monitoring

    async def get_runtime_stats(self) -> dict[str, Any]:
        """Get runtime statistics for monitoring and debugging."""
        pool_stats = self.session_pool.stats()
        return {
            "session_pool": pool_stats,
            "dispatcher_active": self._dispatcher_task is not None
            and not self._dispatcher_task.done(),
        }

    # ------------------------------------------------------------------ startup warmup

    # async def startup_backup_service(self) -> None:
    #     try:
    #         await warm_up_mysql_pool()
    #     except Exception as exc:
    #         log.warning("AgentRuntime startup failed to warm MySQL pool: {}", exc)
    #
    #     try:
    #         await warm_up_qdrant_client()
    #         log.info("启动 warm_up_qdrant_client ready")
    #     except Exception as exc:
    #         log.warning("AgentRuntime startup failed to warm Qdrant client: {}", exc)
    #
    #     for initializer_name, initializer in (
    #         ("monitor_pipeline", get_monitor_pipeline),
    #         ("monitor_store", get_monitor_store),
    #         ("monitor_alert_evaluator", get_monitor_alert_evaluator),
    #         ("health_checker", get_health_checker),
    #         ("monitor_query_service", get_monitor_query_service),
    #         ("monitor_maintenance_service", get_monitor_maintenance_service),
    #     ):
    #         try:
    #             initializer()
    #             log.info(f"启动 {initializer_name} ready")
    #         except Exception as exc:
    #             log.warning(
    #                 "AgentRuntime startup failed to initialize {}: {}", initializer_name, exc
    #             )
