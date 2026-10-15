import json
import time
import asyncio
from codegenx.ai_service.agent.agent_schema import AgentState, AgentEventType
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.intention_agent import SupervisorAgent
from codegenx.ai_service.agent.agents.plan_agent import PlannerAgent

from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState, TurnStoppedError, raise_if_stop_signal
from codegenx.ai_service.hook import HookEvent, hook_manager, HookContext, HookAction
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.utils.config import config
from shared import log


def _message_bus():
    """运行时 lazy 获取 MessageBus（避免模块 import 时 app 尚未初始化）。"""
    return get_app().message_bus



class AutoPlanManager:
    """
    自动规划执行agent —— 统一消息路由中枢。

    核心职责：
    1. 把用户消息包装成 AgentMessage(sender="user") 投给 Supervisor
    2. 循环路由：每次取出 AgentMessage → 实例化 recipient agent → run() → 拿到新 AgentMessage
    3. 终止条件：recipient == "user" 或 max_turn_rounds 达到上限（强制终止）
    4. 每次跨 agent 路由 step_counter++，用于日志、监控与停止判断

    Agent 自身只负责 run() 内部的 ReAct 循环，不再互相 send/receive ——
    所有跨 agent 路由都收归此处，便于集中管控步数、日志与异常。
    """

    async def execute_request(self, session_state: RuntimeSessionState):
        request_id = session_state.request_id
        activate_turn = session_state.activate_turn
        activate_turn.state = AgentState.RUNNING
        activate_turn.started_at = time.time()
        max_rounds = config.multi_agents.turn_max

        hook_ctx = HookContext(
            event=HookEvent.TURN_START, session=session_state, turn=activate_turn,
            action=HookAction.CONTINUE, message="",
        )
        await hook_manager.emit(HookEvent.TURN_START, hook_ctx)

        try:
            raise_if_stop_signal(session_state)

            # ── 启动首 agent：Supervisor，消息 sender="user" ──
            user_text = str(getattr(session_state.request, "message", "") or "")
            current_msg = AgentMessage(
                sender="user",
                recipient="supervisor",
                is_user_message=True,
                message={"task": user_text},
                summary=user_text[:50],
                request_id=request_id,
                session_id=session_state.session_id,
                success=None,
                next=None,
            )

            # ── 循环路由：跨 agent 消息接力，直到 recipient=="user" ──
            while activate_turn.step_counter < max_rounds:
                raise_if_stop_signal(session_state)
                activate_turn.step_counter += 1

                # 1) 检查是否完成：recipient=="user" 表示已有 agent 生成了面向用户的最终答复
                target = current_msg.recipient or "user"
                if target == "user":
                    final_text = self._extract_final_text(current_msg)
                    log.info("═══════════════ TURN COMPLETE ═══════════════ | request={} | steps={} | final_len={}",
                             request_id, activate_turn.step_counter, len(final_text))
                    await self._reply_and_finish(session_state, final_text)
                    return

                # 2) 防止把消息发给自己（极少见，通常是 prompt 没约束好）
                current_agent_name = current_msg.sender
                if target == current_agent_name:
                    log.warning("跨 agent 路由环回警告：{} 试图派发给自身，强制终止", target)
                    await self._reply_and_finish(
                        session_state, f"执行异常：智能体 {target} 试图自我调用",
                    )
                    return

                # 3) 实例化目标 agent
                spec = get_app().agents.get(target)
                if spec is None or spec.cls is None:
                    log.error("目标 agent 未注册或无 cls: recipient={}", target)
                    await self._reply_and_finish(
                        session_state, f"执行失败：找不到智能体 {target}",
                    )
                    return
                agent = spec.cls()

                # 4) 日志高亮：TURN ROUND N/M  sender -> recipient
                task_summary = (current_msg.message.get("task") if current_msg.message else "") or ""
                task_summary = str(task_summary).replace("\n", " ")[:80]
                log.info("─────────────── [TURN {}/{}] {} ──► {} ─────────────── | request={}",
                         activate_turn.step_counter, max_rounds, current_msg.sender, target, request_id)
                log.info("  task: {}", task_summary)

                # 5) run：agent 内部执行 ReAct 循环，返回下一跳 AgentMessage
                next_msg = await agent.run(current_msg, session_state)

                # 6) 兜底：run() 返回空消息或 recipient 丢失
                if next_msg is None or not getattr(next_msg, "recipient", None):
                    log.warning("agent.run 返回空下一跳，强制终止")
                    await self._reply_and_finish(
                        session_state, current_msg.message.get("task", "")[:200] if isinstance(current_msg.message, dict) else str(current_msg.message)[:200],
                    )
                    return

                current_msg = next_msg

            # ── while 正常退出 = step_counter >= max_rounds，强制终止 ──
            log.warning("═══════════════ TURN FORCE STOP (max_rounds={}) ═══════════════ | request={}",
                        max_rounds, request_id)
            final_text = self._extract_final_text(current_msg)
            await self._reply_and_finish(
                session_state,
                f"执行超时：已达最大迭代次数 {max_rounds}，强制结束。\n\n已完成的部分结果：\n{final_text[:500]}",
            )

        except TurnStoppedError as exc:
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.STOPPED
            await _message_bus().publish_runtime_event(
                session_state.publish_context, event_type=AgentEventType.REQUEST_STOPPED,
                data={"reason": str(exc)}, state=AgentState.STOPPED,
            )
            log.info("┌─ TurnStoppedError ─┐ request={} step={} reason={}", request_id, activate_turn.step_counter, exc)
            log.debug("{},{},{}", request_id, activate_turn.step_counter, " TurnStoppedError：", exc)
        except asyncio.CancelledError:
            reason = str(session_state.stop_reason or "user-stop")
            activate_turn.error_text = reason
            activate_turn.state = AgentState.STOPPED
            await _message_bus().publish_runtime_event(
                session_state.publish_context,
                event_type=AgentEventType.REQUEST_STOPPED,
                data={"reason": reason},
                state=AgentState.STOPPED,
            )
            log.info("┌─ CancelledError ─┐ request={} reason={}", request_id, reason)
            log.debug("{},{},{}", request_id, activate_turn.step_counter, " CancelledError")
            raise
        except Exception as exc:
            log.opt(exception=True).error("_execute_request failed: {}", exc)
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.FAILED
            hook_ctx.error = [str(exc)]
            await hook_manager.emit(HookEvent.INTERNAL_ON_ERROR, hook_ctx)
            await _message_bus().publish_runtime_event(
                session_state.publish_context, event_type="Error", data=str(exc), state=AgentState.FAILED,
            )
            log.info("┌─ Exception ─┐ request={} step={} exc={}", request_id, activate_turn.step_counter, exc)
            log.debug("{},{}, Exception:{}", request_id, activate_turn.step_counter, exc)
        finally:
            activate_turn.finished_at = time.time()
            # 注：agent 间消息由本类直接方法接力（内存变量），无收件箱/队列，
            # 停止时无残留消息需清理；SSE 订阅队列由消费端 unsubscribe 管理。
            await get_app().session_io.save_turn_chat_message_snapshot(
                session_state.context_manager.chat_messages,
                user_id=session_state.user_id,
                app_id=session_state.app_id,
                session_id=session_state.session_id,
            )
            await hook_manager.emit(HookEvent.TURN_END, hook_ctx)
            log.info("═══════════════ TURN END ═══════════════ | request={} final_state={} total_steps={}",
                     request_id, activate_turn.state.value, activate_turn.step_counter)
            log.debug("OnTurnEnd {},{}", request_id, activate_turn.step_counter)

    @staticmethod
    def _extract_final_text(msg: AgentMessage) -> str:
        """从最终面向用户的 AgentMessage 里抽取纯文本。

        run() 内部组装返回的 AgentMessage 里 message={"task": next_message, ...}，
        这里兼容 message 为 dict 或 str 两种情况。
        """
        if msg is None:
            return ""
        payload = msg.message
        if isinstance(payload, dict):
            task = payload.get("task", "") or ""
            if task:
                return str(task)
        return str(payload or "")

    @staticmethod
    async def _reply_and_finish(session_state: RuntimeSessionState, content: str) -> None:
        """短路分支收尾：把面向用户的回复经 REQUEST_COMPLETED 事件下发。"""
        session_state.activate_turn.state = AgentState.COMPLETED
        await _message_bus().publish_runtime_event(
            session_state.publish_context,
            event_type=AgentEventType.REQUEST_COMPLETED,
            data={"content": content},
            state=AgentState.COMPLETED,
        )
