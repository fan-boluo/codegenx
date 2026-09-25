"""Hook 统一上下文与决策对象。

HookContext 是监听器的唯一入参（取代旧 (turn, **kwargs) 散参），
session/turn 类型用 Any 以避免 hook 包反向依赖业务模块。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


class HookAction:
    """决策动作：与旧 PreToolUse 的 action 协议对齐。"""

    CONTINUE = "continue"
    BLOCKED = "blocked"
    INJECT = "inject"


@dataclass
class HookDecision:
    """监听器返回的决策对象（也兼容 dict {"action": ..., "message": ...}）。"""

    action: str = HookAction.CONTINUE
    message: str = ""

    @classmethod
    def block(cls, message: str = "") -> "HookDecision":
        return cls(action=HookAction.BLOCKED, message=message)

    @classmethod
    def inject(cls, message: str = "") -> "HookDecision":
        return cls(action=HookAction.INJECT, message=message)


@dataclass
class Span:
    """洋葱模型的一环：session → turn → step → (llm_invoke | tool_call)。"""

    name: str
    parent: "Span | None" = None
    started_at: float = field(default_factory=time.perf_counter)
    duration_ms: float = 0.0
    status: str = "ok"  # ok / error
    error: str = ""

    @property
    def path(self) -> str:
        """trace 路径，形如 session/turn/step_3/llm_invoke。"""
        names = []
        node: Span | None = self
        while node is not None:
            names.append(node.name)
            node = node.parent
        return "/".join(reversed(names))


@dataclass
class HookContext:
    """一次事件分发的上下文，监听器只读约定载荷、按需写决策字段。"""

    event: str
    session: Any = None                 # RuntimeSessionState
    turn: Any = None                    # ActivateTurn
    data: dict[str, Any] = field(default_factory=dict)   # 事件专属载荷
    action: str = HookAction.CONTINUE   # 分发器按决策回填：continue/blocked/inject
    message: str = ""                   # blocked/inject 附带消息
    patches: dict[str, Any] = field(default_factory=dict)  # waterfall hook 回写产物
    errors: list[str] = field(default_factory=list)
    _span_stack: list[Span] = field(default_factory=list, repr=False)

    @property
    def trace(self) -> tuple[Span, ...]:
        """当前 span 栈（栈顶为最内层），只读视图。"""
        return tuple(self._span_stack)

    @property
    def current_span(self) -> Span | None:
        return self._span_stack[-1] if self._span_stack else None


def normalize_decision(result: Any) -> HookDecision | None:
    """监听器返回值 → HookDecision；None/其它类型视为放行。"""
    if result is None:
        return None
    if isinstance(result, HookDecision):
        return result
    if isinstance(result, dict):
        return HookDecision(
            action=str(result.get("action") or HookAction.CONTINUE),
            message=str(result.get("message") or ""),
        )
    return None
