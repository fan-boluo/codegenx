"""HookManager：事件总线（注册收集 → 启动校验/拓扑排序冻结 → 三种分发 → span 追踪）。

分发模式（docs/Hook设计.md §7）：
- emit           顺序执行，支持 blocked/inject 短路（仅可短路事件）
- emit_parallel  并发执行，异常隔离，不可短路
- waterfall      洋葱模型，waterfall 型 hook 逐层包裹 call_next，最内层为被包裹动作

错误隔离策略按 events.py 中各事件的 on_error 声明执行。
"""

from __future__ import annotations

import asyncio
import heapq
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from shared import log

from .context import HookAction, HookContext, HookDecision, Span, normalize_decision
from .events import (
    EVENT_DEFINITIONS,
    DispatchMode,
    ErrorPolicy,
    HookEvent,
    is_valid_event,
)


@dataclass
class HookRegistration:
    """一个监听器的注册记录（@on 装饰器产物）。"""

    event: str
    callback: Callable
    name: str
    priority: int = 100
    depends_on: tuple[str, ...] = ()
    mode: str | None = None            # None = 用事件默认模式
    condition: Callable[[HookContext], bool] | None = None
    once: bool = False
    seq: int = 0                       # 收集顺序，priority 相同时保持稳定

    @property
    def label(self) -> str:
        return f"{self.event}:{self.name}"


class HookManager:
    """全局唯一事件总线。import 时收集（pending），启动时装载冻结（registry）。"""

    def __init__(self) -> None:
        self._pending: list[HookRegistration] = []
        self._registry: dict[str, list[HookRegistration]] = {}
        self._frozen = False
        self._seq = 0
        # span 追踪回调：sink(kind, ctx, span)，kind ∈ span_start/span_end；None 关闭
        self._trace_sink: Callable[[str, HookContext, Span], None] | None = None

    # ------------------------------------------------------------ 注册

    def register(
        self,
        event: str,
        callback: Callable,
        *,
        name: str | None = None,
        priority: int = 100,
        depends_on: list[str] | tuple[str, ...] = (),
        mode: str | None = None,
        condition: Callable[[HookContext], bool] | None = None,
        once: bool = False,
    ) -> Callable:
        """收集一个监听器（@on 装饰器底层）。注册表冻结后调用直接抛错。"""
        if self._frozen:
            raise RuntimeError(f"hook 注册表已冻结，禁止运行期注册: {event}:{name}")
        self._pending.append(
            HookRegistration(
                event=event,
                callback=callback,
                name=name or getattr(callback, "__name__", "anonymous"),
                priority=int(priority),
                depends_on=tuple(depends_on or ()),
                mode=mode,
                condition=condition,
                once=bool(once),
                seq=self._seq,
            )
        )
        self._seq += 1
        return callback

    # ------------------------------------------------------------ 启动装载

    def load_and_freeze(self) -> dict[str, int]:
        """校验 pending → 按事件拓扑排序 → 冻结。返回 {事件: 监听器数}。

        任一校验失败抛 ValueError（启动 fail-fast）。
        """
        if self._frozen:
            return {event: len(regs) for event, regs in self._registry.items()}

        # 同一 (event, name) 重复注册视为配置错误（模块重复导入由解释器缓存天然避免）
        seen: set[tuple[str, str]] = set()
        for reg in self._pending:
            if not is_valid_event(reg.event):
                raise ValueError(f"hook 注册了未定义的事件: {reg.label}")
            key = (reg.event, reg.name)
            if key in seen:
                raise ValueError(f"hook name 重复: {reg.label}")
            seen.add(key)

        # 按事件分组后校验 depends_on 并拓扑排序
        by_event: dict[str, list[HookRegistration]] = {}
        for reg in self._pending:
            by_event.setdefault(reg.event, []).append(reg)

        registry: dict[str, list[HookRegistration]] = {}
        for event, regs in by_event.items():
            names = {r.name for r in regs}
            for reg in regs:
                missing = [d for d in reg.depends_on if d not in names]
                if missing:
                    raise ValueError(
                        f"hook {reg.label} depends_on 不存在: {missing}（须为同事件内已注册 hook）"
                    )
            registry[event] = self._topo_sort(regs)

        self._registry = registry
        self._pending.clear()
        self._frozen = True
        summary = {event: len(regs) for event, regs in registry.items()}
        log.info("hook 注册表已冻结: {}", summary)
        return summary

    @staticmethod
    def _topo_sort(regs: list[HookRegistration]) -> list[HookRegistration]:
        """Kahn 拓扑排序：依赖优先；无依赖关系的按 (priority, 收集序) 升序。"""
        by_name = {r.name: r for r in regs}
        indegree = {r.name: 0 for r in regs}
        dependents: dict[str, list[str]] = {}
        for reg in regs:
            for dep in reg.depends_on:
                indegree[reg.name] += 1
                dependents.setdefault(dep, []).append(reg.name)

        # 小顶堆保证平级时 priority 小者先出，再按收集序稳定
        heap = [
            (r.priority, r.seq, r.name)
            for r in regs
            if indegree[r.name] == 0
        ]
        heapq.heapify(heap)

        ordered: list[HookRegistration] = []
        while heap:
            _, _, name = heapq.heappop(heap)
            ordered.append(by_name[name])
            for nxt in dependents.get(name, ()):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    r = by_name[nxt]
                    heapq.heappush(heap, (r.priority, r.seq, nxt))

        if len(ordered) != len(regs):
            cyclic = sorted(name for name, deg in indegree.items() if deg > 0)
            raise ValueError(f"hook 依赖存在环: {cyclic}")
        return ordered

    # ------------------------------------------------------------ 分发

    def listeners(self, event: str) -> list[HookRegistration]:
        """已冻结的监听器列表（按执行序）。"""
        return list(self._registry.get(event, ()))

    def _select(self, reg: HookRegistration, ctx: HookContext) -> bool:
        """condition 谓词过滤；谓词异常视为不匹配并记录。"""
        if reg.condition is None:
            return True
        try:
            return bool(reg.condition(ctx))
        except Exception as exc:
            log.warning("hook {} condition 异常，跳过: {}", reg.label, exc)
            return False

    @staticmethod
    def _resolve_mode(reg: HookRegistration) -> str:
        definition = EVENT_DEFINITIONS.get(reg.event)
        return reg.mode or (definition.mode if definition else DispatchMode.SERIAL)

    def _apply_decision(
        self,
        ctx: HookContext,
        decision: HookDecision,
        can_short_circuit: bool,
        reg: HookRegistration,
    ) -> bool:
        """把监听器决策回填 ctx。返回 False 表示需要短路终止本次分发。

        action 只升不降：continue < inject < blocked（blocked 优先级最高）。
        """
        if decision.action == HookAction.BLOCKED:
            if can_short_circuit:
                ctx.action = HookAction.BLOCKED
                ctx.message = decision.message
                return False
            # 不可短路事件上的 blocked 视作错误记录，不中断
            ctx.errors.append(f"{reg.name}: {decision.message}")
            log.warning("hook {} 在不可短路事件 {} 上返回 blocked，已记录", reg.label, ctx.event)
            return True
        if decision.action == HookAction.INJECT:
            if ctx.action == HookAction.CONTINUE:
                ctx.action = HookAction.INJECT
                ctx.message = decision.message
            elif ctx.action == HookAction.INJECT:
                ctx.message = f"{ctx.message}\n{decision.message}" if ctx.message else decision.message
            # 已 blocked 时忽略后续 inject
        return True

    async def emit(self, event: str, ctx: HookContext) -> HookContext:
        """serial 顺序分发，决策可短路；异常按事件 on_error 策略处理。"""
        definition = EVENT_DEFINITIONS.get(event)
        can_short = bool(definition.can_short_circuit) if definition else False
        policy = definition.on_error if definition else ErrorPolicy.SWALLOW

        fired_once: list[HookRegistration] = []
        for reg in self.listeners(event):
            if not self._select(reg, ctx):
                continue
            if reg.once:
                fired_once.append(reg)
            try:
                result = reg.callback(ctx)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                ctx.errors.append(f"{reg.name}: {exc}")
                log.error("hook {} 执行异常: {}", reg.label, exc)
                if policy == ErrorPolicy.RAISE:
                    raise
                if policy == ErrorPolicy.BLOCKED:
                    # hook 失败默认阻断下游动作（与旧 blocked 协议一致）
                    ctx.action = HookAction.BLOCKED
                    ctx.message = f"{reg.name}: {exc}"
                    break
                continue  # swallow / skip：主流程继续

            decision = normalize_decision(result)
            if decision is not None and decision.action != HookAction.CONTINUE:
                if not self._apply_decision(ctx, decision, can_short, reg):
                    break

        self._remove_once(fired_once)
        return ctx

    async def emit_parallel(self, event: str, ctx: HookContext) -> HookContext:
        """parallel 并发分发：asyncio.gather 隔离异常，决策仅记录不短路。"""
        selected = [reg for reg in self.listeners(event) if self._select(reg, ctx)]

        async def _run(reg: HookRegistration) -> None:
            try:
                result = reg.callback(ctx)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                ctx.errors.append(f"{reg.name}: {exc}")
                log.error("hook {} 执行异常（并行）: {}", reg.label, exc)
                return
            decision = normalize_decision(result)
            if decision is not None and decision.action != HookAction.CONTINUE:
                self._apply_decision(ctx, decision, False, reg)

        if selected:
            await asyncio.gather(*(_run(reg) for reg in selected))
        return ctx

    async def dispatch(self, event: str, ctx: HookContext) -> HookContext:
        """按事件定义的默认模式分发（触发方首选入口）。"""
        definition = EVENT_DEFINITIONS.get(event)
        mode = definition.mode if definition else DispatchMode.SERIAL
        if mode == DispatchMode.PARALLEL:
            return await self.emit_parallel(event, ctx)
        return await self.emit(event, ctx)

    async def waterfall(
        self,
        event: str,
        ctx: HookContext,
        inner: Callable[[], Any],
    ) -> Any:
        """洋葱分发：waterfall 型监听器逐层包裹，最内层执行 inner()。

        - hook 签名 (ctx, call_next)，必须 return await call_next() 透传；
          不调用则其返回值成为整个链的结果（短路）
        - 事件上注册的非 waterfall 型 hook 作为前置步骤按序执行（保留短路协议）
        - hook 异常：跳过该层继续下游（SKIP 策略，组装不应因单个 hook 中断）
        """
        definition = EVENT_DEFINITIONS.get(event)
        can_short = bool(definition.can_short_circuit) if definition else False

        pre: list[HookRegistration] = []
        layers: list[HookRegistration] = []
        for reg in self.listeners(event):
            if self._resolve_mode(reg) == DispatchMode.WATERFALL:
                layers.append(reg)
            else:
                pre.append(reg)

        fired_once: list[HookRegistration] = []

        # 前置步骤：serial 语义
        for reg in pre:
            if not self._select(reg, ctx):
                continue
            if reg.once:
                fired_once.append(reg)
            try:
                result = reg.callback(ctx)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                ctx.errors.append(f"{reg.name}: {exc}")
                log.error("hook {} 执行异常: {}", reg.label, exc)
                continue
            decision = normalize_decision(result)
            if decision is not None and decision.action != HookAction.CONTINUE:
                if not self._apply_decision(ctx, decision, can_short, reg):
                    self._remove_once(fired_once)
                    return None  # 前置步骤短路：不执行洋葱

        async def run_layer(i: int) -> Any:
            if i >= len(layers):
                result = inner()
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            reg = layers[i]
            if reg.once:
                fired_once.append(reg)
            if not self._select(reg, ctx):
                return await run_layer(i + 1)

            async def call_next() -> Any:
                return await run_layer(i + 1)

            async with self.span(f"hook:{reg.name}", ctx):
                try:
                    result = reg.callback(ctx, call_next)
                    if asyncio.iscoroutine(result):
                        result = await result
                except Exception as exc:
                    ctx.errors.append(f"{reg.name}: {exc}")
                    log.error("waterfall hook {} 执行异常，跳过该层: {}", reg.label, exc)
                    result = await run_layer(i + 1)
            return result

        try:
            return await run_layer(0)
        finally:
            self._remove_once(fired_once)

    def _remove_once(self, fired: list[HookRegistration]) -> None:
        """once 监听器首次执行后自动注销。"""
        for reg in fired:
            hooks = self._registry.get(reg.event)
            if hooks and reg in hooks:
                hooks.remove(reg)

    # ------------------------------------------------------------ span 追踪

    @asynccontextmanager
    async def span(self, name: str, ctx: HookContext):
        """洋葱 span：自动记录用时/状态/trace 路径，sink(kind, ctx, span) 可选上报。"""
        sp = Span(name=name, parent=ctx.current_span)
        ctx._span_stack.append(sp)
        if self._trace_sink is not None:
            try:
                self._trace_sink("span_start", ctx, sp)
            except Exception:
                pass
        t0 = time.perf_counter()
        try:
            yield sp
        except Exception as exc:
            sp.status = "error"
            sp.error = str(exc)[:200]
            raise
        finally:
            sp.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            if ctx._span_stack and ctx._span_stack[-1] is sp:
                ctx._span_stack.pop()
            if self._trace_sink is not None:
                try:
                    self._trace_sink("span_end", ctx, sp)
                except Exception:
                    pass

    def set_trace_sink(self, sink: Callable[[str, HookContext, Span], None] | None) -> None:
        """注册/清除 span 追踪回调；传 None 关闭。"""
        self._trace_sink = sink

    # ------------------------------------------------------------ 测试辅助

    def reset(self) -> None:
        """清空注册表回到未装载状态（仅测试使用）。"""
        self._pending.clear()
        self._registry.clear()
        self._frozen = False
        self._trace_sink = None


def on(
    event: str,
    *,
    name: str | None = None,
    priority: int = 100,
    depends_on: list[str] | tuple[str, ...] = (),
    mode: str | None = None,
    condition: Callable[[HookContext], bool] | None = None,
    once: bool = False,
) -> Callable:
    """监听器注册装饰器：函数上直接标注即完成收集，启动时统一装载校验。

    用法：
        @on("before_tool_call", name="file_tool_param_guard", priority=20)
        async def file_tool_param_guard(ctx: HookContext) -> HookDecision | None:
            ...
    """

    def decorator(func: Callable) -> Callable:
        return hook_manager.register(
            event,
            func,
            name=name,
            priority=priority,
            depends_on=depends_on,
            mode=mode,
            condition=condition,
            once=once,
        )

    return decorator


# 模块级单例：触发方与监听器统一从此导入
hook_manager = HookManager()
