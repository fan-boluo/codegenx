from __future__ import annotations

import asyncio
from contextlib import suppress
import ast
import json
import time
import traceback
from datetime import datetime
from typing import Any, AsyncGenerator

from codegenx.ai_service.agent.agent_schema import AgentEvent, AgentState, AgentEventType
from codegenx.ai_service.agent.runtime_schema import (
    RuntimeSessionState,
    TurnStoppedError, ActivateTurn,
)
from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.llm.async_client import get_llm
from codegenx.ai_service.llm.llm_recovery import LLMRecovery
from codegenx.ai_service.llm.resilience import get_breaker
from codegenx.ai_service.hook import HookAction, HookContext, HookEvent, hook_manager
from codegenx.ai_service.bus import MessageBus
from codegenx.ai_service.utils.config import AgentConfig, config
from codegenx.ai_service.system_app import get_app
from shared import log
from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest
from codegenx.ai_service.compact.thresholds import estimate_tokens as _thresholds_estimate
from codegenx.ai_service.agent.agents.agent import Agent,AgentContext,AgentMessage

class BaseReActAgent(Agent):
    """
    一个基础思考，行动，验证agent
    """

    def __init__(
            self,
            system_app=None,
    ):
        Agent.__init__(self)
        # 读取配置
        self.resolve_agent_config()
        self.max_tool_iterations = max(1, int(self.agent_config.max_tool_iterations or 40))
        self.max_same_tool_calls = self.agent_config.max_same_tool_calls or 3
        self.max_steps = self.agent_config.max_steps

        # 初始化即解析默认智能体模型链
        model_name = self.agent_config.resolved_model_name
        # 准备llm,breaker
        with suppress(Exception):
            get_llm(model_name)
            get_breaker(model_name)
        self._llm_recovery = LLMRecovery()
        self.message_bus = get_app().message_bus
        # agent 间寻址标识与收件箱
        self.agent_name = self.agent_config.name
        self._inbox: asyncio.Queue | None = None


    # -----解析改智能体配置---------------------

    def resolve_agent_config(self) -> None:
        """按 name 属性匹配 config.agents 的 name，解析当前 agent 配置（缺省回退空配置）。"""
        self.agent_config = AgentConfig()
        for agent in config.agents or []:
            if (agent.name or "").strip() and agent.name.strip() == self.name.strip():
                self.agent_config = agent
                return

    def _ensure_inbox(self) -> None:
        """注册 agent 收件箱（幂等）。"""
        if self._inbox is None:
            self._inbox = self.message_bus.register_agent(self.agent_name)

    async def _prepare_agent(
        self,
        session_state: RuntimeSessionState,
    ) -> None:
        """准备 agent 所需的上下文与收件箱（子类可覆盖扩展，需先调 super()）。"""
        self._ensure_inbox()
        self.agent_context = AgentContext(
            session_id=session_state.session_id,
            app_id=str(session_state.app_id or ""),
            user_id=session_state.user_id,
            agent_name=self.name,
        )

    # ------------------------------------------------------------------ message I/O

    async def receive(self, timeout: float | None = None) -> AgentMessage | None:
        """从 agent 收件箱接收消息（timeout=None 阻塞，0 非阻塞）。"""
        self._ensure_inbox()
        if timeout == 0:
            if self._inbox.empty():
                return None
            msg = await self._inbox.get()
        else:
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        log.info(
            "agent 消息接收: {} <- {} | type={} request_id={}",
            self.agent_name, msg.sender, msg.message_type, msg.request_id,
        )
        return msg

    async def send(
        self,
        recipient: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        **kwargs,
    ) -> bool:
        """向指定 role 的 agent 发送消息，"*" 表示广播。"""
        self._ensure_inbox()
        msg = AgentMessage(
            sender=self.agent_name,
            recipient=recipient,
            message_type=message_type,
            payload=payload or {},
            request_id=kwargs.get("request_id", "") or self.agent_context.session_id,
            session_id=self.agent_context.session_id,
            success=kwargs.get("success", True),
            message=kwargs.get("message", ""),
            next_agent=kwargs.get("next_agent"),
        )
        delivered = await self.message_bus.send_to_agent(msg)
        log.info(
            "agent 消息发送: {} -> {} | type={} request_id={} delivered={}",
            msg.sender, msg.recipient, msg.message_type, msg.request_id, delivered,
        )
        return delivered

    # ------------------------------------------------------------------ ReAct lifecycle

    async def thinking(self):
        """推理当前任务目标。"""
        pass

    async def act(self):
        """执行行动。"""
        pass

    async def verify(self):
        """验证结果是否符合预期。"""
        pass

    async def run(self, session_state: RuntimeSessionState) -> AgentMessage:
        """ReAct 方式执行：think → act 循环 → verify。

        参数:
            session_state: 运行时会话状态，携带 request/session/user 及上下文管理器。
        """
        # 1. 准备 agent 上下文与收件箱
        await self._prepare_agent(session_state)
        # 2. 获取接收的消息（用户消息经 runtime 投递，或上游 agent 发送）
        await self.receive(timeout=0)
        # TODO: think → act 循环 → verify（待实现）



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
        log.debug("PreLLMCALL {},{},{}", session_state.request_id, turn_state.step_counter, turn_state.active_step_id)

        llm_response = await self._llm_recovery.invoke(
            messages, turn_state, session_state
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
        log.debug("PostLLMCall {},{},{}", session_state.request_id, turn_state.step_counter, turn_state.active_step_id)
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
            # 偏差③修复：spec.limits.max_tool_iterations 主路径消费
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
                if tc_name in ('write_file', 'read_file', 'edit_file') and (
                        not arg_path or (tc_name == 'write_file' and not arg_content)):
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
                "state": ""
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
                session_state,
                AgentEvent(
                    event_type=AgentEventType.TOOL_EXECUTION_START, data=tool_call, state=AgentState.RUNNING
                ),
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
            log.debug("PostToolUse {},{},{}", session_state.request_id, turn_state.step_counter,
                      turn_state.active_step_id)
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
                session_state,
                AgentEvent(
                    event_type=AgentEventType.TOOL_EXECUTION_END,
                    data=tool_message,
                    state=AgentState.RUNNING,
                ),
            )

        log.debug("执行完一次step 迭代, step_id: {}, step_counter:{}", turn_state.active_step_id,
                  turn_state.step_counter)



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


    def _raise_if_stop_requested(self, session_state: RuntimeSessionState) -> None:
        if session_state.stop_signal.is_set():
            raise TurnStoppedError(session_state.stop_reason or "user-stop")

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




