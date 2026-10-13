import json
import time
import asyncio
from codegenx.ai_service.agent.agent_schema import AgentState, AgentEventType
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.intention_agent import IntentionRecognitionAgent, IntentionType
from codegenx.ai_service.agent.agents.plan_agent import PlannerAgent

from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState, TurnStoppedError, raise_if_stop_signal
from codegenx.ai_service.hook import HookEvent, hook_manager, HookContext, HookAction
from codegenx.ai_service.system_app import get_app
from shared import log

message_bus = get_app().message_bus




class AutoPlanManager:
    """
    自动规划执行agent
    """
    async def execute_request(self, session_state: RuntimeSessionState):
        """
        自动计划执行
        Args:
            session_state:

        Returns:

        """
        request_id = session_state.request_id
        activate_turn = session_state.activate_turn
        activate_turn.state = AgentState.RUNNING
        activate_turn.started_at = time.time()
        # turn 级 hook 上下文：整个 turn 复用同一 ctx）
        hook_ctx = HookContext(
            event=HookEvent.TURN_START, session=session_state, turn=activate_turn,action=HookAction.CONTINUE,message=""
        )
        await hook_manager.emit(HookEvent.TURN_START, hook_ctx)
        try:
            raise_if_stop_signal(session_state)
            # 用户消息收敛为 agent 间消息：manager 把 UserAIRequest.message 包成
            # AgentMessage(sender="user") 投到首 agent inbox；agent.run() 仅靠 receive 取数，
            # 不再 fallback 到 session_state.request（消除双来源混乱）
            first_agent = IntentionRecognitionAgent()
            user_text = str(getattr(session_state.request, "message", "") or "")
            kickoff = AgentMessage(
                sender="user",
                recipient=first_agent.agent_name,
                message_type="task",
                message=user_text,
                payload={"task": user_text},
                request_id=request_id,
                session_id=session_state.session_id,
            )
            await message_bus.send_to_agent(kickoff)
            # 必须 await：agent 内部检查点（think/act 前）抛出的 TurnStoppedError
            # 只有在协程被执行时才会沿调用链传播到本方法
            res = await first_agent.run(session_state)
            raise_if_stop_signal(session_state)

            # 意图识别失败：直接返回失败原因并结束本轮
            if not res.success:
                await self._reply_and_finish(session_state, res.message or "意图识别失败")
                return

            # 意图识别结果：payload.output = {"intent": ..., "ask_user": ...}
            output = res.payload.get("output") or {}
            intent = output.get("intent")

            # 按意图分情况执行
            if intent == IntentionType.GREETING:
                # 打招呼：返回固定的自我介绍
                await self._reply_and_finish(
                    session_state,
                    "你好，我是数据分析智能体，可以帮你做数据查询、分析与任务规划，请问有什么可以帮你的？",
                )
                return
            if intent == IntentionType.NEED_CONFIRM:
                # 信息有歧义：把向用户确认的提问返回
                await self._reply_and_finish(
                    session_state,
                    output.get("ask_user") or "你的请求信息不够明确，请补充更多信息。",
                )
                return
            if intent == IntentionType.UNABLE_ANSWER:
                # 无关 / 违规内容：直接拦截
                await self._reply_and_finish(session_state, "抱歉，我无法处理这个问题。")
                return
            if intent != IntentionType.CALL_PLANNER:
                # 未知意图：兜底结束
                await self._reply_and_finish(session_state, "未能识别你的意图，请重新描述你的问题。")
                return

            # intent == CALL_PLANNER：正常业务问题，链式交给 Planner 规划。
            # 把上一 agent 的返回结果转发给 Planner 的 inbox，
            # 由 manager 仲裁路由，agent 间互不感知对方类，仅认 agent_name
            planner = PlannerAgent()
            res.recipient = planner.agent_name
            res.message_type = "task"
            await message_bus.send_to_agent(res)
            res = await planner.run(session_state)
            raise_if_stop_signal(session_state)

            # 规划失败：返回失败原因
            if not res.success:
                await self._reply_and_finish(session_state, res.message or "任务规划失败")
                return
            # 规划成功：下发任务方案（后续步骤执行暂未接入）
            plan = res.payload.get("output") or {}
            await self._reply_and_finish(session_state, json.dumps(plan, ensure_ascii=False))
        except TurnStoppedError as exc:
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.STOPPED
            await message_bus.publish_runtime_event(
                session_state.publish_context, event_type=AgentEventType.REQUEST_STOPPED,
                data={"reason": str(exc)}, state=AgentState.STOPPED
            )
            log.debug("{},{},{}", request_id, activate_turn.step_counter, " TurnStoppedError：", exc)
        except asyncio.CancelledError:
            reason = str(session_state.stop_reason or "user-stop")
            activate_turn.error_text = reason
            activate_turn.state = AgentState.STOPPED
            await message_bus.publish_runtime_event(
                session_state.publish_context,
                event_type=AgentEventType.REQUEST_STOPPED,
                data={"reason": reason},
                state=AgentState.STOPPED,
            )
            log.debug("{},{},{}", request_id, activate_turn.step_counter, " CancelledError")
            raise
        except Exception as exc:
            log.opt(exception=True).error("_execute_request failed: {}", exc)
            activate_turn.error_text = str(exc)
            activate_turn.state = AgentState.FAILED
            hook_ctx.error = [str(exc)]
            await hook_manager.emit(HookEvent.INTERNAL_ON_ERROR, hook_ctx)
            await message_bus.publish_runtime_event(
                session_state.publish_context, event_type="Error", data=str(exc), state=AgentState.FAILED
            )
            log.debug("{},{}, Exception:{}", request_id, activate_turn.step_counter, exc)
        finally:
            activate_turn.finished_at = time.time()
            # 停止短路时清理本次任务滞留在各 agent 收件箱的消息，
            # 避免下次同 agent_name 的实例 receive() 先取到上一次的旧消息
            if activate_turn.state == AgentState.STOPPED:
                purged = message_bus.purge_agent_messages(
                    request_id=request_id, session_id=session_state.session_id
                )
                log.info("停止短路清理 agent 间消息 request={} purged={}", request_id, purged)
            # 留存快照
            await get_app().session_io.save_turn_chat_message_snapshot(
                session_state.context_manager.chat_messages,
                user_id=session_state.user_id,
                app_id=session_state.app_id,
                session_id=session_state.session_id,
            )
            await hook_manager.emit(HookEvent.TURN_END, hook_ctx)
            log.debug("OnTurnEnd {},{}", request_id, activate_turn.step_counter)

    @staticmethod
    async def _reply_and_finish(session_state: RuntimeSessionState, content: str) -> None:
        """短路分支收尾：把面向用户的回复经 REQUEST_COMPLETED 事件下发，并置本轮为完成。

        REQUEST_COMPLETED 是 ChatService.stream_message 的终止事件，SSE 侧据此结束流。
        """
        session_state.activate_turn.state = AgentState.COMPLETED
        await message_bus.publish_runtime_event(
            session_state.publish_context,
            event_type=AgentEventType.REQUEST_COMPLETED,
            data={"content": content},
            state=AgentState.COMPLETED,
        )
