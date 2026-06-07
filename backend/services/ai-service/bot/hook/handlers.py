from __future__ import annotations

from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any

# from agent.assembler import get_context_assembler
from agent.agent_schema import AgentState
from task.task_manager import TaskManager
from agent.tool_executor import MEMORY_TOOL_NAMES
from monitor.monitor_pipeline import get_monitor_pipeline
from bot.session.manager import SessionManager
from shared.config.log_config import log
from context.session_context import SessionContext

if TYPE_CHECKING:
    from bot.agent.runtime import RuntimeSessionState


def _utcnow() -> datetime:
    return datetime.utcnow()

# Hook implemants

async def on_session_start(session: RuntimeSessionState, **kwargs):
    """ 初始化sessionManager  ContextManager  TaskManager 这些都是会话级别的"""
    req = session.request
    if req is None:
        log.warning("on_session_start: request is None, skipping")
        return
    session_manager = SessionManager(str(req.app_id),session.session_id)
    session.session_manager = session_manager

    task_manager = TaskManager(app_id=session.app_id, session_id=session.session_id)
    session.task_manager = task_manager

    session.context_manager = SessionContext(
        session_id=session.session_id,
        app_id=session.app_id,
        db_name=session.db_name,
        task_manager=task_manager,
    )
    # 初始化聊天历史
    session.context_manager.chat_messages = session_manager.get_turn_chat_message_snapshot() or []
    now = _utcnow()
    session.state = AgentState.RUNNING
    session.started_at = now
    get_monitor_pipeline().on_session_start(session)


async def on_turn_start(turn: Any, **kwargs):
    session = kwargs["session"]
    context = session.context_manager.system_prompt
    # snapshot = {
    #     "session": dict(session.audit_context),
    #     "turn": {
    #         "turn_id": session.request_id,
    #         "turn_number": turn.step_counter,
    #         "context": context,
    #     },
    # }  TODO 没想好及这个snapshot怎么使用
    # snapshot_path = await session.session_manager.save_turn_snapshot(session.request_id, snapshot)

    await get_monitor_pipeline().on_turn_start(session, turn)


async def pre_llm_call(turn: Any, **kwargs):
    session = kwargs["session"]
    get_monitor_pipeline().pre_llm_call(
        session, turn,
        prompt_tokens=kwargs.get("prompt_tokens"),
        projected_total_tokens=kwargs.get("projected_total_tokens"),
    )


async def post_llm_call(turn: Any, **kwargs):
    session = kwargs["session"]
    await get_monitor_pipeline().post_llm_call(
        session, turn,
        usage=kwargs.get("usage"),
    )


async def pre_tool_use(turn: Any, **kwargs):
    session = kwargs["session"]
    tool_call = kwargs.get("tool_call") or {}
    get_monitor_pipeline().pre_tool_use(session, turn, tool_call)


async def post_tool_use(turn: Any, **kwargs):
    """
    工具执行结果落盘
    工具执行结果加入监控

    """
    session = kwargs["session"]
    tool_call = kwargs.get("tool_call") or {}
    result = kwargs.get("result")
    tool_name = tool_call.get("name")
    tool_input = dict(tool_call.get("arguments", {}) or {})
    try:
        # 过滤掉运行时注入的不可序列化对象
        loggable_input = {
            k: v for k, v in tool_input.items()
            if k not in {"context", "context_compactor"}
        }
        snapshot: dict[str, Any] = {
            "request_id":session.request.request_id,
            "ts": datetime.now(UTC).isoformat(),
            "turn_id": turn.active_step_id,
            "tool": tool_name,
            "input": loggable_input,
            "result": result,
        }
        await session.session_manager.append_tool_log(snapshot)
        if tool_name in MEMORY_TOOL_NAMES:
            await session.session_manager.append_memory_log(snapshot)
    except Exception as exc:
        log.debug(f"Session log write failed for tool '{tool_name}': {exc}")

    await get_monitor_pipeline().post_tool_use(session, turn, tool_call, result)


async def on_turn_end(turn: Any, **kwargs):
    session_state = kwargs["session"]
    # 保留上下文快照
    if session_state.session_manager is not None and session_state.context_manager is not None:
        await session_state.session_manager.save_turn_chat_message_snapshot(
            session_state.context_manager.chat_messages,
            user_id=session_state.user_id
        )
    await get_monitor_pipeline().on_turn_end(session_state, turn)


async def on_error(turn: Any, **kwargs):
    session = kwargs["session"]
    get_monitor_pipeline().on_error(session, turn)


async def on_session_end(session: Any, **kwargs):
    await get_monitor_pipeline().on_session_end(session, **kwargs)
