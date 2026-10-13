import time
import asyncio
from codegenx.ai_service.agent.agent_schema import AgentState, AgentEventType
from codegenx.ai_service.agent.agents.intention_agent import IntentionRecognitionAgent
from codegenx.ai_service.agent.agents.plan_agent import PlannerAgent

from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState, TurnStoppedError, raise_if_stop_signal
from codegenx.ai_service.hook import HookEvent, hook_manager, HookContext, HookAction
from codegenx.ai_service.system_app import get_app
from shared import log

message_bus = get_app().message_bus




class AutoPlanManager():
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
            # 必须 await：agent 内部检查点（think/act 前）抛出的 TurnStoppedError
            # 只有在协程被执行时才会沿调用链传播到本方法
            res = await IntentionRecognitionAgent().run(session_state)
            raise_if_stop_signal(session_state)
            if not res.next_agent:
                return

            planner = PlannerAgent()
            res = await planner.run(session_state)
            raise_if_stop_signal(session_state)
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
