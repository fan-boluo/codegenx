"""Hook 事件系统：@on 注解注册 + 事件总线（serial/parallel/waterfall 三种分发）。

用法（docs/Hook设计.md §5）：
    from codegenx.ai_service.hook import on, HookContext

    @on("after_llm_invoke")
    async def report_usage(ctx: HookContext) -> None: ...

触发方统一通过 hook_manager.emit / dispatch / waterfall / span 触发。
"""

from .context import HookAction, HookContext, HookDecision, Span, normalize_decision
from .core import HookManager, HookRegistration, hook_manager, on
from .events import (
    EVENT_DEFINITIONS,
    DispatchMode,
    ErrorPolicy,
    EventDefinition,
    HookEvent,
    is_valid_event,
)

__all__ = [
    "HookAction",
    "HookContext",
    "HookDecision",
    "Span",
    "normalize_decision",
    "HookManager",
    "HookRegistration",
    "hook_manager",
    "on",
    "EVENT_DEFINITIONS",
    "DispatchMode",
    "ErrorPolicy",
    "EventDefinition",
    "HookEvent",
    "is_valid_event",
]
