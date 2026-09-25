"""Hook 事件定义：事件常量 + 每类事件的默认分发模式 / 可短路性 / 异常策略声明。

设计文档：docs/Hook设计.md §3（10 类业务事件 + internal/* 内部事件）。
"""

from __future__ import annotations

from dataclasses import dataclass


class DispatchMode:
    """分发模式。"""

    SERIAL = "serial"        # 顺序执行，可短路（blocked/inject）
    PARALLEL = "parallel"    # 并发执行，异常隔离，不可短路
    WATERFALL = "waterfall"  # 洋葱模型，hook 包裹 call_next，最内层为被包裹动作


class HookEvent:
    """10 类业务事件 + 内部事件名常量（snake_case）。"""

    SESSION_START = "on_session_start"
    TURN_START = "on_turn_start"
    BEFORE_BUILD = "before_build"
    BEFORE_LLM_INVOKE = "before_llm_invoke"
    AFTER_LLM_INVOKE = "after_llm_invoke"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_COMPLETE = "on_complete"
    TURN_END = "on_turn_end"
    SESSION_END = "on_session_end"

    # 内部事件：框架内部使用，不参与业务 trace，不对外承诺稳定性
    INTERNAL_ON_ERROR = "internal/on_error"


class ErrorPolicy:
    """监听器抛异常时的处理策略（按事件声明）。"""

    RAISE = "raise"      # 异常上抛（lifecycle：初始化失败应可见地失败）
    BLOCKED = "blocked"  # 记入 ctx.errors 并按 blocked 短路（hook 失败默认阻断下游动作）
    SWALLOW = "swallow"  # 仅记录日志与 ctx.errors，主流程继续（finally 语义/只读上报类）
    SKIP = "skip"        # waterfall 专用：跳过失败层，继续洋葱下游


@dataclass(frozen=True)
class EventDefinition:
    """单类事件的分发契约声明。"""

    name: str
    mode: str                      # 默认分发模式，见 DispatchMode
    can_short_circuit: bool        # 是否支持 blocked/inject 短路协议
    on_error: str                  # 异常策略，见 ErrorPolicy
    payload_keys: tuple[str, ...]  # ctx.data 约定载荷（文档用途）
    description: str = ""


EVENT_DEFINITIONS: dict[str, EventDefinition] = {
    HookEvent.SESSION_START: EventDefinition(
        HookEvent.SESSION_START, DispatchMode.SERIAL, False, ErrorPolicy.RAISE,
        ("session",), "会话首次创建：初始化会话级对象、上报监控",
    ),
    HookEvent.TURN_START: EventDefinition(
        HookEvent.TURN_START, DispatchMode.SERIAL, False, ErrorPolicy.SWALLOW,
        ("session", "turn"), "单轮请求开始",
    ),
    HookEvent.BEFORE_BUILD: EventDefinition(
        HookEvent.BEFORE_BUILD, DispatchMode.WATERFALL, True, ErrorPolicy.SKIP,
        ("session", "turn"), "上下文组装前：洋葱包裹 assemble，可改输入/短路产出",
    ),
    HookEvent.BEFORE_LLM_INVOKE: EventDefinition(
        HookEvent.BEFORE_LLM_INVOKE, DispatchMode.SERIAL, True, ErrorPolicy.BLOCKED,
        ("session", "turn", "messages", "prompt_tokens"), "LLM 调用前：token 上报、限流，可 blocked",
    ),
    HookEvent.AFTER_LLM_INVOKE: EventDefinition(
        HookEvent.AFTER_LLM_INVOKE, DispatchMode.PARALLEL, False, ErrorPolicy.SWALLOW,
        ("session", "turn", "response", "usage"), "LLM 返回后：usage 上报、审计，相互独立并行",
    ),
    HookEvent.BEFORE_TOOL_CALL: EventDefinition(
        HookEvent.BEFORE_TOOL_CALL, DispatchMode.SERIAL, True, ErrorPolicy.BLOCKED,
        ("session", "turn", "tool_call"), "工具调用前：安全/参数校验，可 blocked/inject",
    ),
    HookEvent.AFTER_TOOL_CALL: EventDefinition(
        HookEvent.AFTER_TOOL_CALL, DispatchMode.PARALLEL, False, ErrorPolicy.SWALLOW,
        ("session", "turn", "tool_call", "result"), "工具返回后：落盘、上报、记忆信号，并行",
    ),
    HookEvent.ON_COMPLETE: EventDefinition(
        HookEvent.ON_COMPLETE, DispatchMode.SERIAL, True, ErrorPolicy.BLOCKED,
        ("session", "turn", "final_output"), "turn 最终回复产生后：输出安全校验，可 blocked 替换回复",
    ),
    HookEvent.TURN_END: EventDefinition(
        HookEvent.TURN_END, DispatchMode.SERIAL, False, ErrorPolicy.SWALLOW,
        ("session", "turn"), "单轮结束（finally，含异常路径）：快照、记忆漏斗、上报",
    ),
    HookEvent.SESSION_END: EventDefinition(
        HookEvent.SESSION_END, DispatchMode.SERIAL, False, ErrorPolicy.RAISE,
        ("session", "end_reason"), "会话关闭：记忆会话末触发、上报、清理",
    ),
    HookEvent.INTERNAL_ON_ERROR: EventDefinition(
        HookEvent.INTERNAL_ON_ERROR, DispatchMode.SERIAL, False, ErrorPolicy.SWALLOW,
        ("session", "turn", "error"), "请求执行异常（内部事件）",
    ),
}


def is_valid_event(name: str) -> bool:
    """事件名合法性：10 类业务事件，或 internal/* 内部事件。"""
    return name in EVENT_DEFINITIONS or str(name).startswith("internal/")
