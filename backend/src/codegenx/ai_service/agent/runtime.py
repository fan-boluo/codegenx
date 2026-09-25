
from __future__ import annotations

import asyncio
from contextlib import suppress
import ast
import json
import time
import traceback
from datetime import datetime
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import result

from codegenx.ai_service.agent.agent_schema import     AgentEvent,AgentState,AgentEventType
from codegenx.ai_service.agent.runtime_schema import (
    RuntimeSessionState,
    TurnStoppedError, ActivateTurn,
)
from codegenx.ai_service.agent.session_pool import SessionPool
from codegenx.ai_service.llm.llm_recovery import LLMRecoveryMixin
from codegenx.ai_service.hook import HookAction, HookContext, HookEvent, hook_manager, on
from codegenx.ai_service.agent.tool_executor import ToolExecutor
from codegenx.ai_service.agent.tool_handler import get_tool_registry
from codegenx.ai_service.bus import MessageBus, RuntimeTurnEvent
from codegenx.ai_service.utils.config import AgentConfig, config
from codegenx.ai_service.context.session_context import SessionContext
from shared import log
from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest
from codegenx.ai_service.compact.thresholds import estimate_tokens as _thresholds_estimate

class AgentRuntime(LLMRecoveryMixin):

    CONTINUATION_MESSAGE: str = "Please continue from where you left off."

    def __init__(
        self,
        tool_executor: ToolExecutor | None = None,
        message_bus: MessageBus | None = None,
    ):
        self.config = config
        self.agent_config = self.config.get_default_agent() or AgentConfig()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = 3
        self.stop_grace_seconds = max(0.0, float(self.agent_config.session_stop_grace_seconds or 2.0))
        self.max_steps = self.agent_config.max_steps

        self.message_bus = message_bus or MessageBus()
        self.tool_registry = get_tool_registry()
        self.tool_executor = tool_executor or ToolExecutor(self.tool_registry)
        log.info("共加载{}个工具", len(self.tool_registry.tools))

        # hook 监听器随应用 import 链完成 @on 收集，由 AgentAdapterService.startup() 冻结（docs/Hook设计.md §6）
        self._dispatcher_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        
        # Session pool with intelligent cleanup
        # Default: max 1000 sessions, idle timeout 1 hour, cleanup every 5 minutes
        self.session_pool = SessionPool(
            max_sessions=int(self.agent_config.max_sessions or 1000),
            idle_timeout_seconds=int(self.agent_config.session_idle_timeout_seconds or 3600),
            cleanup_interval_seconds=int(self.agent_config.session_cleanup_interval_seconds or 300),
            swap_idle_seconds=int(getattr(self.agent_config, "session_swap_idle_seconds", 0) or 300),
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
        session_state, is_new = await self.session_pool.get_or_create(session_id, request)

        if is_new:
            await hook_manager.emit(
                HookEvent.SESSION_START,
                HookContext(event=HookEvent.SESSION_START, session=session_state),
            )
            log.debug("新建一个session_state")
        else:
            # P3 swap-out 恢复：闲置卸载过的会话按需从快照重载 chat_messages
            # （复用 on_session_start 已有的快照重载逻辑，docs/SystemApp架构设计.md §7）
            await self._restore_swapped_session(session_state)
        return session_state

    async def _restore_swapped_session(self, session_state: RuntimeSessionState) -> None:
        if not getattr(session_state, "swapped_out", False):
            return
        session_state.swapped_out = False
        cm = session_state.context_manager
        if cm is not None and not cm.chat_messages:
            from codegenx.ai_service.system_app import get_app

            cm.chat_messages = await get_app().session_io.get_turn_chat_message_snapshot(
                user_id=session_state.user_id,
                app_id=session_state.app_id,
                session_id=session_state.session_id,
            ) or []
            log.info("swapped-out session {} 已从快照恢复（{} 条消息）",
                     session_state.session_id, len(cm.chat_messages))

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
                    await self._publish_runtime_event(
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
        
        await hook_manager.emit(
            HookEvent.SESSION_END,
            HookContext(
                event=HookEvent.SESSION_END,
                session=session_state,
                data={"end_reason": end_reason},
            ),
        )
        
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


    # ------------------------------------------------------------------ request execution

    async def _execute_request(self, session_state: RuntimeSessionState) -> None:
        """Process all turns. Always publishes a terminal event; re-raises CancelledError."""
        request_id = session_state.request_id
        activate_turn = session_state.activate_turn
        activate_turn.state = AgentState.RUNNING
        activate_turn.started_at = time.time()

        # turn 级 hook 上下文：整个 turn 复用同一 ctx，保证 span 栈跨事件连续（洋葱树 §8.2）
        hook_ctx = HookContext(
            event=HookEvent.TURN_START, session=session_state, turn=activate_turn
        )

        async with hook_manager.span("turn", hook_ctx):
            try:
                # 加入聊天历史
                user_message = session_state.request.message
                context_manager = session_state.context_manager
                if context_manager is None:
                    raise RuntimeError("context_manager is not initialized — on_session_start hook may not have run")
                context_manager.add_user_message(user_message)
                await context_manager.build_system_prompt(user_message)

                await self._fire(HookEvent.TURN_START, hook_ctx)
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
                        # step 层洋葱 span：记录单个 step 用时与 trace 路径
                        async with hook_manager.span(step_id, hook_ctx):
                            await self._execute_step(session_state, activate_turn, hook_ctx)
                    finally:
                        activate_turn.active_steps.pop()
                        activate_turn.active_step_id = ""
                        async for compact_event in context_manager.compact_after_step():
                            activate_turn.last_step_compacted = True
                            await self._publish_runtime_event(session_state, compact_event)
                    if not activate_turn.requires_followup:
                        break
                log.debug("{},{},{}",request_id, activate_turn.step_counter, " 执行完一轮了")
                try:
                    await context_manager.compact_after_turn()
                except Exception as exc:
                    log.debug(traceback.format_exc())
                    log.exception("compact_after_turn 执行异常（非致命）")
                # 输出安全校验（on_complete）：blocked 时以安全提示替换最终回复
                final_output = self._last_assistant_content(session_state)
                await self._fire(HookEvent.ON_COMPLETE, hook_ctx, final_output=final_output)
                if hook_ctx.action == HookAction.BLOCKED:
                    self._replace_last_assistant_content(session_state, hook_ctx.message)
                await self._publish_runtime_event(
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
                await self._publish_runtime_event(
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
                await self._publish_runtime_event(
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
                await self._fire(HookEvent.INTERNAL_ON_ERROR, hook_ctx, error=exc)
                await self._publish_runtime_event(
                    session_state,
                    AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED),
                )
                log.debug("{},{}, Exception:{}",request_id, activate_turn.step_counter,  exc)
            finally:
                activate_turn.finished_at = time.time()
                await self._fire(HookEvent.TURN_END, hook_ctx)
                log.debug("OnTurnEnd {},{}",request_id, activate_turn.step_counter)
    # ------------------------------------------------------------------ turn execution

    async def _execute_step(
        self, session_state: RuntimeSessionState, turn_state: ActivateTurn, hook_ctx: HookContext
    ) -> None:
        self._raise_if_stop_requested(session_state)
        # 上下文组装（before_build 洋葱分发：hook 可修改输入/短路产出，最内层为 assemble）
        messages = await hook_manager.waterfall(
            HookEvent.BEFORE_BUILD,
            hook_ctx,
            inner=session_state.context_manager.assemble,
        )
        prompt_tokens = self._estimate_message_tokens(messages)

        await self._fire(
            HookEvent.BEFORE_LLM_INVOKE,
            hook_ctx,
            messages=messages,
            prompt_tokens=prompt_tokens,
            projected_total_tokens=prompt_tokens,
        )
        if hook_ctx.action == HookAction.BLOCKED:
            # blocked：以拦截消息作为本轮回复并结束 turn（限流/预算控制场景）
            log.warning("before_llm_invoke 拦截 LLM 调用: {}", hook_ctx.message)
            session_state.context_manager.add_assistant_message(
                {"role": "assistant", "content": hook_ctx.message or "LLM 调用被 hook 拦截"}
            )
            turn_state.requires_followup = False
            return
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
        # LLM 调用层洋葱 span：记录用时与 trace 路径
        async with hook_manager.span("llm_invoke", hook_ctx):
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
        await self._fire(
            HookEvent.AFTER_LLM_INVOKE,
            hook_ctx,
            parallel=True,
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
        # assistant 消息入库（MySQL chat_message 表；失败不阻断对话）
        try:
            from codegenx.ai_service.chat_message import get_chat_message_store
            await get_chat_message_store().append_message(
                session_state.user_id, str(session_state.app_id), session_state.session_id, assistant_message
            )
        except Exception as exc:
            log.warning("assistant 消息入库失败（不影响对话）: {}", exc)

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
                tc_name = tool_call.get('name', 'unknown')
                tc_args = tool_call.get('arguments', {}) or {}
                arg_path = (tc_args.get('path') or '').strip() if isinstance(tc_args, dict) else ''
                arg_content = (tc_args.get('content') or '').strip() if isinstance(tc_args, dict) else ''
                if tc_name in ('write_file', 'read_file', 'edit_file') and (not arg_path or (tc_name == 'write_file' and not arg_content)):
                    raise RuntimeError(
                        f"模型连续 {session_state.consecutive_same_tool_calls} 次调用 {tc_name} "
                        f"但未提供有效参数(path={repr(arg_path)}, content_len={len(arg_content)})。"
                        f"可能是上下文过长导致大模型参数丢失，建议新建会话重试。"
                    )
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
                "state":""
            }
            log.debug("PreToolUse {},{},{}", session_state.request_id, turn_state.step_counter,
                      turn_state.active_step_id)
            # before_tool_call 顺序分发：安全/参数守卫可 blocked（含文件工具参数校验，
            # 已迁移至 tools/base.py file_tool_param_guard）或 inject 注入提示
            await self._fire(HookEvent.BEFORE_TOOL_CALL, hook_ctx, tool_call=tool_call)
            if hook_ctx.action == HookAction.BLOCKED:
                log.debug("PreToolUse blocked")
                tool_message["content"] = f"Tool blocked by hook :{hook_ctx.message}"
                tool_message['state'] = "blocked"
                session_state.context_manager.add_tool_message(tool_message)
                continue
            if hook_ctx.action == HookAction.INJECT:
                log.debug("PreToolUse inject")
                tool_message["content"] = f" PreToolUse message :{hook_ctx.message}"
                session_state.context_manager.add_tool_message(tool_message)

            await self._publish_runtime_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.TOOL_EXECUTION_START, data=tool_call, state=AgentState.RUNNING
                ),
            )
            # 工具层洋葱 span：记录单次工具执行用时与 trace 路径
            async with hook_manager.span(f"tool_call:{tool_call.get('name', 'unknown')}", hook_ctx):
                result = await self.tool_executor.execute(tool_call, turn_state, session_state)
            self._raise_if_stop_requested(session_state)
            await self._fire(
                HookEvent.AFTER_TOOL_CALL,
                hook_ctx,
                parallel=True,
                tool_call=tool_call,
                result=result,
            )
            log.debug("PostToolUse {},{},{}",session_state.request_id, turn_state.step_counter, turn_state.active_step_id)
            if isinstance(result, dict):
                # Safety check 失败等返回的 dict
                tool_message['content'] = result.get('error') or result.get('message') or ''
                tool_message['state'] = "failure"
            elif result.success:
                tool_message['content'] = result.data or ""
                tool_message['state'] = "success"
            else:
                tool_message['content'] = result.message or ""
                tool_message['state'] = "failure"
            session_state.context_manager.add_tool_message(tool_message)
            # 附加文件路径供前端渲染
            tool_message['render'] = result.get('render', '') if isinstance(result, dict) else (result.render or '')
            tool_message['state'] = tool_message.get('state', '')  # ensure state is set before sanitize strips it
            # tool_name = tool_message.get("name", "")
            # if tool_name in ("read_file", "write_file"):
            #     tool_message["path"] = tool_call.get("arguments", {}).get("path", "")
            await self._publish_runtime_event(
                session_state,
                AgentEvent(
                    event_type=AgentEventType.TOOL_EXECUTION_END,
                    data= tool_message,
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



    async def _record_chat_history(self, session_state: RuntimeSessionState, event: AgentEvent) -> None:
        """将工具执行结果写入 chat_message 表，供前端展示完整对话过程。"""
        if event.event_type == AgentEventType.TOOL_EXECUTION_END:
            tc = event.data if isinstance(event.data, dict) else {}
            try:
                from codegenx.ai_service.chat_message import get_chat_message_store
                await get_chat_message_store().append_message(
                    session_state.user_id,
                    str(session_state.app_id),
                    session_state.session_id,
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("tool_id", ""),
                        "name": tc.get("tool_name", ""),
                        "content": str(tc.get("description", "")),
                    },
                )
            except Exception as exc:
                log.warning("tool 消息入库失败（不影响对话）: {}", exc)

    def _sanitize_event_data(self, event: AgentEvent) -> None:
        """过滤敏感数据，工具事件只描述正在做什么，不传输原始内容。"""
        if event.event_type == AgentEventType.TOOL_EXECUTION_START:
            tc = event.data if isinstance(event.data, dict) else {}
            event.data =  {
                "tool_name": tc.get("name", ""),
                "tool_id": tc.get("id", ""),
                "description": f"执行工具: {tc.get('name', 'unknown')}",
            }
        if event.event_type == AgentEventType.TOOL_EXECUTION_END:
            tc = event.data if isinstance(event.data, dict) else {}
            # TODO 添加友好的工具执行描述
            # desc = tc.get('content')
            tool_name = tc.get('name')
            # render = self._render_tool_front(tc)
            render_raw = tc.get("render", "")
            desc = render_raw or tc.get("name", "unknown")

            event.data = {
                "tool_name": tc.get("name", ""),
                "tool_id": tc.get("tool_call_id", ""),
                "description": desc,
                "state": tc.get("state", ""),
            }
            # 附加结构化 task_data 供前端任务面板消费
            if tool_name in ("task_create", "task_update") and render_raw:
                try:
                    task_data = json.loads(render_raw)
                    event.data["task_data"] = task_data
                    event.data["description"] = task_data.get("task", {}).get("subject", tc.get("name", ""))
                except Exception:
                    event.data["description"] = render_raw or tc.get("name", "")


    # def _render_tool_front(self,tc:dict):
    #     """返回工具执行结果的人类可读渲染字符串，供前端展示。"""
    #     tool_name = tc.get('name')
    #     content = tc.get('content', '')
    #
    #     if tool_name == "read_file":
    #         path = tc.get("path", "")
    #         filename = path.split("/")[-1] if path else "unknown"
    #         return f"文件: {filename}"
    #
    #     elif tool_name == "write_file":
    #         path = tc.get("path", "")
    #         filename = path.split("/")[-1] if path else "unknown"
    #         return f"文件: {filename}"
    #
    #     elif tool_name == "task_create":
    #         try:
    #             task_dict = ast.literal_eval(content)
    #             return json.dumps({"action": "create", "task": task_dict}, ensure_ascii=False)
    #         except Exception:
    #             return ""
    #
    #     elif tool_name == "task_update":
    #         try:
    #             task_dict = ast.literal_eval(content)
    #             return json.dumps({"action": "update", "task": task_dict}, ensure_ascii=False)
    #         except Exception:
    #             return ""
    #
    #     return ""





    # async def _publish_request_event(
    #     self, session_state: RuntimeSessionState, event: AgentEvent
    # ) -> None:
    #     await self.message_bus.publish_outbound(
    #         RuntimeTurnEvent(
    #             session_id=session_state.session_id,
    #             request_id=session_state.request_id,
    #             turn_id=session_state.activate_turn.active_step_id,
    #             event_type=event.event_type,
    #             state=event.state.value,
    #             data=event.data,
    #         )
    #     )
    async def _publish_runtime_event(
        self,
        session_state: RuntimeSessionState,
        event: AgentEvent,
    ) -> None:
        # event结果整理后记录、发布
        self._sanitize_event_data(event)

        await self._record_chat_history(session_state, event)
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

    async def _fire(
        self, event: str, ctx: HookContext, *, parallel: bool = False, **data: Any
    ) -> HookContext:
        """复用 turn 级 ctx 触发 hook 事件：重置 event/data/action，保持 span 栈连续。"""
        ctx.event = event
        ctx.data = data
        ctx.action = HookAction.CONTINUE
        ctx.message = ""
        if parallel:
            return await hook_manager.emit_parallel(event, ctx)
        return await hook_manager.emit(event, ctx)

    @staticmethod
    def _last_assistant_content(session_state: RuntimeSessionState) -> str:
        """取最后一条 assistant 消息内容，作为 on_complete 的校验对象。"""
        chat = getattr(session_state.context_manager, "chat_messages", None) or []
        for message in reversed(chat):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return str(message.get("content") or "")
        return ""

    @staticmethod
    def _replace_last_assistant_content(session_state: RuntimeSessionState, content: str) -> None:
        """on_complete blocked 时以安全提示替换最终回复（后续 turn 的上下文同步净化）。"""
        chat = getattr(session_state.context_manager, "chat_messages", None) or []
        for message in reversed(chat):
            if isinstance(message, dict) and message.get("role") == "assistant":
                message["content"] = content
                return

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


# ── Hook 监听器：会话/turn 生命周期编排（docs/Hook设计.md §4.2） ─────────────
# 会话级对象只剩 SessionContext（纯状态）；落盘/任务看板走 SystemApp 无状态服务


@on(HookEvent.SESSION_START, name="init_session_objects", priority=10)
async def init_session_objects(ctx: HookContext) -> None:
    """初始化会话级对象（迁自 handlers.on_session_start）：

    SessionContext（纯会话状态）、加载聊天历史快照、
    用户消息入库、更新会话索引、state→RUNNING。
    """
    session = ctx.session
    req = session.request
    if req is None:
        log.warning("on_session_start: request is None, skipping")
        return
    from codegenx.ai_service.system_app import get_app

    session_io = get_app().session_io

    # P4 §10.3：会话归属智能体（请求 metadata.agent_name；空=默认智能体）
    session.agent_name = str((getattr(req, "metadata", None) or {}).get("agent_name", "") or "")

    session.context_manager = SessionContext(
        session_id=session.session_id,
        app_id=session.app_id,
        user_id=session.user_id,
        db_name=session.db_name,
        agent_name=session.agent_name,
    )
    # 加载上次聊天时的历史记录到内存
    session.context_manager.chat_messages = await session_io.get_turn_chat_message_snapshot(
        user_id=session.user_id, app_id=session.app_id, session_id=session.session_id
    ) or []
    user_dict = {"role": "user", "content": req.message}
    # 聊天消息入库（MySQL chat_message 表；失败不阻断对话）
    try:
        from codegenx.ai_service.chat_message import get_chat_message_store
        await get_chat_message_store().append_message(
            session.user_id, str(req.app_id), session.session_id, user_dict
        )
    except Exception as exc:
        log.warning("user 消息入库失败（不影响对话）: {}", exc)

    # 更新会话索引，供快速列出历史会话
    await session_io.upsert_session_index(
        req.message,
        user_id=session.user_id, app_id=session.app_id, session_id=session.session_id,
    )

    session.state = AgentState.RUNNING
    session.started_at = datetime.utcnow()


@on(HookEvent.TURN_END, name="persist_chat_snapshot", priority=10)
async def persist_chat_snapshot(ctx: HookContext) -> None:
    """保留上下文快照（迁自 handlers.on_turn_end 前半）。"""
    session = ctx.session
    if session.context_manager is not None:
        from codegenx.ai_service.system_app import get_app

        await get_app().session_io.save_turn_chat_message_snapshot(
            session.context_manager.chat_messages,
            user_id=session.user_id,
            app_id=session.app_id,
            session_id=session.session_id,
        )
