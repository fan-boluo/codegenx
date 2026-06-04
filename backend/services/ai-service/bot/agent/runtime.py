
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time
import traceback
from datetime import datetime
from typing import Any, AsyncGenerator
from agent.agent_schema import     AgentEvent,AgentState,AgentEventType
from agent.runtime_schema import (
    RuntimeSessionState,
    TurnStoppedError, ActivateTurn,
)
from agent.session_pool import SessionPool
from bot.llm.llm_recovery import LLMRecoveryMixin
from bot.agent.hook.registry import register_all_hooks
from bot.agent.hook.runner import HookRunner
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import get_tool_registry
from bot.bus import MessageBus, RuntimeTurnEvent
from bot.utils.config import AgentConfig, load_config
from shared.config.log_config import log
from shared.schema.ai_service import AiServiceGenerateRequest
from compact.thresholds import estimate_tokens as _thresholds_estimate

class AgentRuntime(LLMRecoveryMixin):

    CONTINUATION_MESSAGE: str = "Please continue from where you left off."

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
        self.max_steps = self.agent_config.max_steps
        
        self.message_bus = message_bus or MessageBus()
        self.tool_registry = get_tool_registry()
        self.tool_executor = tool_executor or ToolExecutor(self.tool_registry)
        log.info("共加载{}个工具", len(self.tool_registry.tools))

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

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._shutdown_event.clear()
        
        # Start session pool cleanup task
        await self.session_pool.start()

        # Build tool catalog (async — must be awaited)
        self.tools = await self.tool_registry.build_tool(self.config.tools.excluded)
        log.info("工具目录构建完成,共{}个工具", len(self.tools))

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
        """ 一次请求的消息收发 """
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
            active_step_ids = list(session_state.activate_turn.active_steps)

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
        """消费 inbound requests 并传给 session workers."""
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
        session_state, is_new = await self.session_pool.get_or_create(session_id, request, self)

        if is_new:
            await self.hook_runner.dispatch("OnSessionStart", session_state)
            log.debug("新建一个session_state")
        return session_state

    async def _wait_for_request_cleanup(self, session_id: str, request_id: str) -> None:
        """Wait for request cleanup using event notification (avoids busy-wait polling)."""
        session_state = await self.session_pool.get(session_id)
        if session_state is None:
            return
        if request_id not in session_state.active_tasks:
            return
        while True:
            try:
                await asyncio.wait_for(session_state.close_signal.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            # Re-read session_state in case pool replaced it
            session_state = await self.session_pool.get(session_id)
            if session_state is None or request_id not in session_state.active_tasks:
                return
            session_state.close_signal.clear()

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
    # ------------------------------------------------------------------ session worker

    async def _process_session_requests(self, session_state: RuntimeSessionState) -> None:
        """  携程"""
        session_state.state = AgentState.RUNNING
        # Ensure worker_task references the running task and mark processing
        session_state.worker_task = asyncio.current_task()
        session_state.processing = True
        try:
            while not self._shutdown_event.is_set() and not session_state.closed:
                async with session_state.request_lock:
                    # 没有等待的请求了，
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
                    await self._close_session_state(session_state, end_reason="runtime-stop")
                    raise
                except Exception as exc:
                    log.opt(exception=True).error("Unhandled error in request {}: {}", session_state.request_id, exc)
                    await self._publish_request_event(
                        session_state,
                        AgentEvent(event_type=AgentEventType.ERROR, data=str(exc), state=AgentState.FAILED),
                    )
                finally:
                    session_state.active_tasks.pop(session_state.request_id, None)
                    if not session_state.closed:
                        session_state.stop_signal.clear()
                        session_state.stop_reason = ""
                        session_state.close_signal.set()  # 通知 _wait_for_request_cleanup
                    session_state.touch()
                    if session_state.session_manager is not None and session_state.context_manager is not None:
                        await session_state.session_manager.save_history(
                            session_state.session_id,
                            session_state.context_manager.chat_messages,
                            user_id=session_state.user_id
                        )
        finally:
            async with session_state.request_lock:
                # Only clear processing/worker_task if this is the task that set them.
                current_task = asyncio.current_task()
                if session_state.worker_task is current_task:
                    session_state.processing = False
                    session_state.worker_task = None
                log.info("{},{}",session_state.session_id,"finished")




    async def _close_session_state(
        self, session_state: RuntimeSessionState, *, end_reason: str
    ) -> None:
        """Close session and cleanup resources (pool handles removal)."""
        if session_state.closed:
            return
        
        session_state.closed = True
        if end_reason == "runtime-stop" and session_state.activate_turn.active_steps:
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
        """Reset per-request fields."""
        session_state.request = request
        session_state.tool_iterations = 0
        session_state.last_tool_signature = None
        session_state.consecutive_same_tool_calls = 0

        activate_turn = session_state.activate_turn
        activate_turn.step_counter = 0
        activate_turn.active_step_id = ""
        activate_turn.active_steps.clear()
        activate_turn.requires_followup = False
        activate_turn.state = AgentState.IDLE

        now = datetime.utcnow()
        request_dict = request.model_dump()
        request_dict["started_at"] = now.isoformat()
        await session_state.session_manager.append_chat_history_message(session_state.session_id, request_dict)


    # ------------------------------------------------------------------ request execution

    async def _execute_request(self, session_state: RuntimeSessionState) -> None:
        """Process all turns. Always publishes a terminal event; re-raises CancelledError."""
        request_id = session_state.request_id
        activate_turn = session_state.activate_turn
        activate_turn.state = AgentState.RUNNING
        activate_turn.started_at = time.time()

        try:
            # 加入聊天历史
            user_message = session_state.request.message
            context_manager = session_state.context_manager
            if context_manager is None:
                raise RuntimeError("context_manager is not initialized — on_session_start may not have run")
            context_manager.add_user_message(user_message)
            await context_manager.build_system_prompt(user_message)

            await self.hook_runner.dispatch("OnTurnStart", activate_turn, session=session_state)
            await self._publish_runtime_event(
                session_state,
                AgentEvent(event_type=AgentEventType.ON_TURN_START, data={
                    "request_id": request_id,
                    "step_counter": activate_turn.step_counter,
                }, state=activate_turn.state))
            log.debug("{},{},{}",request_id,activate_turn.step_counter," 发送事件 OnTurnStart")
            # 执行turn的任务
            while activate_turn.step_counter < self.max_steps:
                self._raise_if_stop_requested(session_state)
                # 聊天历史微压，清除工具执行结果
                await context_manager.micro_compact(self.config.compact.maxToolResultTokens)
                log.debug("{},{},{}",request_id, activate_turn.step_counter, " micro_compact")
                # 初始化step_id step_counter

                activate_turn.step_counter += 1
                step_id = f"{session_state.request_id}_{activate_turn.step_counter}"  # reqid_1,2,3
                activate_turn.active_step_id = step_id
                activate_turn.active_steps.append(step_id)
                activate_turn.state = AgentState.RUNNING

                try:
                    await self._execute_step(session_state, activate_turn)
                finally:
                    activate_turn.active_steps.pop()
                    activate_turn.active_step_id = ""
                    async for compact_event in context_manager.compact_after_step():
                        await self._publish_runtime_event(session_state, compact_event)
                if not activate_turn.requires_followup:
                    break
            log.debug("{},{},{}",request_id, activate_turn.step_counter, " 执行完一轮了")
            try:
                await context_manager.compact_after_turn()
            except Exception:
                log.exception("compact_after_turn 执行异常（非致命）")
            await self._publish_request_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.REQUEST_COMPLETED,
                    data={"request_id": request_id},
                    state=AgentState.COMPLETED,
                ),
            )
            activate_turn.finished_at = time.time()
            activate_turn.state = AgentState.COMPLETED

        except TurnStoppedError as exc:
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.STOPPED
            await self._publish_request_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.REQUEST_STOPPED,
                    data={"request_id": request_id, "reason": str(exc)},
                    state=AgentState.STOPPED,
                ),
            )
            log.debug("{},{},{}",request_id, activate_turn.step_counter, " TurnStoppedError：",exc)
        except asyncio.CancelledError:
            reason = self._stop_reason(session_state)
            activate_turn.error_text = reason
            activate_turn.state = AgentState.STOPPED
            await self._publish_request_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.REQUEST_STOPPED,
                    data={"request_id": request_id, "reason": reason},
                    state=AgentState.STOPPED,
                ),
            )
            log.debug("{},{},{}",request_id, activate_turn.step_counter, " CancelledError")
            raise
        except Exception as exc:
            log.opt(exception=True).error("_execute_request failed: {}", exc)
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.FAILED
            await self.hook_runner.dispatch("OnError", activate_turn, session=session_state, error=exc)
            await self._publish_request_event(
                session_state,
                AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED),
            )
            log.debug("{},{}, Exception:{}",request_id, activate_turn.step_counter,  exc)
        finally:
            activate_turn.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", activate_turn, session=session_state)
            log.debug("OnTurnEnd {},{}",request_id, activate_turn.step_counter)
    # ------------------------------------------------------------------ turn execution

    async def _execute_step(
        self, session_state: RuntimeSessionState, turn_state: ActivateTurn
    ) -> None:
        self._raise_if_stop_requested(session_state)
        messages = await session_state.context_manager.assemble()
        prompt_tokens = self._estimate_message_tokens(messages)

        await self.hook_runner.dispatch(
            "PreLLMCall",
            turn_state,
            session=session_state,
            messages=messages,
            prompt_tokens=prompt_tokens,
            projected_total_tokens=prompt_tokens,
        )
        await self._publish_runtime_event(
            session_state,
            AgentEvent(
                event_type=AgentEventType.LLM_THINKING_START,
                data={
                    "request_id": session_state.request_id,
                    "prompt_tokens": prompt_tokens,
                    "message_count": len(messages),
                },
                state=AgentState.RUNNING,
            ),
        )
        log.debug("PreLLMCALL {},{},{}",session_state.request_id, turn_state.step_counter,turn_state.active_step_id)
        llm_response = await self._invoke_llm_with_recovery(
            messages, turn_state, session_state
        )
        log.debug(llm_response)
        completion_tokens = self._estimate_completion_tokens(llm_response)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "first_token": 0,
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
        log.debug("PostLLMCall {},{},{}",session_state.request_id, turn_state.step_counter, turn_state.active_step_id)
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
        session_state.context_manager.add_assistant_message(assistant_message)
        if session_state.session_manager is not None:
            await session_state.session_manager.append_chat_history_message(
                session_state.session_id, assistant_message
            )

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

            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call.get("id", ""),
                "name": tool_call.get("name", ""),
                "content": "",
            }
            log.debug("PreToolUse {},{},{}", session_state.request_id, turn_state.step_counter,
                      turn_state.active_step_id)
            pre_result = await self.hook_runner.dispatch(
                "PreToolUse", turn_state, session=session_state, tool_call=tool_call
            )
            if pre_result.get("action") == "blocked":
                messages = pre_result.get("messages", "Blocked by hook")
                tool_message["content"] = f"Tool blocked by PreToolUse hook :{messages}"
                session_state.context_manager.add_tool_message(tool_message)
                continue
            if pre_result.get("action") == "inject":
                messages = pre_result.get("messages", "")
                tool_message["content"] = f" PreToolUse message :{messages}"
                session_state.context_manager.add_tool_message(tool_message)
            await self._publish_runtime_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.TOOL_EXECUTION_START, data=tool_call, state=AgentState.RUNNING
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
            log.debug("PostToolUse {},{},{}",session_state.request_id, turn_state.step_counter, turn_state.active_step_id)

            session_state.context_manager.add_tool_message(tool_message)
            await self._publish_runtime_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.TOOL_EXECUTION_END,
                    data={"tool_id": tool_call.get("id"), "tool_name": tool_call.get("name"), "result": tool_message["content"]},
                    state=AgentState.RUNNING,
                ),
            )

        log.debug("执行完一次step 迭代, step_id: {}, step_counter:{}", turn_state.active_step_id, turn_state.step_counter)

    # ------------------------------------------------------------------ event helpers

    async def _publish_stopped_request(
        self, request: AiServiceGenerateRequest, *, reason: str
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=str(request.session_id or ""),
                request_id=self._request_id(request),
                turn_id="",
                event_type=AgentEventType.REQUEST_STOPPED,
                state=AgentState.STOPPED.value,
                data={"reason": reason, "request_id": self._request_id(request)},
            )
        )

    async def _publish_runtime_event(
        self,
        session_state: RuntimeSessionState,
        event: AgentEvent,
    ) -> None:
        await self._record_chat_history(session_state, event)
        data = self._sanitize_event_data(event)
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=session_state.session_id,
                request_id=session_state.request_id,
                turn_id=session_state.activate_turn.active_step_id,
                event_type=event.event_type,
                state=event.state.value,
                data=data,
            )
        )

    async def _record_chat_history(self, session_state: RuntimeSessionState, event: AgentEvent) -> None:
        """将工具执行结果写入 chat_history JSONL，供前端展示完整对话过程。"""
        sm = session_state.session_manager
        if sm is None:
            return
        if event.event_type == AgentEventType.TOOL_EXECUTION_END:
            tc = event.data if isinstance(event.data, dict) else {}
            await sm.append_chat_history_message(session_state.session_id, {
                "role": "tool",
                "tool_call_id": tc.get("tool_id", ""),
                "name": tc.get("tool_name", ""),
                "content": str(tc.get("result", "")),
            })

    def _sanitize_event_data(self, event: AgentEvent) -> Any:
        """过滤敏感数据，工具事件只描述正在做什么，不传输原始内容。"""
        if event.event_type == AgentEventType.TOOL_EXECUTION_START:
            tc = event.data if isinstance(event.data, dict) else {}
            return {
                "tool_name": tc.get("name", ""),
                "tool_id": tc.get("id", ""),
                "description": f"执行工具: {tc.get('name', 'unknown')}",
            }
        if event.event_type == AgentEventType.TOOL_EXECUTION_END:
            tc = event.data if isinstance(event.data, dict) else {}
            result = str(tc.get("result", ""))
            desc = f"工具执行完成, 输出 {len(result)} 字符"
            return {
                "tool_name": tc.get("tool_name", ""),
                "tool_id": tc.get("tool_id", ""),
                "description": desc,
            }
        return event.data

    async def _publish_request_event(
        self, session_state: RuntimeSessionState, event: AgentEvent
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=session_state.session_id,
                request_id=session_state.request_id,
                turn_id=session_state.activate_turn.active_step_id,
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



    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        return json.dumps(
            {"name": tool_call.get("name"), "arguments": tool_call.get("arguments", {}) or {}},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )



    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        return _thresholds_estimate([{"content": str(text or "")}])

    def _estimate_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        return _thresholds_estimate(messages)

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
