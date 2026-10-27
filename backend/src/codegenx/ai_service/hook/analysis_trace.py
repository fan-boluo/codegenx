"""分析链路追踪 Hook：把工具执行生命周期转换为 SSE 分析链路事件。

设计要点：
- 仅对 execute_sql 工具生效（condition 谓词过滤），其余工具不进入监听器
- BEFORE_TOOL_CALL 发 SQL_Start 并记录 start_ts；AFTER_TOOL_CALL 发 SQL_End 并弹出计算耗时
- BEFORE_TOOL_CALL 的 on_error=BLOCKED 策略要求监听器绝不抛错，所有异常内部吞掉仅记日志
- AFTER_TOOL_CALL 为 PARALLEL+SWALLOW，天然容错，但仍保守捕获避免污染 ctx.errors
- 未来 chart/report 工具落地后，按相同模式在此追加 CHART_* / REPORT_* 监听器
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from shared import log

from codegenx.ai_service.agent.agent_schema import AgentEventType, AgentState
from codegenx.ai_service.hook import HookContext, HookEvent, on

# tool_call_id → start_ts（秒）；BEFORE/AFTER 配对使用，AFTER 弹出防泄漏
_sql_start_ts: dict[str, float] = {}


def _is_execute_sql(ctx: HookContext) -> bool:
    """condition 谓词：仅对 execute_postgresql 工具生效。"""
    tc = ctx.data.get("tool_call") or {}
    return tc.get("name") == "execute_postgresql"


def _extract_sql(ctx: HookContext) -> str:
    """从 tool_call.arguments 中提取 sql 字段，兼容 arguments 为 JSON 字符串的兜底。"""
    tc = ctx.data.get("tool_call") or {}
    args = tc.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return ""
    return str(args.get("sql") or "") if isinstance(args, dict) else ""


def _extract_row_count(data: str) -> int:
    """从 execute_sql 输出首行 '查询结果（共 N 行）' 反向提取行数。"""
    if not data:
        return 0
    m = re.search(r"共\s*(\d+)\s*行", data)
    return int(m.group(1)) if m else 0


async def _publish(session: Any, event_type: AgentEventType, state: AgentState, data: dict) -> None:
    """复用 message_bus.publish_runtime_event 发布事件；任何异常仅记日志，绝不外抛。"""
    if session is None:
        return
    try:
        # 延迟 import 防循环（system_app 在 initialize_components 中显式加载本模块）
        from codegenx.ai_service.system_app import get_app
        await get_app().message_bus.publish_runtime_event(
            session.publish_context,
            event_type=event_type,
            state=state,
            data=data,
        )
    except Exception as exc:
        log.warning("分析链路 {} 事件发布失败: {}", event_type, exc)


@on(HookEvent.BEFORE_TOOL_CALL, name="sql_trace_start", priority=100, condition=_is_execute_sql)
async def sql_trace_start(ctx: HookContext) -> None:
    """execute_sql 执行前：发 SQL_Start，并记录 start_ts 供 AFTER 计算耗时。"""
    tc = ctx.data.get("tool_call") or {}
    tool_call_id = str(tc.get("id") or "")
    sql = _extract_sql(ctx)
    _sql_start_ts[tool_call_id] = time.time()
    await _publish(ctx.session, AgentEventType.SQL_START, AgentState.RUNNING, {"sql": sql})
    return None


@on(HookEvent.AFTER_TOOL_CALL, name="sql_trace_end", priority=100, condition=_is_execute_sql)
async def sql_trace_end(ctx: HookContext) -> None:
    """execute_sql 执行后：发 SQL_End（含 row_count/duration_ms/state/error）。"""
    tc = ctx.data.get("tool_call") or {}
    tool_call_id = str(tc.get("id") or "")
    sql = _extract_sql(ctx)
    result = ctx.data.get("result")

    # result 可能是 ToolResult 或 dict（safety check 失败等场景）
    if isinstance(result, dict):
        success = False
        data_str = ""
        message = str(result.get("error") or result.get("message") or "SQL 执行失败")
        extras: dict = {}
    else:
        success = bool(getattr(result, "success", False))
        data_str = str(getattr(result, "data", "") or "")
        message = str(getattr(result, "message", "") or "")
        extras = getattr(result, "extras", None) or {}

    start_ts = _sql_start_ts.pop(tool_call_id, None)
    duration_ms = int((time.time() - start_ts) * 1000) if start_ts else 0

    await _publish(
        ctx.session,
        AgentEventType.SQL_END,
        AgentState.SUCCESS if success else AgentState.FAILED,
        {
            "sql": sql,
            "row_count": _extract_row_count(data_str) if success else 0,
            "duration_ms": duration_ms,
            "state": "success" if success else "failed",
            "error": "" if success else (message or "SQL 执行失败"),
            # 前 10 行原始 dict 列表（ExecuteSqlTool 通过 ToolResult.extras 传递）
            "rows_preview": extras.get("rows_preview") or [],
        },
    )
    return None
