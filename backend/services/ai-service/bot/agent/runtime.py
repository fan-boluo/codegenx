import asyncio
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List
from enum import Enum
from pydantic import BaseModel, Field
from shared.constants import get_bot_code_dir
from shared.config.log_config import log
from bot.utils.config import AgentConfig, load_config
from bot.utils.context_utils import ensure_app_workdir, ensure_context_workdir

class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

class TurnContext(BaseModel):
    app_id: str = "main"
    session_id: str
    turn_id: str
    user_input: str
    workdir: str = ""
    state: AgentState = AgentState.IDLE
    history: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    transition_reason: str = ""
    tool_iteration_count: int = 0
    plan_state: str = ""
    token_budget: int = 0
    session_total_tokens: int = 0
    runtime_flags: Dict[str, Any] = Field(default_factory=dict)
    metrics: List[Dict[str, Any]] = Field(default_factory=list)
    recovery_state: Dict[str, Any] = Field(default_factory=dict)
    current_prompt_tokens: int = 0
    projected_total_tokens: int = 0
    last_llm_usage: Dict[str, Any] = Field(default_factory=dict)
    last_tool_result: Dict[str, Any] = Field(default_factory=dict)
    active_skill_names: List[str] = Field(default_factory=list)
    compaction_state: Dict[str, Any] = Field(default_factory=dict)
    
class AgentEvent(BaseModel):
    event_type: str
    data: Any = None
    state: AgentState

from bot.agent.hook.runner import HookRunner
from bot.agent.context_compaction import ContextCompactor
from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import ToolsHandler
from bot.agent.context import ContextAssembler, TurnReducer

from bot.agent.hook.registry import register_all_hooks

class AgentRuntime:
    CONTINUATION_MESSAGE = (
        "Output limit hit. Continue directly from where you stopped. "
        "Do not restart, recap, or repeat prior content."
    )

    def __init__(
        self,
        app_id: str = "main",
        hook_runner: HookRunner = None,
        context_assembler: ContextAssembler = None,
        tool_executor: ToolExecutor = None,
        turn_reducer: TurnReducer = None,
    ):
        self.app_id = app_id or "main"
        self.agent_config = self._resolve_agent_config()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.context_window_tokens = max(1, int(self.agent_config.context_window_tokens or 65_536))
        self.max_same_tool_calls = 3
        self.max_continuation_attempts = 3
        self.max_compact_attempts = 2
        self.max_transport_attempts = 3
        self.transport_backoff_base_seconds = 1.0
        self.transport_backoff_max_seconds = 8.0
        if hook_runner is None:
            self.hook_runner = HookRunner()
            register_all_hooks(self.hook_runner)
        else:
            self.hook_runner = hook_runner
            
        self.context_assembler = context_assembler or ContextAssembler()
        self.context_compactor = ContextCompactor()
        if tool_executor is None:
            app_code_dir = ensure_app_workdir(self.app_id)
            self.tool_executor = ToolExecutor(ToolsHandler(), safe_paths=[str(Path(app_code_dir))])
        else:
            self.tool_executor = tool_executor
        self.turn_reducer = turn_reducer or TurnReducer()
        self.openai_tools = self._build_openai_tools_spec()

    def _build_openai_tools_spec(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self.tool_executor.tools_handler.tools
        ]

    def _resolve_agent_config(self) -> AgentConfig:
        try:
            config = load_config()
            return config.get_agent(self.app_id)
        except Exception as exc:
            log.warning("Failed to load agent config, using defaults: {}", exc)
            return AgentConfig()

    def _tool_call_signature(self, tool_call: Dict[str, Any]) -> str:
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

    def _estimate_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        payload = json.dumps(messages, ensure_ascii=False, default=str)
        return self._estimate_text_tokens(payload)

    def _estimate_completion_tokens(self, llm_response: Dict[str, Any]) -> int:
        parts = [str(llm_response.get("content", "") or "")]
        tool_calls = llm_response.get("tool_calls", []) or []
        if tool_calls:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
        return self._estimate_text_tokens("\n".join(part for part in parts if part))

    @staticmethod
    def _refresh_runtime_flags(context: TurnContext) -> None:
        context.runtime_flags = {
            "session_initialized": bool(context.metadata.get("session_initialized")),
            "plan_state_locked": bool(context.metadata.get("plan_state_locked")),
            "memory_bootstrapped": bool(context.metadata.get("memory_bootstrapped")),
            "has_compacted": bool(context.compaction_state.get("has_compacted")),
        }
        context.metadata["runtime_flags"] = dict(context.runtime_flags)

    def _sync_context_state(self, context: TurnContext) -> None:
        if not context.plan_state:
            context.plan_state = str(context.metadata.get("plan_state", "") or "")
        else:
            context.plan_state = str(context.plan_state)
        context.transition_reason = str(context.transition_reason or "")
        if not context.recovery_state and isinstance(context.metadata.get("recovery_state"), dict):
            context.recovery_state = dict(context.metadata.get("recovery_state", {}))
        else:
            context.recovery_state = dict(context.recovery_state) if isinstance(context.recovery_state, dict) else {}
        if not context.metrics and isinstance(context.metadata.get("tool_metrics"), list):
            context.metrics = list(context.metadata.get("tool_metrics", []))
        else:
            context.metrics = list(context.metrics) if isinstance(context.metrics, list) else []
        context.session_total_tokens = int(context.session_total_tokens or 0)
        context.token_budget = int(context.token_budget or 0)

        active_skill_names = context.metadata.get("active_skill_names")
        if isinstance(active_skill_names, list):
            context.active_skill_names = [str(item) for item in active_skill_names if str(item).strip()]
        elif not context.active_skill_names:
            skill_catalog = context.metadata.get("skill_catalog", [])
            if isinstance(skill_catalog, list):
                context.active_skill_names = [
                    str(item.get("name", "")).strip()
                    for item in skill_catalog
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                ]

        self._sync_compaction_state(context)
        self._refresh_runtime_flags(context)

    def _set_transition_reason(self, context: TurnContext, reason: str) -> None:
        context.transition_reason = str(reason or "")
        self._refresh_runtime_flags(context)

    @staticmethod
    def _compaction_event_cursor(context: TurnContext) -> int:
        state = context.metadata.get("context_compaction")
        if not isinstance(state, dict):
            return 0
        events = state.get("events")
        if not isinstance(events, list):
            return 0
        return len(events)

    @staticmethod
    def _sync_compaction_state(context: TurnContext) -> None:
        state = context.metadata.get("context_compaction")
        if not isinstance(state, dict):
            return
        normalized_state = {
            key: value
            for key, value in state.items()
            if key != "events"
        }
        normalized_state.setdefault("has_compacted", False)
        normalized_state.setdefault("last_summary", "")
        normalized_state.setdefault("recent_files", [])
        normalized_state.setdefault("last_transcript_hash", "")
        normalized_state.setdefault("last_transcript_path", "")
        context.compaction_state = normalized_state
        context.metadata["compaction_state"] = normalized_state

    def _new_compaction_events(self, context: TurnContext, cursor: int) -> List[AgentEvent]:
        state = context.metadata.get("context_compaction")
        if not isinstance(state, dict):
            return []
        events = state.get("events")
        if not isinstance(events, list) or cursor >= len(events):
            self._sync_compaction_state(context)
            return []

        self._sync_compaction_state(context)
        return [
            AgentEvent(
                event_type="Compaction",
                data=dict(event),
                state=context.state,
            )
            for event in events[cursor:]
            if isinstance(event, dict)
        ]

    def _get_recovery_state(self, context: TurnContext) -> dict[str, Any]:
        recovery_state = context.recovery_state
        if isinstance(recovery_state, dict):
            recovery_state.setdefault("continuation_attempts", 0)
            recovery_state.setdefault("compact_attempts", 0)
            recovery_state.setdefault("transport_attempts", 0)
            recovery_state.setdefault("last_recovery_kind", "")
            recovery_state.setdefault("last_recovery_reason", "")
            return recovery_state

        recovery_state = {
            "continuation_attempts": 0,
            "compact_attempts": 0,
            "transport_attempts": 0,
            "last_recovery_kind": "",
            "last_recovery_reason": "",
        }
        context.recovery_state = recovery_state
        return recovery_state

    def _choose_recovery(self, stop_reason: str | None, error_text: str | None) -> dict[str, str]:
        normalized_stop_reason = str(stop_reason or "").strip().lower()
        normalized_error = str(error_text or "").strip().lower()

        if normalized_stop_reason in {"max_tokens", "length"}:
            return {"kind": "continue", "reason": "output truncated by model token limit"}

        prompt_too_long_markers = [
            "prompt too long",
            "context length",
            "maximum context length",
            "too many tokens",
            "context window",
            "reduce the length",
        ]
        if normalized_error and any(marker in normalized_error for marker in prompt_too_long_markers):
            return {"kind": "compact", "reason": "prompt exceeded model context budget"}

        transient_markers = ["timeout", "rate limit", "temporarily unavailable", "unavailable", "connection", "connection reset"]
        if normalized_error and any(marker in normalized_error for marker in transient_markers):
            return {"kind": "backoff", "reason": "transient upstream transport failure"}

        return {"kind": "fail", "reason": "non-recoverable or unknown failure"}

    def _append_assistant_message(self, context: TurnContext, llm_response: Dict[str, Any]) -> None:
        tool_calls = llm_response.get("tool_calls", [])
        if tool_calls:
            formatted_tool_calls = []
            for tc in tool_calls:
                formatted_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("arguments", {})) if isinstance(tc.get("arguments"), dict) else tc.get("arguments", "{}")
                    }
                })
            context.history.append({
                "role": "assistant",
                "content": llm_response.get("content", ""),
                "tool_calls": formatted_tool_calls,
            })
            return

        context.history.append({
            "role": "assistant",
            "content": llm_response.get("content", ""),
        })

    async def _apply_recovery(
        self,
        context: TurnContext,
        llm_response: Dict[str, Any],
        decision: dict[str, str],
    ) -> bool:
        recovery_state = self._get_recovery_state(context)
        kind = decision["kind"]
        reason = decision["reason"]

        if kind == "continue":
            if recovery_state["continuation_attempts"] >= self.max_continuation_attempts:
                raise RuntimeError("max_tokens recovery exhausted")
            recovery_state["continuation_attempts"] += 1
            recovery_state["last_recovery_kind"] = kind
            recovery_state["last_recovery_reason"] = reason
            self._set_transition_reason(context, reason)
            if llm_response.get("content") or llm_response.get("tool_calls"):
                self._append_assistant_message(context, llm_response)
            context.history.append({"role": "user", "content": self.CONTINUATION_MESSAGE})
            return True

        if kind == "compact":
            if recovery_state["compact_attempts"] >= self.max_compact_attempts:
                raise RuntimeError("context compaction recovery exhausted")
            recovery_state["compact_attempts"] += 1
            recovery_state["last_recovery_kind"] = kind
            recovery_state["last_recovery_reason"] = reason
            self._set_transition_reason(context, reason)
            await self.context_compactor.compact_history(
                context,
                focus="Recover from context overflow while preserving the current coding task state.",
                reason="recovery-context-too-large",
            )
            return True

        if kind == "backoff":
            if recovery_state["transport_attempts"] >= self.max_transport_attempts:
                raise RuntimeError("transport recovery exhausted")
            recovery_state["transport_attempts"] += 1
            recovery_state["last_recovery_kind"] = kind
            recovery_state["last_recovery_reason"] = reason
            self._set_transition_reason(context, reason)
            delay = min(
                self.transport_backoff_base_seconds * (2 ** (recovery_state["transport_attempts"] - 1)),
                self.transport_backoff_max_seconds,
            )
            await asyncio.sleep(delay)
            return True

        return False

    def _build_tool_history_result(self, context: TurnContext, tool_call_id: str, tool_name: str, result: Any) -> str:
        tool_result_overrides = context.metadata.setdefault("tool_result_overrides", {})
        history_content = tool_result_overrides.pop(tool_call_id, None)
        history_content = history_content if history_content is not None else str(result)
        return self.context_compactor.persist_large_output(context, tool_call_id, history_content)

    async def _record_tool_result(
        self,
        context: TurnContext,
        tool_call: Dict[str, Any],
        result: Any,
    ) -> AgentEvent:
        tool_call_id = tool_call.get("id")
        tool_name = tool_call.get("name")
        history_content = self._build_tool_history_result(context, tool_call_id, tool_name, result)
        context.history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": history_content,
            }
        )
        context.last_tool_result = {
            "tool_id": tool_call_id,
            "tool_name": tool_name,
            "result": history_content,
        }
        return AgentEvent(
            event_type="ToolExecutionEnd",
            data={"tool_id": tool_call_id, "result": history_content},
            state=context.state,
        )

    @staticmethod
    def _coerce_agent_state(value: Any, default: AgentState = AgentState.STOPPED) -> AgentState:
        if isinstance(value, AgentState):
            return value
        try:
            return AgentState(str(value or default.value))
        except ValueError:
            return default

    def _extract_hook_control(self, hook_result: Any) -> dict[str, Any]:
        if not isinstance(hook_result, dict):
            return {}

        control = hook_result.get("hook_control")
        if isinstance(control, dict):
            return control

        action = hook_result.get("action")
        if isinstance(action, str):
            return hook_result

        return {}

    @staticmethod
    def _coerce_message_list(value: Any) -> List[Dict[str, Any]] | None:
        if not isinstance(value, list):
            return None
        normalized: List[Dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                return None
            normalized.append(dict(item))
        return normalized

    @staticmethod
    def _coerce_tool_call(value: Any) -> Dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        normalized = dict(value)
        if not isinstance(normalized.get("name"), str) or not normalized.get("name"):
            return None
        arguments = normalized.get("arguments", {})
        if arguments is None:
            normalized["arguments"] = {}
        elif not isinstance(arguments, dict):
            return None
        return normalized

    def _apply_hook_control(
        self,
        event_name: str,
        context: TurnContext,
        hook_result: Any,
        messages: List[Dict[str, Any]] | None = None,
        tool_call: Dict[str, Any] | None = None,
    ) -> AgentEvent | None:
        control = self._extract_hook_control(hook_result)
        action = str(control.get("action", "") or "").strip().lower()
        if not action or action == "continue":
            return None

        message = str(control.get("message", "") or "")
        reason = str(control.get("reason", message or f"{event_name} hook requested {action}") or "")
        self._set_transition_reason(context, reason)

        if action == "block":
            if message:
                context.history.append({"role": "assistant", "content": message})
            context.state = self._coerce_agent_state(control.get("state"), AgentState.STOPPED)

        if action == "inject" and message:
            injected_message = {
                "role": "user",
                "content": f"<system-reminder>\n{message}\n</system-reminder>",
            }
            if messages is not None:
                messages.append(injected_message)
            else:
                context.metadata["system_reminder"] = message

        if action == "override":
            override_data = control.get("data") if isinstance(control.get("data"), dict) else {}
            replacement_messages = self._coerce_message_list(override_data.get("messages"))
            if replacement_messages is not None and messages is not None:
                messages[:] = replacement_messages

            replacement_tool_call = self._coerce_tool_call(override_data.get("tool_call"))
            if replacement_tool_call is not None and tool_call is not None:
                original_id = tool_call.get("id")
                tool_call.clear()
                tool_call.update(replacement_tool_call)
                tool_call.setdefault("id", original_id)

        return AgentEvent(
            event_type="HookControl",
            data={
                "event": event_name,
                "action": action,
                "reason": reason,
                "message": message or None,
            },
            state=context.state,
        )

    async def run_turn(self, context: TurnContext) -> AsyncGenerator[AgentEvent, None]:
        context.app_id = context.app_id or self.app_id
        ensure_context_workdir(context)
        context.state = AgentState.RUNNING
        if context.token_budget <= 0:
            context.token_budget = self.context_window_tokens
        context.metadata.setdefault("tool_result_overrides", {})
        context.metadata["context_compactor"] = self.context_compactor
        self._get_recovery_state(context)
        self._sync_context_state(context)
        tool_iterations = 0
        last_tool_signature: str | None = None
        consecutive_same_tool_calls = 0
        turn_finished = False
        
        # Note SessionStart if it is the first time running
        if not context.metadata.get("session_initialized"):
            await self.hook_runner.dispatch('OnSessionStart', context)
            context.metadata["session_initialized"] = True
            self._sync_context_state(context)
        
        # Append initial user input to history if it's the start
        if context.user_input and (not context.history or context.history[-1].get("content") != context.user_input):
            context.history.append({"role": "user", "content": context.user_input})
            
        on_turn_start_result = await self.hook_runner.dispatch('OnTurnStart', context) or {}
        self._sync_context_state(context)
        yield AgentEvent(event_type="OnTurnStart", state=context.state)
        on_turn_start_control = self._apply_hook_control("OnTurnStart", context, on_turn_start_result)
        if on_turn_start_control is not None:
            yield on_turn_start_control
        
        try:
            while context.state == AgentState.RUNNING and not turn_finished:
                prepare_compaction_cursor = self._compaction_event_cursor(context)
                await self.context_compactor.prepare_for_llm(context)
                for compaction_event in self._new_compaction_events(context, prepare_compaction_cursor):
                    yield compaction_event
                messages = await self.context_assembler.assemble(context)
                prompt_tokens = self._estimate_message_tokens(messages)
                context.current_prompt_tokens = prompt_tokens
                context.projected_total_tokens = context.session_total_tokens + prompt_tokens
                pre_llm_result = await self.hook_runner.dispatch(
                    'PreLLMCall',
                    context,
                    messages=messages,
                    prompt_tokens=prompt_tokens,
                    projected_total_tokens=context.projected_total_tokens,
                    token_budget=context.token_budget,
                ) or {}
                pre_llm_control = self._apply_hook_control("PreLLMCall", context, pre_llm_result, messages=messages)
                if pre_llm_control is not None:
                    yield pre_llm_control
                    if context.state != AgentState.RUNNING:
                        turn_finished = True
                        break
                yield AgentEvent(
                    event_type="LLM_Thinking_Start",
                    data={"prompt_tokens": prompt_tokens, "message_count": len(messages)},
                    state=context.state,
                )

                # Assemble final payload
                from bot.llm.async_client import AsyncLLMClient
                llm_client = AsyncLLMClient()
                
                llm_response = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [],
                    "finish_reason": None,
                }
                
                try:
                    async for chunk in llm_client.invoke_stream(messages, self.openai_tools):
                        if chunk["type"] == "content":
                            llm_response["content"] += chunk["data"]
                            yield AgentEvent(event_type="LLM_Response_Chunk", data=chunk["data"], state=context.state)
                        elif chunk["type"] == "tool_calls":
                            llm_response["tool_calls"] = chunk["data"]
                        elif chunk["type"] == "response_info":
                            llm_response["finish_reason"] = (chunk.get("data") or {}).get("finish_reason")
                except Exception as e:
                    decision = self._choose_recovery(None, str(e))
                    yield AgentEvent(event_type="RecoveryDecision", data=decision, state=context.state)
                    recovery_compaction_cursor = self._compaction_event_cursor(context)
                    if await self._apply_recovery(context, llm_response, decision):
                        for compaction_event in self._new_compaction_events(context, recovery_compaction_cursor):
                            yield compaction_event
                        continue
                    log.error("LLM call failed: {}", e)
                    raise

                completion_tokens = self._estimate_completion_tokens(llm_response)
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
                context.last_llm_usage = dict(usage)
                context.session_total_tokens += usage["total_tokens"]

                recovery_decision = self._choose_recovery(llm_response.get("finish_reason"), None)
                if recovery_decision["kind"] != "fail":
                    yield AgentEvent(event_type="RecoveryDecision", data=recovery_decision, state=context.state)
                    recovery_compaction_cursor = self._compaction_event_cursor(context)
                    if await self._apply_recovery(context, llm_response, recovery_decision):
                        for compaction_event in self._new_compaction_events(context, recovery_compaction_cursor):
                            yield compaction_event
                        continue
                    
                tool_calls = llm_response.get("tool_calls", [])
                self._append_assistant_message(context, llm_response)

                await self.hook_runner.dispatch('PostLLMCall', context, response=llm_response, usage=usage, messages=messages)
                
                tool_calls = llm_response.get("tool_calls", [])
                
                if not tool_calls:
                    context.state = AgentState.COMPLETED
                    turn_finished = True
                    break
                
                for tool_call in tool_calls:
                    if context.state != AgentState.RUNNING:
                        turn_finished = True
                        break

                    tool_name = tool_call.get("name")
                    tool_iterations += 1
                    context.tool_iteration_count = tool_iterations
                    if tool_iterations > self.max_tool_iterations:
                        raise RuntimeError(
                            f"Agent exceeded max tool iterations ({self.max_tool_iterations}) for one turn"
                        )

                    pre_tool_result = await self.hook_runner.dispatch('PreToolUse', context, tool_call=tool_call) or {}
                    pre_tool_control = self._apply_hook_control(
                        "PreToolUse",
                        context,
                        pre_tool_result,
                        tool_call=tool_call,
                    )
                    if pre_tool_control is not None:
                        yield pre_tool_control
                    tool_name = tool_call.get("name")
                    tool_call_id = tool_call.get("id")
                    tool_signature = self._tool_call_signature(tool_call)
                    if tool_signature == last_tool_signature:
                        consecutive_same_tool_calls += 1
                    else:
                        consecutive_same_tool_calls = 1
                        last_tool_signature = tool_signature

                    context.metadata["consecutive_same_tool_calls"] = consecutive_same_tool_calls
                    if consecutive_same_tool_calls >= self.max_same_tool_calls:
                        raise RuntimeError(
                            f"Agent repeated the same tool call {consecutive_same_tool_calls} times without making progress: {tool_name}"
                        )

                    yield AgentEvent(event_type="ToolExecutionStart", data=tool_call, state=context.state)

                    try:
                        tool_compaction_cursor = self._compaction_event_cursor(context)
                        result = await self.tool_executor.execute(tool_call, context)
                    except Exception as e:
                        log.error("Tool execution failed: {}", e)
                        result = {"error": str(e)}
                    for compaction_event in self._new_compaction_events(context, tool_compaction_cursor):
                        yield compaction_event

                    await self.hook_runner.dispatch('PostToolUse', context, tool_call=tool_call, result=result)
                    self._sync_context_state(context)
                    yield await self._record_tool_result(context, tool_call, result)

                    if context.state != AgentState.RUNNING:
                        turn_finished = True
                        break
                
            await self.turn_reducer.reduce(context)
            finalize_compaction_cursor = self._compaction_event_cursor(context)
            await self.context_compactor.finalize_turn(context)
            for compaction_event in self._new_compaction_events(context, finalize_compaction_cursor):
                yield compaction_event
            await self.hook_runner.dispatch('OnTurnEnd', context)
            self._sync_context_state(context)
            if context.state == AgentState.RUNNING:
                context.state = AgentState.COMPLETED
            yield AgentEvent(event_type="TurnCompleted", state=context.state)
            
            if context.state in (AgentState.FAILED, AgentState.STOPPED):
                await self.hook_runner.dispatch('OnSessionEnd', context)

        except Exception as e:
            log.exception("Turn crashed")
            context.state = AgentState.FAILED
            await self.hook_runner.dispatch('OnError', context, error=e)
            yield AgentEvent(event_type="Error", data=str(e), state=context.state)
            await self.hook_runner.dispatch('OnSessionEnd', context)
