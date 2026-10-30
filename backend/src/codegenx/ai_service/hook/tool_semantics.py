"""工具语义记录：评测/归因用结构化数据独立落表（telemetry.tool_semantics）。

通道分工（docs/链路追踪设计.md）：trace span 只记执行骨架（谁在何时调了什么、
多久、成败），语义字段经 AFTER_TOOL_CALL 提取后按入参/结果分列落本表
（tool_args/tool_result），经 tool_call_id 与 trace_spans.tool_call_id join
（评测数据长期保留，trace_spans 受 retention 清理）。eval 脚本从本表取
SQL 尝试时序。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from shared import log

from codegenx.ai_service.hook import HookContext, HookEvent, on

# execute_postgresql 输出首行提取行数
_ROW_COUNT_RE = re.compile(r"共\s*(\d+)\s*行")


def _extract_semantics(name: str, arguments: dict, result: Any) -> tuple[dict, dict] | None:
    """按工具提取 (入参语义, 结果语义)；无语义载荷的工具返回 None（不落表）。"""
    extras = getattr(result, "extras", None) or {}
    if name == "execute_postgresql":
        tool_args: dict[str, Any] = {}
        sql = str(arguments.get("sql") or "")
        if sql:
            tool_args["sql"] = sql
        datasource = arguments.get("datasource") or arguments.get("db_name")
        if datasource:
            tool_args["datasource"] = str(datasource)
        tool_result: dict[str, Any] = {}
        data = str(getattr(result, "data", "") or "")
        match = _ROW_COUNT_RE.search(data)
        if match:
            tool_result["row_count"] = int(match.group(1))
        preview = extras.get("rows_preview")
        if isinstance(preview, list) and preview:
            tool_result["rows_preview"] = preview
        return (tool_args, tool_result) if (tool_args or tool_result) else None
    # 语义层召回结构化：search_tables 召回的表名（评测归因"SQL 错因召回缺表"）
    if name == "search_tables" and isinstance(extras.get("tables"), list):
        return {}, {"recalled_tables": [str(t) for t in extras["tables"]]}
    return None


@on(HookEvent.AFTER_TOOL_CALL, name="tool_semantics_recorder", priority=50)
async def tool_semantics_recorder(ctx: HookContext) -> None:
    """AFTER_TOOL_CALL：语义字段写 telemetry.tool_semantics（TraceStore 批量落库）。

    观测监听器绝不抛错；trace 禁用或无 trace 身份时跳过。
    """
    try:
        from codegenx.ai_service.trace import recorder

        if not recorder._cfg().enabled:
            return
        tc = ctx.data.get("tool_call") or {}
        name = str(tc.get("name") or "")
        arguments = tc.get("arguments") or {}
        if not name or not isinstance(arguments, dict):
            return
        session = ctx.session
        trace_id = str(getattr(session, "trace_id", "") or getattr(session, "request_id", "") or "")
        if not trace_id:
            return
        result = ctx.data.get("result")
        extracted = _extract_semantics(name, arguments, result)
        if extracted is None:
            return
        tool_args, tool_result = extracted
        # 载荷截断与 trace 同配置：rows_preview 限行、单字段字符上限
        if isinstance(tool_result.get("rows_preview"), list):
            tool_result["rows_preview"] = tool_result["rows_preview"][: recorder._cfg().rows_preview_limit]
        tool_args, _ = recorder._clip(tool_args, recorder._cfg().payload_max_chars)
        tool_result, _ = recorder._clip(tool_result, recorder._cfg().payload_max_chars)
        # tool_call_id = LLM 调用 id（与 span/tool 消息配对）；非 LLM 发起时 uuid 兜底
        tool_call_id = str(tc.get("id") or "") or f"call-{uuid.uuid4().hex}"
        recorder.store.enqueue("tool_semantics", {
            "tool_call_id": tool_call_id,
            "trace_id": trace_id,
            "span_id": ctx.data.get("span_id"),
            "tool_name": name,
            "success": bool(getattr(result, "success", False)),
            "tool_args": tool_args,
            "tool_result": tool_result,
            "created_at": recorder._utc(),
        })
    except Exception as exc:  # noqa: BLE001 — 观测失败只降级
        log.warning("tool_semantics 记录失败: {}", exc)
