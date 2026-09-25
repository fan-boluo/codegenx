# Hook 设计

> 版本：v2.1（设计方案） · 2026-09-25
> 相关代码：`backend/src/codegenx/ai_service/hook/`
> 触发方：`backend/src/codegenx/ai_service/agent/runtime.py`

## 1. 背景与现状问题

当前 hook 实现（`hook/runner.py` 的 `HookRunner`）本质上只是一个
`dict[事件名 → 回调列表]` + 顺序 for 循环，**并不是真正的事件系统**：

| 问题 | 现状 |
|---|---|
| 注册方式 | 硬编码在 `registry.py.register_all_hooks()` 中逐个 `runner.register()`，新增钩子必须改注册代码 |
| 分发模式 | 只有顺序执行一种，无并行、无洋葱（waterfall） |
| 排序能力 | 无优先级、无依赖声明，完全按注册顺序 |
| 事件数量 | 9 个（OnSessionStart 等 PascalCase 命名），缺 `before_build`、`on_complete` |
| Trace | 无统一的用时记录与调用路径追踪 |
| 历史包袱 | `hook/events.py` 是从 cordis 移植的 EventsService，**无任何引用**（死代码）；两套事件总线并存易混淆 |
| 可扩展性 | 业务/插件无法在不修改框架代码的情况下挂载自己的钩子 |

## 2. 设计目标

1. **事件监听 + 注册**：观察者模式，事件总线（HookManager）为中心，监听器自注册
2. **注解注册**：函数上直接 `@on("on_session_start", ...)` 即完成注册，零接线
3. **启动时装载**：服务启动（lifespan）时统一校验依赖图后冻结注册表
4. **三种分发模式**：顺序（serial，可短路）、并行（parallel）、洋葱（waterfall）
5. **依赖与优先级**：每个 hook 声明 `priority` 与 `depends_on`，按拓扑排序执行
6. **洋葱模型 trace**：session / turn / llm / tool 四层洋葱包裹，自动记录用时与 trace 路径
7. **故障隔离**：钩子异常不影响主循环（lifecycle 类钩子除外）
8. **监听器按模块归属**：内置 hook 写在各功能模块的**原有文件**内（监控归 monitor、记忆归 memory、上下文归 context……），不新建集中的 builtin 目录；仅真正无归属的才单写文件

## 3. 事件清单与职责分析（10 类）

事件命名统一 snake_case。每类事件定义：触发时机、默认分发模式、payload、内置监听器职责、可短路性。

### 3.1 触发点与 runtime.py 对应关系

```
会话生命周期        on_session_start ──────────► session_pool.get_or_create(is_new=True)
                   on_session_end ─────────────► _close_session_state()

单轮（一个请求）    on_turn_start ──────────────► _execute_request() 入口
（一次请求=一个    before_build ───────────────► _execute_step() 中 assemble() 之前
 turn，内含多      before_llm_invoke ──────────► _invoke_llm_with_recovery() 之前
 个 step）         after_llm_invoke ───────────► LLM 返回后
                   before_tool_call ───────────► tool_executor.execute() 之前（逐个工具）
                   after_tool_call ────────────► 工具返回后（逐个工具）
                   on_complete ────────────────► turn 正常结束、发布 REQUEST_COMPLETED 前
                   on_turn_end ────────────────► _execute_request() finally
```

### 3.2 事件定义表

| # | 事件 | 触发时机 | 默认模式 | 可短路 | 关键 payload | 内置监听器职责（归属模块见 §4.2） |
|---|---|---|---|---|---|---|
| 1 | `on_session_start` | 会话首次创建 | serial | 否 | `session` | 会话级对象初始化：SessionManager / TaskManager / SessionContext、加载聊天历史快照、用户消息入库、更新会话索引（agent 层）；monitor 上报（monitor 层） |
| 2 | `on_turn_start` | 每次请求开始 | serial | 否 | `session, turn` | monitor turn_start 上报；turn 快照（预留） |
| 3 | `before_build` | 上下文组装前（每 step 的 `assemble()` 之前） | **waterfall** | 是（不调 call_next） | `session, turn, build_input` | 注入 memory_prompt / skill_prompt / persona / task_prompt / extra 提醒；修改或替换组装输入（context 模块） |
| 4 | `before_llm_invoke` | LLM 调用前 | serial | **是** | `session, turn, messages, prompt_tokens` | token 预估上报（monitor）；限流/预算控制；消息改写（waterfall 覆盖时） |
| 5 | `after_llm_invoke` | LLM 返回后 | **parallel** | 否 | `session, turn, response, usage` | usage 上报（monitor）；响应审计/计费；相互独立故并行 |
| 6 | `before_tool_call` | 每次工具调用前 | serial | **是** | `session, turn, tool_call` | 权限与安全校验（`action=blocked`）；文件工具参数守卫（`action=inject`，从 runtime.py 内联校验迁入，tools 模块）；monitor 上报 |
| 7 | `after_tool_call` | 工具执行返回后 | **parallel** | 否 | `session, turn, tool_call, result` | 工具执行快照落盘 append_tool_log（session 模块）；monitor 上报；记忆漏斗信号（memory 模块） |
| 8 | `on_complete` | turn 正常产生最终回复后、发布完成事件前 | serial | **是** | `session, turn, final_output` | **输出安全校验**（敏感内容/合规审查，guardrail 模块）；失败时 `action=blocked`，以安全提示替换最终回复 |
| 9 | `on_turn_end` | 单轮结束（finally，含异常路径） | serial | 否 | `session, turn` | 保存聊天消息快照（session 层）；记忆两级漏斗信号 warm_extract（memory 层）；monitor turn_end |
| 10 | `on_session_end` | 会话关闭 | serial | 否 | `session, end_reason` | 记忆会话末触发（memory 层）；monitor session_end；资源清理 |

补充约定：

- **`on_error`**（现有第 9 个钩子）保留为内部事件 `internal/on_error`，在 `_execute_request` 异常分支触发，不计入 10 类业务事件。
- **`internal/*` 前缀**保留给框架内部事件，不参与业务 trace。
- 事件模式由 `events.py` 的 `EVENT_DEFINITIONS` 统一声明，单个 hook 可用 `mode=` 覆盖（如把某个 `after_llm_invoke` 监听器改回 serial 以控制顺序）。

### 3.3 为什么各事件选择该默认模式

- **serial（顺序）**：`on_session_start` / `on_turn_start` / `on_turn_end` / `on_session_end` 存在隐式先后依赖（先初始化 manager 才能加载历史）；`before_llm_invoke` / `before_tool_call` / `on_complete` 需要短路语义（任一 hook 拒绝即终止），必须顺序评估。
- **parallel（并行）**：`after_llm_invoke` / `after_tool_call` 的监听器（监控上报、日志落盘、计费、记忆信号）彼此独立、只读入参、互不依赖，并行可降低对主循环时延的叠加。
- **waterfall（洋葱）**：`before_build` 需要"包住"组装过程——既能在前置修改输入，也能在后置修改产物（messages），且支持短路（某个 hook 直接给出组装结果）。

## 4. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│ AgentRuntime（触发方，只依赖 hook_manager 一个入口）          │
│   await hook_manager.emit("on_turn_start", ctx)          │
│   async with hook_manager.span("llm_invoke", ctx): ...   │
└────────────────────────┬────────────────────────────────┘
                         │ emit / emit_parallel / waterfall / span
┌────────────────────────▼────────────────────────────────┐
│ hook/（事件系统框架层，只含机制、不含业务监听器）               │
│   HookManager：注册表 + 拓扑排序 + 三种分发 + span          │
└────────────────────────▲────────────────────────────────┘
                         │ import 时自注册（@on）
┌────────────────────────┴────────────────────────────────┐
│ 各功能模块原有文件内的监听器（归属见 §4.2）                    │
│   monitor/ memory/ context/ tools/ session/ guardrail/   │
│   agent/（生命周期编排）                                    │
│ 业务扩展模块（通过配置 hook_extra_modules 加入装载清单）        │
└─────────────────────────────────────────────────────────┘
```

### 4.1 hook/ 框架层模块划分（只含机制）

```
hook/
├── __init__.py        # 导出 on、hook_manager、HookContext、HookDecision、事件常量
├── events.py          # 事件常量 + EVENT_DEFINITIONS（默认模式/payload/可短路声明）
├── core.py            # HookManager、HookRegistration、拓扑排序、三种分发、span
├── context.py         # HookContext 统一上下文、HookDecision 决策对象
└── discover.py        # 启动装载：import 各模块的监听器所在文件 + 配置扩展模块，校验依赖图
```

### 4.2 内置监听器归属（**不新建 builtin 目录，写进模块原有文件**）

归属原则：**监听器跟功能走**——某个模块提供的能力，其 hook 监听器就写在该模块的原有文件内；
多个模块可在同一事件上各自注册监听器（如 `on_turn_end` 上 session 层存快照、memory 层发信号、
monitor 层上报，互不相干）。仅当确实无模块归属时，才在 `hook/` 下单写一个文件（如未来的独立 safety 模块）。

| 监听器 | 所在文件（原有文件，不新建） | 说明 |
|---|---|---|
| 会话/turn 生命周期编排：`init_session_objects`、`persist_chat_snapshot` 等 | `agent/runtime.py` | 跨模块对象创建（SessionManager/TaskManager/SessionContext）是 agent 运行时组合根的职责，写在 runtime.py 内 |
| monitor 上报 ×8：`report_turn_start`、`report_prompt_tokens`、`report_llm_usage`、`report_tool_start`、`report_tool_end`、`report_turn_end`、`report_session_start`、`report_session_end` | `monitor/monitor_pipeline.py` | 现 handlers.py 里对 `get_monitor_pipeline()` 的全部转发调用收口到此，各事件上独立注册 |
| 记忆信号：`memory_turn_signal`（on_turn_end）、`memory_session_end`（on_session_end） | `memory/trigger.py` | 薄封装调同文件的 `process_turn_signal` / `process_session_end` |
| 上下文注入：`inject_dynamic_prompts`（before_build 默认实现，透传洋葱） | `context/assembler.py` | 与 prompt 组装同文件，直接访问 persona/memory_prompt 等字段 |
| 工具参数守卫：`file_tool_param_guard`（before_tool_call） | `tools/base.py` | 从 runtime.py L616-656 的内联校验迁入（path/content 为空检查） |
| 工具日志落盘：`persist_tool_log`（after_tool_call） | `session/manager.py` | append_tool_log 本就是 SessionManager 方法，监听器与其同文件 |
| 输出安全校验：`output_safety_check`（on_complete） | `guardrail/prompt_safety_input_guardrail.py` | 与输入安全校验同文件；新增输出侧检测方法。若未来安全能力独立成模块，随迁 |

装载清单（`discover.py`，均为 import 路径，启动时逐一 import 以触发 @on 收集）：

```python
BUILTIN_HOOK_MODULES = [
    "codegenx.ai_service.agent.runtime",
    "codegenx.ai_service.monitor.monitor_pipeline",
    "codegenx.ai_service.memory.trigger",
    "codegenx.ai_service.context.assembler",
    "codegenx.ai_service.tools.base",
    "codegenx.ai_service.session.manager",
    "codegenx.ai_service.guardrail.prompt_safety_input_guardrail",
]
```

删除：`hook/runner.py`（HookRunner）、`hook/registry.py`（register_all_hooks）、现 `hook/events.py`（cordis 移植死代码）。

## 5. `@on` 装饰器设计

```python
from codegenx.ai_service.hook import on, HookContext, HookDecision

# 最简形式：只传事件名，其余全部默认
@on("after_llm_invoke")
async def report_usage(ctx: HookContext) -> None: ...

# 完整形式
@on(
    "before_tool_call",
    name="file_tool_param_guard",       # 依赖声明引用此名，默认取函数名
    priority=10,                        # 数值小者先执行（同事件内）
    depends_on=["tool_safety_check"],   # 必须晚于这些 hook 执行
    mode="serial",                      # 可选：覆盖事件默认分发模式
    condition=lambda ctx: ctx.session.app_id == 1,   # 可选：谓词过滤
    once=False,                         # True 则首次触发后自动注销
)
async def file_tool_param_guard(ctx: HookContext) -> HookDecision | None:
    path = (ctx.data["tool_call"].get("arguments") or {}).get("path", "")
    if not path:
        return HookDecision(action="blocked", message="path 参数为空")
    return None   # None = 放行，继续后续 hook
```

### 5.1 参数语义

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `event` | str | 必填 | 事件名，必须是 `events.py` 中已定义的 10 类事件（或 `internal/*`） |
| `name` | str | 函数名 | hook 唯一标识，`depends_on` 与查重均按此名 |
| `priority` | int | 100 | 拓扑排序中的**优先级决胜**：无依赖关系的 hook 之间按 priority 升序 |
| `depends_on` | list[str] | [] | 声明"我必须在哪些 hook 之后执行"，启动校验存在性与无环 |
| `mode` | str | 事件默认 | serial / parallel / waterfall，覆盖 EVENT_DEFINITIONS 的默认值 |
| `condition` | Callable | None | 谓词 `(ctx) -> bool`，False 则本次分发跳过该 hook |
| `once` | bool | False | 首次执行后自动移除 |

### 5.2 注册时机与幂等

- `@on` 在**模块 import 时**执行：只把 `HookRegistration` 放入 `hook_manager` 的待注册列表（pending），**不立即生效**——避免模块重复导入导致重复注册，也允许启动前统一校验。
- 启动时装载（见 §6）：`load_and_freeze()` 逐个正式注册 → 校验 → 拓扑排序 → 冻结；冻结后调用 `@on` 直接抛错（防止运行期热改注册表引发并发问题）。

## 6. 启动注册流程

```
main.py lifespan
  └─ AgentAdapterService.startup()
      ├─ hook_manager.load_and_freeze()     # ① 逐一 import BUILTIN_HOOK_MODULES（各模块
      │                                     #    原有文件内的 @on 完成收集；多数模块 runtime
      │                                     #    本就会 import，幂等）
      │                                     # ② 按 settings.hook_extra_modules 追加业务扩展模块
      │                                     # ③ 校验（任一失败 → 启动失败，fail-fast）：
      │                                     #    - 事件名合法（在 EVENT_DEFINITIONS 中）
      │                                     #    - hook name 无重复
      │                                     #    - depends_on 指向存在的 hook
      │                                     #    - 依赖图无环（Kahn 拓扑排序成功）
      │                                     # ④ 每个事件按 拓扑序(priority 决胜) 冻结监听器列表
      └─ runtime.start()
```

配置项（遵循项目配置规范）：

| 位置 | 键 | 默认 | 说明 |
|---|---|---|---|
| `.env` / `shared/config/config.py` | `HOOK_EXTRA_MODULES` | 空 | 额外装载的 hook 模块 import 路径列表，逗号分隔 |
| `ai_service/utils/config.py`（AgentConfig） | `hook_fail_fast` | true | 启动校验失败是否阻断服务启动；false 则降级为告警 |

## 7. 分发协议

### 7.1 统一签名

监听器**只接收一个 `HookContext`**（不再像现在 `(turn, **kwargs)` 散参），返回值约定：

```python
async def hook(ctx: HookContext) -> HookDecision | None | dict
```

| 返回 | 语义 |
|---|---|
| `None` | 放行，继续后续 hook（绝大多数 hook 的正常返回） |
| `HookDecision(action="blocked", message=...)` | 短路：仅 `before_llm_invoke` / `before_tool_call` / `on_complete` 及 waterfall 有效；主流程按现有 blocked 协议处理（工具消息注入 "blocked" 并跳过执行） |
| `HookDecision(action="inject", message=...)` | 不阻断，但注入一条提示消息到上下文（现有 PreToolUse inject 协议保留） |
| waterfall 模式 | 签名为 `(ctx, call_next)`，**必须** `return await call_next(ctx)` 透传；不调用即短路，其返回值成为整个链的结果 |

### 7.2 三种分发的执行语义

```python
class HookManager:
    async def emit(self, event, ctx) -> HookContext:
        """serial：按冻结顺序逐个 await；遇 blocked/inject 短路记录到 ctx；
        pre 类 hook 抛异常 → 记入 ctx.errors 并按 blocked 处理（hook 失败默认阻断，保持现行为）"""

    async def emit_parallel(self, event, ctx) -> HookContext:
        """parallel：asyncio.gather(return_exceptions=True)；异常仅记录不短路；
        所有 hook 看到的是同一 ctx，禁止并行 hook 修改 ctx.data（约定只读）"""

    async def waterfall(self, event, ctx, inner: Callable) -> Any:
        """洋葱：hook1(ctx, next) → hook2(ctx, next) → ... → inner(ctx)；
        最内层 inner 由触发方提供（如 context_manager.assemble）；
        任意一层不调用 next 即短路；调用链自动记录 span"""
```

**错误隔离规则**（与现行为对齐）：

| hook 类别 | 异常处理 |
|---|---|
| `before_*`（serial + 可短路） | 异常 → `ctx.errors.append()` → 按 `blocked` 处理（注入失败信息，不执行下游动作）；不抛出到主循环 |
| `after_*` / parallel | 异常 → 仅记录日志与 ctx.errors，绝不中断主循环 |
| `on_session_start` / `on_session_end` 等 lifecycle | 异常上抛（现状如此：初始化失败会话应可见地失败） |
| `on_turn_end` | 异常吞掉记日志（finally 语义，不能掩盖原始异常） |

## 8. HookContext 与 trace（洋葱模型）

### 8.1 HookContext

```python
@dataclass
class HookContext:
    event: str                                   # 当前事件名
    session: RuntimeSessionState                 # 会话态（request/user_id/app_id/manager 们…）
    turn: ActivateTurn | None = None             # turn 态（step_counter/active_step_id…）
    data: dict[str, Any] = field(default_factory=dict)   # 事件专属载荷（tool_call/response/…）
    action: str = "continue"                     # continue / blocked / inject（分发器回填）
    message: str = ""                            # blocked/inject 附带消息
    patches: dict[str, Any] = field(default_factory=dict)  # waterfall hook 回写产物（如 messages）
    errors: list[str] = field(default_factory=list)
    trace: list[Span] = field(default_factory=list)        # 当前 span 栈（只读视图）
```

### 8.2 洋葱 span 树

session → turn → step → (llm_invoke | tool_call) 四层嵌套，与真实调用结构一致：

```
session(s1)                         # on_session_start ~ on_session_end
└─ turn(t1, req_1)                  # on_turn_start ~ on_turn_end
   ├─ step(step_1)
   │  ├─ llm_invoke  duration=1.2s  # before/after_llm_invoke 包裹
   │  └─ tool_call:read_file  230ms # before/after_tool_call 包裹
   ├─ step(step_2)
   │  └─ llm_invoke  0.9s
   └─ on_complete    45ms           # 输出安全校验
```

- `HookManager.span(name, ctx)` 为 async contextmanager：进入时建 Span 压栈、退出时自动记录 `duration_ms / status(ok|error|short_circuit) / error`；
- `ctx.turn.active_step_id` 天然是 step 层 span 的 id；waterfall 分发中每个 hook 就是洋葱的一层，其执行用时并入所在 span；
- Span 通过 `set_trace_sink(sink)` 输出，**默认接 `monitor_pipeline` / span_collector**（已有基础设施），上报路径形如 `session/turn/step_3/llm_invoke`；
- runtime 侧只需在 4 处包上 span（见 §9），耗时统计与 trace 路径即全自动。

## 9. runtime.py 触发点改造明细

现 9 处 `hook_runner.dispatch(...)` 全部替换，并新增 2 个触发点（before_build、on_complete）：

| 位置（行号对应 main@5fb2064） | 现调用 | 目标调用 |
|---|---|---|
| L237 `_get_or_create_session_state` | `dispatch("OnSessionStart", session_state)` | `emit(HookEvent.SESSION_START, HookContext(session=…))` |
| L391 `_execute_request` | `dispatch("OnTurnStart", …)` | `emit(ON_TURN_START, ctx)`，外套 `span("turn", ctx)` |
| L486 `_execute_step` assemble() 前 | **无** | `waterfall(BEFORE_BUILD, ctx, inner=assemble)` |
| L489 | `dispatch("PreLLMCall", …)` | `emit(BEFORE_LLM_INVOKE, ctx)`，外套 `span("llm_invoke", ctx)` |
| L522 | `dispatch("PostLLMCall", …)` | `emit_parallel(AFTER_LLM_INVOKE, ctx)` |
| L599 | `dispatch("PreToolUse", …)`（读 `pre_result["action"]`） | `emit(BEFORE_TOOL_CALL, ctx)`（读 `ctx.action`），外套 `span("tool_call", ctx)` |
| L665 | `dispatch("PostToolUse", …)` | `emit_parallel(AFTER_TOOL_CALL, ctx)` |
| **新增** while 循环正常退出后、发布 REQUEST_COMPLETED 前 | **无** | `emit(ON_COMPLETE, ctx)`；`ctx.action=="blocked"` 时以 `ctx.message` 替换最终回复 |
| L478 finally | `dispatch("OnTurnEnd", …)` | `emit(ON_TURN_END, ctx)` |
| L470 异常分支 | `dispatch("OnError", …)` | `emit(INTERNAL_ON_ERROR, ctx)` |
| L345 `_close_session_state` | `dispatch("OnSessionEnd", …)` | `emit(ON_SESSION_END, ctx)` |

AgentRuntime 构造函数中 `HookRunner` / `register_all_hooks` 相关代码删除，改为直接使用模块级 `hook_manager` 单例。

## 10. 内置监听器迁移对照

现 `handlers.py` 中 9 个函数的职责拆分迁移（签名统一改为 `(ctx: HookContext)`），**一个现函数可能拆成多个归属模块的监听器**：

| 现 handler | 职责 | → 归属文件 | 监听器名 |
|---|---|---|---|
| `on_session_start` | 初始化 SessionManager/TaskManager/SessionContext、加载历史、消息入库、索引更新 | `agent/runtime.py` | `init_session_objects` |
| `on_session_start`（末尾 monitor 调用） | monitor 上报 | `monitor/monitor_pipeline.py` | `report_session_start` |
| `on_turn_start` | monitor turn_start 上报 | `monitor/monitor_pipeline.py` | `report_turn_start` |
| `pre_llm_call` | token 预估上报 | `monitor/monitor_pipeline.py` | `report_prompt_tokens` |
| `post_llm_call` | usage 上报 | `monitor/monitor_pipeline.py` | `report_llm_usage` |
| `pre_tool_use` | monitor 上报 | `monitor/monitor_pipeline.py` | `report_tool_start` |
| `post_tool_use`（前半） | 工具快照落盘 | `session/manager.py` | `persist_tool_log` |
| `post_tool_use`（后半） | monitor 上报 | `monitor/monitor_pipeline.py` | `report_tool_end` |
| `on_turn_end`（前半） | 保存聊天快照 | `agent/runtime.py` | `persist_chat_snapshot` |
| `on_turn_end`（`_funnel_turn_signal`） | 记忆漏斗信号 | `memory/trigger.py` | `memory_turn_signal` |
| `on_turn_end`（末尾 monitor 调用） | monitor 上报 | `monitor/monitor_pipeline.py` | `report_turn_end` |
| `on_error` | monitor 上报 | `monitor/monitor_pipeline.py` | `report_error`（挂 `internal/on_error`） |
| `on_session_end`（前半） | 记忆会话末触发 | `memory/trigger.py` | `memory_session_end` |
| `on_session_end`（后半） | monitor 上报 | `monitor/monitor_pipeline.py` | `report_session_end` |
| **新增**（runtime.py L616-656 内联逻辑迁出） | 文件工具参数守卫 | `tools/base.py` | `file_tool_param_guard` |
| **新增** | 输出安全校验 | `guardrail/prompt_safety_input_guardrail.py` | `output_safety_check` |
| **新增** | before_build 默认实现（透传） | `context/assembler.py` | `inject_dynamic_prompts` |

依赖与优先级示例（`on_turn_end` 事件）：`persist_chat_snapshot(priority=10)` →
`memory_turn_signal(priority=20, depends_on=["persist_chat_snapshot"])` → `report_turn_end(priority=100)`——
记忆信号必须在快照保存后读 chat_messages，用 depends_on 显式声明。

迁移期间 `PreToolUse` 的 `action: continue/blocked/inject` 协议**原样保留**，仅承载对象从 dict 换成 `HookDecision`/`ctx.action`。

## 11. 扩展示例

```python
# 业务方自定义钩子（如 app 级审计），无需改框架任何代码：
# 1. 写模块 my_project/hooks.py
# 2. .env 配置 HOOK_EXTRA_MODULES=my_project.hooks

from codegenx.ai_service.hook import on, HookContext

@on("on_session_start", priority=200)          # 内置(100)之后执行
async def audit_session(ctx: HookContext):
    await bi.report("session_open", user=ctx.session.user_id)

@on("after_tool_call", mode="parallel")
async def collect_tool_metrics(ctx: HookContext):
    tool = ctx.data["tool_call"].get("name")
    metrics.counter("tool_calls", tags={"tool": tool}).inc()
```

## 12. 测试计划

固化到 `backend/tests/`：

| 用例组 | 覆盖点 |
|---|---|
| `test_hook_registry.py` | @on 收集与幂等；重复 name / 非法事件名 / depends_on 悬空 / 依赖环 → 启动校验报错；拓扑排序正确性（依赖优先，平级按 priority） |
| `test_hook_dispatch.py` | serial 顺序与短路（blocked/inject）；parallel 全部执行且异常不扩散；waterfall 洋葱嵌套、不调 call_next 短路、内层 inner 返回值透传 |
| `test_hook_context.py` | HookContext.action 由分发器回填；errors 收集；once 注销 |
| `test_hook_runtime_integration.py` | mock LLM 跑一次完整请求，断言事件触发序列：`on_session_start → on_turn_start → [before_build → before_llm_invoke → after_llm_invoke → (before_tool_call → after_tool_call)*] → on_complete → on_turn_end → on_session_end`；span 树的父子关系与 duration 非空 |

## 13. 实施步骤

1. 新建 `hook/context.py`、`hook/events.py`（替换现 events.py 死代码）、`hook/core.py`、`hook/discover.py`
2. 各模块原有文件内添加 @on 监听器（按 §4.2 归属表）：`agent/runtime.py`、`monitor/monitor_pipeline.py`、`memory/trigger.py`、`context/assembler.py`、`tools/base.py`、`session/manager.py`、`guardrail/prompt_safety_input_guardrail.py`
3. `AgentAdapterService.startup()` 接入 `load_and_freeze()`；`AgentRuntime` 11 处触发点替换 + 2 处新增（before_build / on_complete），删除 `register_all_hooks` / `HookRunner` 引用
4. 删除 `hook/runner.py` / `hook/registry.py` / 旧 `hook/events.py`，清理 `hook/handlers.py`
5. 配置项：`HOOK_EXTRA_MODULES`（.env + config.py）、`hook_fail_fast`（AgentConfig）
6. 固化测试脚本并回归
