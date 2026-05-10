from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from agent.plan.planner import Planner
from agent.runtime_schema import RuntimeSessionState, TurnContext
from bot.agent.context import ContextAssembler, TurnReducer
from bot.agent.hook.registry import register_all_hooks
from bot.agent.hook.runner import HookRunner
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import get_tool_registry
from bot.bus import MessageBus, RuntimeTurnEvent
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
from monitor.telemetry_schema import RequestTelemetry, SessionTelemetry, TelemetryStatus, TurnTelemetry
from shared.config.log_config import log
from shared.constants import get_bot_code_dir
from shared.schema.ai_service import AiServiceGenerateRequest



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

        self.config = load_config()
        self.agent_config = self._resolve_agent_config()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = 3
        self.stop_grace_seconds = max(0.0, float(self.agent_config.session_stop_grace_seconds or 2.0))

        self.message_bus = message_bus or MessageBus()
        # self.context_assembler = context_assembler or ContextAssembler()
        self.turn_reducer = turn_reducer or TurnReducer()
        self.tool_registry = get_tool_registry()
        self.tool_executor = tool_executor or ToolExecutor(self.tool_registry)
        self.planner = Planner()
        self.skill_loader = SkillLoader()
        self.skills = self.skill_loader.load_all_skills()

        log.info("共加载{}个工具", len(self.tool_registry.tools))
        log.info("共加载{}个skill", len(self.skills) if self.skills else 0)

        try:
            self.memory_manager = get_memory_manager()
            self.memory_manager.warm_up()
            log.info("启动 warm_up_memory_manager ready")
        except Exception as exc:

            log.warning("AgentRuntime startup failed to warm MemoryManager: {}", exc)

        self.hook_runner = hook_runner or HookRunner()
        if hook_runner is None:
            register_all_hooks(self.hook_runner)
        self._dispatcher_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._session_states: dict[str, RuntimeSessionState] = {}
        self._session_lock = asyncio.Lock()
        # 一些初始化操作,后台服务
        self.startup_backup_service()
        # self._tool_catalog = [
        #     tool for tool in self.tool_executor.tools_handler.tools if tool.name not in self.EXCLUDED_TOOL_NAMES
        # ]

    def _resolve_agent_config(self) -> AgentConfig:
        try:
            return self.config.get_default_agent()
        except Exception as exc:
            log.warning("Failed to load runtime config, using defaults: {}", exc)
            return AgentConfig()

    async def start(self) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._shutdown_event.clear()
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop(), name="agent-runtime-dispatcher")
        log.info("启动完成 dispatch_loop,等待消息。。。")

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

    async def submit_request(self, request: AiServiceGenerateRequest) -> AsyncGenerator[AgentEvent, None]:
        """
        完成一次请求，消息收发
        """
        await self.start()
        request_id = self._request_id(request)
        subscriber = self.message_bus.subscribe_request(request_id)
        try:
            await self.message_bus.publish_inbound(request)
            log.info("message_bus 输送请求：{}",request_id)
            while True:
                item = await subscriber.get()
                if not isinstance(item, RuntimeTurnEvent):
                    continue
                event = AgentEvent(event_type=item.event_type, data=item.data, state=AgentState(item.state))
                yield event
                if event.event_type in {"RequestCompleted", "RequestStopped", "Error"}:
                    await self._wait_for_request_cleanup(str(request.session_id or ""), request_id)
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
        session_state: RuntimeSessionState | None = None
        active_tasks: list[tuple[str, asyncio.Task[Any]]] = []
        dropped_requests: list[AiServiceGenerateRequest] = []
        remaining_requests: list[AiServiceGenerateRequest] = []
        stop_reason = str(reason or "user-stop")
        target_request_id = str(request_id or "").strip()

        async with self._session_lock:
            session_state = self._session_states.get(session_id)
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
                active_tasks = [(target_request_id, session_state.active_tasks[target_request_id])]
            while True:
                try:
                    queued_request = session_state.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(queued_request, AiServiceGenerateRequest):
                    if self._request_id(queued_request) == target_request_id:
                        dropped_requests.append(queued_request)
                    else:
                        remaining_requests.append(queued_request)

            for queued_request in remaining_requests:
                await session_state.queue.put(queued_request)

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
            if current_state is not None and not current_state.closed:
                current_state.stop_signal.clear()
                current_state.stop_reason = ""

        current_state = self._session_states.get(session_id)
        active_turn_ids: list[str] = []
        if current_state is not None:
            for active_request_id, _ in active_tasks:
                request_state = current_state.active_requests.get(active_request_id)
                if request_state is not None and request_state.active_turn_id:
                    active_turn_ids.append(request_state.active_turn_id)

        return {
            "accepted": bool(active_tasks or dropped_requests),
            "sessionId": session_id,
            "stoppedRequestCount": len(active_tasks),
            "droppedRequestCount": len(dropped_requests),
            "activeRequestIds": [active_request_id for active_request_id, _ in active_tasks],
            "droppedRequestIds": [str(request.request_id or "") for request in dropped_requests],
            "activeTurnIds": active_turn_ids,
        }

    async def _dispatch_loop(self) -> None:
        while not self._shutdown_event.is_set():
            request = await self.message_bus.consume_inbound()
            log.info("dispatcher loop 接收到请求：{} {}", request.request_id,str(getattr(request, "message", "") or "")[:10])
            if not isinstance(request, AiServiceGenerateRequest):
                continue
            session_state = await self._get_or_create_session_state(request)
            # request加入session的queue
            await session_state.queue.put(request)
            log.debug("{} 加入到session的消息队列",request.request_id)


    async def _get_or_create_session_state(self, request: AiServiceGenerateRequest) -> RuntimeSessionState:
        async with self._session_lock:
            session_id = str(request.session_id or "")
            session_state = self._session_states.get(session_id)
            if session_state is not None and not session_state.closed:
                session_state.request = request
                session_state.touch()
                return session_state

            session_state = RuntimeSessionState(
                session_id=session_id,
                request=request,
                runtime=self
            )
            context = TurnContext()
            # TODO
            await self.hook_runner.dispatch("OnSessionStart", session_state,context)

            session_state.worker_task = asyncio.create_task(
                self._session_worker(session_state),
                name=f"agent-session-{session_id}",
            )
            log.info("创建session 循环：{}",session_id)
            self._session_states[session_id] = session_state

            return session_state

    async def _wait_for_request_cleanup(self, session_id: str, request_id: str) -> None:
        while True:
            async with self._session_lock:
                session_state = self._session_states.get(session_id)
                if session_state is None or request_id not in session_state.active_tasks:
                    return
            await asyncio.sleep(0)

    async def _session_worker(self, session_state: RuntimeSessionState) -> None:
        session_state.state = AgentState.RUNNING
        while not self._shutdown_event.is_set() and not session_state.closed:
            log.debug("进入session 循环：{}", session_state.session_id)
            try:
                request = await asyncio.wait_for(
                    session_state.queue.get(),
                    timeout=max(1, int(self.agent_config.session_worker_idle_seconds or 1800)),
                )
                log.debug("session loop 获取到请求：{}",request.request_id)
            except asyncio.TimeoutError:
                await self._close_session_state(session_state, end_reason="idle-timeout")
                return
            except asyncio.CancelledError:
                await self._close_session_state(session_state, end_reason="runtime-stop")
                raise

            request_state = await self._build_request_state(session_state, request)
            session_state.active_requests[request_state.request_id] = request_state
            request_task = asyncio.create_task(
                self._execute_request(session_state, request_state),
                name=f"agent-request-{request_state.request_id}",
            )
            log.debug("请求任务开始执行:{}",request_state.request_id)
            session_state.active_tasks[request_state.request_id] = request_task
            try:
                await request_task
            except Exception as exc:
                log.exception("Request crashed for session {} request {}", session_state.session_id, request_state.request_id)
                await self._finalize_worker_request_error(session_state, request_state, exc)
            except asyncio.CancelledError:
                request_task.cancel()
                with suppress(asyncio.CancelledError):
                    await request_task
                await self._finalize_cancelled_request(session_state, request_state, reason="runtime-stop")
                await self._close_session_state(session_state, end_reason="runtime-stop")
                raise
            finally:
                session_state.active_tasks.pop(request_state.request_id, None)
                session_state.active_requests.pop(request_state.request_id, None)
                if not session_state.closed:
                    session_state.stop_signal.clear()
                    session_state.stop_reason = ""
                session_state.touch()
                session_state.session_manager.save_history(session_state.session_id, request_state.context.chat_history)

    async def _close_session_state(self, session_state: RuntimeSessionState, *, end_reason: str) -> None:
        if session_state.closed:
            return
        session_state.closed = True
        if end_reason == "runtime-stop" and session_state.active_turns:
            session_state.state = AgentState.STOPPED
        elif session_state.state == AgentState.FAILED:
            session_state.state = AgentState.FAILED
        else:
            session_state.state = AgentState.COMPLETED
        if session_state.worker_task is not None and session_state.worker_task.cancelled():
            session_state.worker_task = None
        await self.hook_runner.dispatch("OnSessionEnd", session_state, end_reason=end_reason)
        async with self._session_lock:
            self._session_states.pop(session_state.session_id, None)

    async def _build_request_state(self, session_state: RuntimeSessionState, request: AiServiceGenerateRequest) -> RuntimeRequestState:
        session_state.request = request
        return RuntimeRequestState(
            request=request,
            request_id=self._request_id(request),
            context=TurnContext(user_input=str(request.message or "")),
            state=AgentState.IDLE,
        )

    def _build_turn_id(self, request_state: RuntimeRequestState) -> str:
        return f"{request_state.request_id}-turn-{request_state.turn_counter:04d}"

    async def _build_turn_state(self, session_state: RuntimeSessionState, request_state: RuntimeRequestState) -> RuntimeTurnState:
        session_state.turn_counter += 1
        request_state.turn_counter += 1
        turn_context = TurnContext(
            build_tool=deepcopy(request_state.context.build_tool),
            skill=deepcopy(request_state.context.skill),
            memory=list(request_state.context.memory),
            system_prompt=request_state.context.system_prompt,
            chat_history=deepcopy(request_state.context.chat_history),
            user_input=request_state.context.user_input,
        )
        return RuntimeTurnState(
            request=request_state.request,
            request_state=request_state,
            turn_id=self._build_turn_id(request_state),
            turn_number=session_state.turn_counter,
            context=turn_context,
            code_dir=request_state.code_dir,
            safe_paths=list(request_state.safe_paths),
            workspace_metadata=deepcopy(request_state.workspace_metadata),
            knowledge_cache=deepcopy(request_state.knowledge_cache),
            prompt_template=request_state.prompt_template,
            plan_summary=request_state.plan_summary,
            state=AgentState.IDLE,
        )

    async def _execute_request(self, session_state: RuntimeSessionState, request_state: RuntimeRequestState) -> None:
        """
        处理单个请求的任务
        """
        session_state.request = request_state.request
        request_state.state = AgentState.RUNNING
        request_state.started_at = time.time()
        await self.hook_runner.dispatch("OnTurnStart", session_state, request_state=request_state)

        try:
            while request_state.state == AgentState.RUNNING:
                self._raise_if_stop_requested(session_state)
                turn_state = await self._build_turn_state(session_state, request_state)
                session_state.active_turns[turn_state.turn_id] = turn_state
                request_state.active_turn_id = turn_state.turn_id
                try:
                    await self._execute_turn(session_state, request_state, turn_state)
                finally:
                    request_state.context.chat_history = deepcopy(turn_state.context.chat_history)
                    request_state.context.user_input = turn_state.context.user_input
                    session_state.active_turns.pop(turn_state.turn_id, None)
                    request_state.active_turn_id = ""

                if turn_state.state == AgentState.STOPPED:
                    request_state.state = AgentState.STOPPED
                    break
                if turn_state.state == AgentState.FAILED:
                    request_state.state = AgentState.FAILED
                    request_state.error_text = turn_state.error_text
                    session_state.state = AgentState.FAILED
                    break
                if not turn_state.requires_followup:
                    request_state.state = AgentState.COMPLETED
                    break

            request_state.finished_at = time.time()
            if request_state.state == AgentState.COMPLETED:
                await self._persist_request_metrics(session_state, request_state, end_reason="completed")
                await self._publish_request_event(
                    session_state,
                    request_state,
                    AgentEvent(
                        event_type="RequestCompleted",
                        data={"request_id": request_state.request_id},
                        state=AgentState.COMPLETED,
                    ),
                )
            elif request_state.state == AgentState.STOPPED:
                await self._persist_request_metrics(
                    session_state,
                    request_state,
                    end_reason=self._stop_reason(session_state),
                )
                await self._publish_request_event(
                    session_state,
                    request_state,
                    AgentEvent(
                        event_type="RequestStopped",
                        data={"request_id": request_state.request_id, "reason": self._stop_reason(session_state)},
                        state=AgentState.STOPPED,
                    ),
                )
            else:
                await self._persist_request_metrics(
                    session_state,
                    request_state,
                    end_reason=request_state.error_text or "failed",
                )
        except TurnStoppedError:
            request_state.state = AgentState.STOPPED
            request_state.finished_at = time.time()
            await self._persist_request_metrics(
                session_state,
                request_state,
                end_reason=self._stop_reason(session_state),
            )
            await self._publish_request_event(
                session_state,
                request_state,
                AgentEvent(
                    event_type="RequestStopped",
                    data={"request_id": request_state.request_id, "reason": self._stop_reason(session_state)},
                    state=AgentState.STOPPED,
                ),
            )
        except asyncio.CancelledError:
            request_state.state = AgentState.STOPPED
            request_state.finished_at = time.time()
            await self._persist_request_metrics(
                session_state,
                request_state,
                end_reason=self._stop_reason(session_state),
            )
            await self._publish_request_event(
                session_state,
                request_state,
                AgentEvent(
                    event_type="RequestStopped",
                    data={"request_id": request_state.request_id, "reason": self._stop_reason(session_state)},
                    state=AgentState.STOPPED,
                ),
            )
            raise
        except Exception as exc:
            request_state.state = AgentState.FAILED
            request_state.error_text = str(exc)
            request_state.finished_at = time.time()
            session_state.state = AgentState.FAILED
            await self._persist_request_metrics(session_state, request_state)
            await self._publish_request_event(
                session_state,
                request_state,
                AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED),
            )

    async def _execute_turn(
        self,
        session_state: RuntimeSessionState,
        request_state: RuntimeRequestState,
        turn_state: RuntimeTurnState,
    ) -> None:
        turn_state.state = AgentState.RUNNING
        turn_state.started_at = time.time()

        try:
            self._raise_if_stop_requested(session_state)

            await self.hook_runner.dispatch("OnTurnStart", turn_state, session=session_state)
            on_turn_start = AgentEvent(
                event_type="OnTurnStart",
                data={"request_id": request_state.request_id, "turn_id": turn_state.turn_id},
                state=AgentState.RUNNING,
            )
            await self._publish_runtime_event(session_state, turn_state, on_turn_start)
            llm_response = {"content": "", "tool_calls": [], "finish_reason": None}

            self.context_assembler.ensure_user_message(turn_state.context)
            self._raise_if_stop_requested(session_state)
            messages = await self.context_assembler.assemble(turn_state.context)
            prompt_tokens = self._estimate_message_tokens(messages)
            session_telemetry = session_state.telemetry
            projected_total_tokens = prompt_tokens
            if session_telemetry is not None:
                projected_total_tokens += (
                    int(session_telemetry.total_prompt_tokens or 0)
                    + int(session_telemetry.total_completion_tokens or 0)
                )
            await self.hook_runner.dispatch(
                "PreLLMCall",
                turn_state,
                session=session_state,
                messages=messages,
                prompt_tokens=prompt_tokens,
                projected_total_tokens=projected_total_tokens,
                tool_catalog=turn_state.context.build_tool,
            )
            await self._publish_runtime_event(
                session_state,
                turn_state,
                AgentEvent(
                    event_type="LLM_Thinking_Start",
                    data={
                        "request_id": request_state.request_id,
                        "turn_id": turn_state.turn_id,
                        "prompt_tokens": prompt_tokens,
                        "message_count": len(messages),
                    },
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
                    event = AgentEvent(
                        event_type="LLM_Response_Chunk",
                        data=chunk["data"],
                        state=AgentState.RUNNING,
                    )
                    await self._publish_runtime_event(session_state, turn_state, event)
                elif chunk["type"] == "tool_calls":
                    llm_response["tool_calls"] = chunk["data"]
                elif chunk["type"] == "response_info":
                    llm_response["finish_reason"] = (chunk.get("data") or {}).get("finish_reason")

            completion_tokens = self._estimate_completion_tokens(llm_response)
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            if session_telemetry is not None:
                session_telemetry.total_prompt_tokens += usage["prompt_tokens"]
                session_telemetry.total_completion_tokens += usage["completion_tokens"]
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
                request_state.tool_iterations += 1
                if request_state.tool_iterations > self.max_tool_iterations:
                    raise RuntimeError(f"Agent exceeded max tool iterations ({self.max_tool_iterations}) for one request")

                signature = self._tool_call_signature(tool_call)
                if signature == request_state.last_tool_signature:
                    request_state.consecutive_same_tool_calls += 1
                else:
                    request_state.last_tool_signature = signature
                    request_state.consecutive_same_tool_calls = 1
                if request_state.consecutive_same_tool_calls >= self.max_same_tool_calls:
                    raise RuntimeError(
                        f"Agent repeated the same tool call {request_state.consecutive_same_tool_calls} times without making progress: {tool_call.get('name')}"
                    )

                await self.hook_runner.dispatch("PreToolUse", turn_state, session=session_state, tool_call=tool_call)
                start_event = AgentEvent(event_type="ToolExecutionStart", data=tool_call, state=AgentState.RUNNING)
                await self._publish_runtime_event(session_state, turn_state, start_event)
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

            await self.turn_reducer.reduce(turn_state.context)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            turn_state.state = AgentState.COMPLETED
            completed_event = AgentEvent(
                event_type="TurnCompleted",
                data={
                    "request_id": request_state.request_id,
                    "turn_id": turn_state.turn_id,
                    "requires_followup": turn_state.requires_followup,
                },
                state=AgentState.COMPLETED,
            )
            await self._publish_runtime_event(session_state, turn_state, completed_event)
        except TurnStoppedError as exc:
            turn_state.state = AgentState.STOPPED
            turn_state.transition_reason = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            stopped_event = self._build_stopped_event(turn_state, reason=str(exc))
            await self._publish_runtime_event(session_state, turn_state, stopped_event)
        except asyncio.CancelledError:
            reason = self._stop_reason(session_state)
            turn_state.state = AgentState.STOPPED
            turn_state.transition_reason = reason
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            stopped_event = self._build_stopped_event(turn_state, reason=reason)
            await self._publish_runtime_event(session_state, turn_state, stopped_event)
        except Exception as exc:
            turn_state.state = AgentState.FAILED
            turn_state.error_text = str(exc)
            turn_state.finished_at = time.time()
            await self.hook_runner.dispatch("OnError", turn_state, session=session_state, error=exc)
            await self.hook_runner.dispatch("OnTurnEnd", turn_state, session=session_state)
            error_event = AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED)
            await self._publish_runtime_event(session_state, turn_state, error_event)

    async def _publish_stopped_request(self, request: AiServiceGenerateRequest, *, reason: str) -> None:
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
            data={"reason": reason, "turn_id": turn_state.turn_id, "request_id": turn_state.request_state.request_id},
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
                request_id=turn_state.request_state.request_id,
                turn_id=turn_state.turn_id,
                event_type=event.event_type,
                state=event.state.value,
                data=event.data,
            )
        )

    async def _publish_request_event(
        self,
        session_state: RuntimeSessionState,
        request_state: RuntimeRequestState,
        event: AgentEvent,
    ) -> None:
        await self.message_bus.publish_outbound(
            RuntimeTurnEvent(
                session_id=session_state.session_id,
                request_id=request_state.request_id,
                turn_id=request_state.active_turn_id,
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

    async def _finalize_worker_request_error(self, session_state: RuntimeSessionState, request_state: RuntimeRequestState, exc: Exception) -> None:
        request_state.state = AgentState.FAILED
        request_state.error_text = str(exc)
        request_state.finished_at = time.time()
        session_state.state = AgentState.FAILED
        await self._persist_request_metrics(session_state, request_state)
        await self._publish_request_event(
            session_state,
            request_state,
            AgentEvent(event_type="Error", data=str(exc), state=AgentState.FAILED),
        )

    async def _finalize_cancelled_request(self, session_state: RuntimeSessionState, request_state: RuntimeRequestState, *, reason: str) -> None:
        if request_state.finished_at > 0:
            return

        request_state.state = AgentState.STOPPED
        request_state.finished_at = time.time()
        await self._persist_request_metrics(session_state, request_state, end_reason=reason)
        await self._publish_request_event(
            session_state,
            request_state,
            AgentEvent(
                event_type="RequestStopped",
                data={"request_id": request_state.request_id, "reason": reason},
                state=AgentState.STOPPED,
            ),
        )

    @staticmethod
    def _request_id(request: AiServiceGenerateRequest) -> str:
        return str(getattr(request, "request_id", "") or "")

    @staticmethod
    def _telemetry_status_for_request(state: AgentState) -> TelemetryStatus:
        if state == AgentState.FAILED:
            return TelemetryStatus.ERROR
        if state == AgentState.STOPPED:
            return TelemetryStatus.STOPPED
        if state == AgentState.COMPLETED:
            return TelemetryStatus.SUCCESS
        return TelemetryStatus.RUNNING

    async def _persist_request_metrics(
        self,
        session_state: RuntimeSessionState,
        request_state: RuntimeRequestState,
        *,
        end_reason: str | None = None,
    ) -> None:
        request_telemetry = RequestTelemetry(
            trace_id=str(getattr(request_state.request, "trace_id", "") or session_state.trace_id or ""),
            session_id=session_state.session_id,
            request_id=request_state.request_id,
            app_id=session_state.app_id,
            user_id=session_state.user_id,
            model=getattr(self.agent_config, "resolved_model_name", "unknown"),
            token_budget=int(getattr(session_state.telemetry, "token_budget", 0) or 0),
            started_at=datetime.utcfromtimestamp(request_state.started_at) if request_state.started_at else None,
            ended_at=datetime.utcfromtimestamp(request_state.finished_at) if request_state.finished_at else None,
            status=self._telemetry_status_for_request(request_state.state),
            end_reason=str(end_reason or self._stop_reason(session_state) or request_state.state.value or "completed"),
        )
        await get_monitor_pipeline().persist_request_metrics(request_telemetry)


    async def startup_backup_service(self) -> None:
        try:
            await warm_up_mysql_pool()
        except Exception as exc:
            log.warning("AgentRuntime startup failed to warm MySQL pool: {}", exc)

        try:
            await warm_up_qdrant_client()
            log.info("启动 warm_up_qdrant_client ready")
        except Exception as exc:
            log.warning("AgentRuntime startup failed to warm Qdrant client: {}", exc)

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

                log.info(f"启动 {initializer_name} ready")
            except Exception as exc:
                log.warning("AgentRuntime startup failed to initialize {}: {}", initializer_name, exc)
