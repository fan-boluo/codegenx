
from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any
from codegenx.ai_service.agent.agent_schema import  AgentState,AgentEventType
from codegenx.ai_service.agent.runtime_schema import (
    RuntimeSessionState,
    TurnStoppedError, ActivateTurn,
)
from codegenx.ai_service.llm.llm_recovery import invoke_stream_with_revovery
from codegenx.ai_service.hook import HookAction, HookContext, HookEvent, hook_manager
from codegenx.ai_service.utils.config import AgentConfig, config
from codegenx.ai_service.system_app import get_app
from shared import log
from codegenx.ai_service.compact.thresholds import estimate_tokens as _thresholds_estimate


class AgentRuntime:
    """全局对话引擎（进程级组件）：session pool + dispatcher + turn 编排。
    旧版迁移中，目前剩余的方法留作迁移对照使用，待全部验证通过后删除此类
    """

    def __init__(
        self,
    ):
        self.config = config
        self.agent_config = self.config.get_default_agent() or AgentConfig()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = self.agent_config.max_same_tool_calls or 3

        self.max_steps = self.agent_config.max_steps

        # 初始化即解析默认智能体模型链（组件自持 LLM 资源；会话级覆盖见 resolve_agent_chain）
        model = (self.agent_config.resolved_model_name or "").strip() \
            or self.config.get_default_model()
        self.model_chain: list[str] = [model]

        self.message_bus = get_app().message_bus
        self.tool_registry = get_app().tool_registry
        self.tool_executor = get_app().tools_executor
        log.info("共加载{}个工具", len(self.tool_registry.tool_registry))

    async def _execute_request(self, session_state: RuntimeSessionState) -> None:
        """Process all turns. Always publishes a terminal event; re-raises CancelledError."""

        request_id = session_state.request_id
        activate_turn = session_state.activate_turn
        activate_turn.state = AgentState.RUNNING
        activate_turn.started_at = time.time()

        # turn 级 hook 上下文：整个 turn 复用同一 ctx）
        hook_ctx = HookContext(
            event=HookEvent.TURN_START, session=session_state, turn=activate_turn
        )
        await hook_manager.emit(HookEvent.TURN_START, hook_ctx)
        try:
            # 加入聊天历史
            user_message = session_state.request.message
            context_manager = session_state.context_manager
            if context_manager is None:
                raise RuntimeError("context_manager is not initialized — on_session_start hook may not have run")
            context_manager.add_user_message(user_message)
            # await context_manager.build_system_prompt(user_message)

            await self.message_bus.publish_runtime_event(
                session_state.publish_context,event_type=AgentEventType.ON_TURN_START,
                state=activate_turn.state)
            log.debug("{},{},{}",request_id,activate_turn.step_counter," 发送事件 OnTurnStart")
            # 执行turn的任务
            while activate_turn.step_counter < self.max_steps:
                self._raise_if_stop_requested(session_state)
                # 聊天历史微压，清除工具执行结果
                await context_manager.micro_compact()
                log.debug("{},{},{}",request_id, activate_turn.step_counter, " micro_compact")
                # 初始化step_id step_counter
                activate_turn.step_counter += 1
                step_id = f"{session_state.request_id}_{activate_turn.step_counter}"  # reqid_1,2,3
                activate_turn.active_step_id = step_id
                activate_turn.active_steps.append(step_id)
                activate_turn.state = AgentState.RUNNING

                try:
                    await self._execute_step(session_state, activate_turn, hook_ctx)
                finally:
                    activate_turn.active_steps.pop()
                    activate_turn.active_step_id = ""
                    async for data in context_manager.compact():
                        activate_turn.last_step_compacted = True
                        await self.message_bus.publish_runtime_event(session_state.publish_context, event_type=AgentEventType.COMPACT_EVENT,data=data,state=AgentState.RUNNING)
                if not activate_turn.requires_followup:
                    break
            log.debug("{},{},{}",request_id, activate_turn.step_counter, " 执行完一轮了")
            try:
                await context_manager.session_summary_extract()
            except Exception as exc:
                log.debug(traceback.format_exc())
                log.exception("compact_after_turn 执行异常（非致命）")
            # 输出安全校验（on_complete）：blocked 时以安全提示替换最终回复
            final_output = self._last_assistant_content(session_state)
            await self._fire(HookEvent.ON_COMPLETE, hook_ctx, final_output=final_output)
            if hook_ctx.action == HookAction.BLOCKED:
                self._replace_last_assistant_content(session_state, hook_ctx.message)
            await self.message_bus.publish_runtime_event(
                session_state.publish_context,event_type=AgentEventType.REQUEST_COMPLETED,state=AgentState.COMPLETED
            )
            activate_turn.finished_at = time.time()
            activate_turn.state = AgentState.COMPLETED

        except TurnStoppedError as exc:
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.STOPPED
            await self.message_bus.publish_runtime_event(
                session_state.publish_context,event_type=AgentEventType.REQUEST_STOPPED,
                    data={"reason": str(exc)},state=AgentState.STOPPED
            )
            log.debug("{},{},{}",request_id, activate_turn.step_counter, " TurnStoppedError：",exc)
        except asyncio.CancelledError:
            reason = self._stop_reason(session_state)
            activate_turn.error_text = reason
            activate_turn.state = AgentState.STOPPED
            await self.message_bus.publish_runtime_event(
                    session_state.publish_context,
                    event_type=AgentEventType.REQUEST_STOPPED,
                    data={"reason": reason},
                    state=AgentState.STOPPED,
            )
            log.debug("{},{},{}",request_id, activate_turn.step_counter, " CancelledError")
            raise
        except Exception as exc:
            log.opt(exception=True).error("_execute_request failed: {}", exc)
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.FAILED
            await self._fire(HookEvent.INTERNAL_ON_ERROR, hook_ctx, error=exc)
            await self.message_bus.publish_runtime_event(
                session_state.publish_context,event_type="Error", data=str(exc), state=AgentState.FAILED
            )
            log.debug("{},{}, Exception:{}",request_id, activate_turn.step_counter,  exc)
        finally:
            activate_turn.finished_at = time.time()
            # 留存快照
            await get_app().session_io.save_turn_chat_message_snapshot(
                session_state.context_manager.chat_messages,
                user_id=session_state.user_id,
                app_id=session_state.app_id,
                session_id=session_state.session_id,
            )
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

        # 这里计算的prompt_token数字不准，和实际invoke后返回的值差别很大
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
        await self.message_bus.publish_runtime_event(
            session_state.publish_context,
                event_type=AgentEventType.LLM_THINKING_START,
                data={
                    "prompt_tokens": prompt_tokens,
                    "message_count": len(messages),
                },
                state=AgentState.RUNNING,
        )
        log.debug("PreLLMCALL {},{},{}",session_state.request_id, turn_state.step_counter,turn_state.active_step_id)

        llm_response = await invoke_stream_with_revovery(
            self.agent_config,self.tools,messages, session_state
        )
        log.debug(llm_response)
        completion_tokens = self._estimate_completion_tokens(llm_response)
        meta_usage = llm_response.get("usage") or {}
        prompt_tokens = int(meta_usage.get("prompt_tokens") or 0) or prompt_tokens
        completion_tokens = int(meta_usage.get("completion_tokens") or 0) or completion_tokens
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
        # BUG-5 接线：model/finish_reason/真实 usage 作为独立 meta 落 chat_message 用量列
        # （不塞进 assistant_message，避免随 assemble 回传 LLM；缺省回退本地估算）
        chat_meta = {
            "model": str(llm_response.get("model") or "") or None,
            "finish_reason": str(llm_response.get("finish_reason") or "") or None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        # assistant 消息入库（MySQL chat_message 表；失败不阻断对话）
        try:
            from codegenx.ai_service.chat_message import get_chat_message_store
            await get_chat_message_store().append_message(
                session_state.user_id, str(session_state.app_id), session_state.session_id, assistant_message,
                meta=chat_meta,
            )
        except Exception as exc:
            log.warning("assistant 消息入库失败（不影响对话）: {}", exc)

        turn_state.requires_followup = bool(tool_calls)
        for tool_call in tool_calls:
            self._raise_if_stop_requested(session_state)
            turn_state.tool_iterations += 1
            limits = self._spec_limits(session_state)
            effective_max_tool_iterations = (
                int(limits.max_tool_iterations)
                if limits is not None and getattr(limits, "max_tool_iterations", None)
                else self.max_tool_iterations
            )
            if turn_state.tool_iterations > effective_max_tool_iterations:
                raise RuntimeError(
                    f"Agent exceeded max tool iterations ({effective_max_tool_iterations})"
                )

            signature = self._tool_call_signature(tool_call)
            if signature == turn_state.last_tool_signature:
                turn_state.consecutive_same_tool_calls += 1
            else:
                turn_state.last_tool_signature = signature
                turn_state.consecutive_same_tool_calls = 1
            if turn_state.consecutive_same_tool_calls >= self.max_same_tool_calls:
                tc_name = tool_call.get('name', 'unknown')
                tc_args = tool_call.get('arguments', {}) or {}
                arg_path = (tc_args.get('path') or '').strip() if isinstance(tc_args, dict) else ''
                arg_content = (tc_args.get('content') or '').strip() if isinstance(tc_args, dict) else ''
                if tc_name in ('write_file', 'read_file', 'edit_file') and (not arg_path or (tc_name == 'write_file' and not arg_content)):
                    raise RuntimeError(
                        f"模型连续 {turn_state.consecutive_same_tool_calls} 次调用 {tc_name} "
                        f"但未提供有效参数(path={repr(arg_path)}, content_len={len(arg_content)})。"
                        f"可能是上下文过长导致大模型参数丢失，建议新建会话重试。"
                    )
                raise RuntimeError(
                    f"Agent repeated the same tool call "
                    f"{turn_state.consecutive_same_tool_calls} times: "
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
            # 已迁移至 agent/tool_executor.py 与 tools/base.py 的模块级监听器
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

            await self.message_bus.publish_runtime_event(
                session_state.publish_context,
                event_type=AgentEventType.TOOL_EXECUTION_START, data=tool_call, state=AgentState.RUNNING
            )

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
            from codegenx.ai_service.chat_message import get_chat_message_store
            await get_chat_message_store().append_message(
                session_state.user_id,
                str(session_state.app_id),
                session_state.session_id,
                {
                    "role": "tool",
                    "tool_call_id": tool_message.get("tool_id", ""),
                    "name": tool_message.get("tool_name", ""),
                    "content": str(tool_message.get("description", "")),
                },
            )

            await self.message_bus.publish_runtime_event(
                session_state.publish_context,
                    event_type=AgentEventType.TOOL_EXECUTION_END,
                    data= tool_message,
                    state=AgentState.RUNNING
            )

        log.debug("执行完一次step 迭代, step_id: {}, step_counter:{}", turn_state.active_step_id, turn_state.step_counter)


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

