from __future__ import annotations

from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any

# from agent.assembler import get_context_assembler
from agent.runtime_schema import AgentState
from agent.tool_executor import MEMORY_TOOL_NAMES
from monitor.monitor_pipeline import get_monitor_pipeline
from bot.session.manager import SessionManager
from shared.config.log_config import log
from context.session_context import SessionContext

if TYPE_CHECKING:
    from bot.agent.runtime import RuntimeSessionState


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Hook implementations
# ---------------------------------------------------------------------------


async def on_session_start(session: RuntimeSessionState, **kwargs):

    req = session.request
    if req is None:
        log.warning("on_session_start: request is None, skipping")
        return

    # context = kwargs.get("context")
    # if context is None:
    #     return

    session_manager = SessionManager(str(req.app_id))
    session.session_manager = session_manager
    session.context_manager = SessionContext(session_id=session.session_id,app_id=session.app_id)

    # await get_context_assembler().build_fix_context(session)
    # await session.context_manager.build_system_prompt(session.request.message)

    now = _utcnow()
    # request_dict = req.model_dump()
    # request_dict["started_at"] = now.isoformat()
    # session_manager.append_chat_history_message(session.session_id, request_dict)

    session.state = AgentState.RUNNING
    session.started_at = now

    get_monitor_pipeline().on_session_start(session)


async def on_turn_start(turn: Any, **kwargs):
    context = turn.context
    session = kwargs["session"]
    snapshot = {
        "session": dict(session.audit_context),
        "turn": {
            "turn_id": turn.turn_id,
            "turn_number": turn.turn_number,
            "context": context.model_dump(),
        },
    }
    snapshot_path = session.session_manager.save_turn_snapshot(turn.turn_id, snapshot)
    turn.snapshot_path = str(snapshot_path)

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
    def _log_tool_execution(
        self,
        session_manager: Any,
        session_id: str,
        turn_id: str,
        tool_name: str,
        tool_input: dict,
        result: Any,
    ) -> None:
        """将工具调用结果追加写入 session 目录下的日志文件。"""
        try:
            # 过滤掉运行时注入的不可序列化对象
            loggable_input = {
                k: v for k, v in tool_input.items()
                if k not in {"context", "context_compactor"}
            }
            entry: dict[str, Any] = {
                "ts": datetime.now(UTC).isoformat(),
                "turn_id": turn_id,
                "tool": tool_name,
                "input": loggable_input,
                "result": result,
            }
            session_manager.append_tool_log(session_id, entry)
            if tool_name in MEMORY_TOOL_NAMES:
                session_manager.append_memory_log(session_id, entry)
        except Exception as exc:
            log.debug(f"Session log write failed for tool '{tool_name}': {exc}")


async def post_tool_use(turn: Any, **kwargs):
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
            "turn_id": turn.turn_id,
            "tool": tool_name,
            "input": loggable_input,
            "result": result,
        }
        session.session_manager.append_tool_log(session.session_id, snapshot)
        if tool_name in MEMORY_TOOL_NAMES:
            session.session_manager.append_memory_log(session.session_id, snapshot)
    except Exception as exc:
        log.debug(f"Session log write failed for tool '{tool_name}': {exc}")

    await get_monitor_pipeline().post_tool_use(session, turn, tool_call, result)


async def on_turn_end(turn: Any, **kwargs):
    session = kwargs["session"]
    await get_monitor_pipeline().on_turn_end(session, turn)


async def on_error(turn: Any, **kwargs):
    session = kwargs["session"]
    get_monitor_pipeline().on_error(session, turn)


async def on_session_end(session: Any, **kwargs):
    await get_monitor_pipeline().on_session_end(session, **kwargs)
