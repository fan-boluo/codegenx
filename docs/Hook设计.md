# Hook 设计

> 版本：v2.1（设计方案） · 2026-09-25
> 相关代码：`backend/src/codegenx/ai_service/hook/`
> 触发方：`backend/src/codegenx/ai_service/agent/runtime.py`
> 【重要原则】对于要不要放hook的原则：
Hook 是执行流上的**拦截 / 扩展点**，核心价值是把**横切关注点**（监控、日志、安全、记忆、注入、策略）从核心执行循环里抽离出来 —— 让主流程保持显式、可推理，同时允许这些能力按需插入。判断 "要不要放 hook" 只有一个试金石：**删掉这个 hook，主流程是否还能正确运行**


## 1. 设计目标

1. **事件监听 + 注册**：观察者模式，事件总线（HookManager）为中心，监听器自注册
2. **注解注册**：函数上直接 `@on("on_session_start", ...)` 即完成注册，零接线
3. **启动时装载**：服务启动（lifespan）时统一校验依赖图后冻结注册表
4. **三种分发模式**：顺序（serial，可短路）、并行（parallel）、洋葱（waterfall）
5. **依赖与优先级**：每个 hook 声明 `priority` 与 `depends_on`，按拓扑排序执行
6. **洋葱模型 trace**：session / turn / llm / tool 四层洋葱包裹，自动记录用时与 trace 路径
7. **故障隔离**：钩子异常不影响主循环（lifecycle 类钩子除外）
8. **监听器按模块归属**：内置 hook 写在各功能模块的**原有组件文件**内（监控归 monitor、记忆归 memory、上下文归 context……），不新建集中的 builtin 目录；

## 2. 事件清单与职责分析（10 类）

事件命名统一 snake_case。每类事件定义：触发时机、默认分发模式、payload、内置监听器职责、可短路性。


### 2.2 事件定义
目前的事件及触发点对于函数名称
event：on_user_input ,name:inputput_safety_check 用户安全检查
event：on_session_start ,name:report_session_start 初始化session，turn,telemetry对象，上报session总数、活跃session数到监控
event：on_turn_start ,name:report_turn_start monitor 监控
event：before_build ,name:inject_dynamic_prompts  目前什么也没有，保持现状

event：before_llm_invoke ,name:report_prompt_tokens  这个prompt token计算和实际差别太大，也没有任何拦截
event：after_llm_invoke ,name:report_llm_usage  模型返回的实际使用量

event：before_tool_call ,name:report_tool_start   具体怎么检查后续再说吧
event：before_tool_call ,name:safe_path_guard
event：before_tool_call ,name:file_tool_param_guard

event：after_tool_call ,name:persist_tool_log 
event：after_tool_call ,name:report_tool_end

event：on_complete ,name:output_safety_check  ? 必须要经过的一个环节，做成hook不好吧
event：internal/on_error ,name:report_error

event：on_turn_end ,name:report_turn_end
event：on_turn_end ,name:memory_turn_signal

event：on_session_end ,name:report_session_end
event：on_session_end ,name:memory_session_end


关于span: 现在的span做的是监控，已经在monitor部分有这类功能了，这里就去掉了
链路追踪，后续有可能再补充吧

补充约定：

- **`on_error`**（现有第 9 个钩子）保留为内部事件 `internal/on_error`，在 `_execute_request` 异常分支触发，不计入 10 类业务事件。
- **`internal/*` 前缀**保留给框架内部事件，不参与业务 trace。
- 事件模式由 `events.py` 的 `EVENT_DEFINITIONS` 统一声明，单个 hook 可用 `mode=` 覆盖（如把某个 `after_llm_invoke` 监听器改回 serial 以控制顺序）。

### 2.3 为什么各事件选择该默认模式

- **serial（顺序）**：
- **parallel（并行）**：
- **waterfall（洋葱）**：

## 3. 总体架构

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

```

### 3.1 hook/ 框架层模块划分（只含机制）

```
hook/
├── __init__.py        # 导出 on、hook_manager、HookContext、HookDecision、事件常量
├── events.py          # 事件常量 + EVENT_DEFINITIONS（默认模式/payload/可短路声明）
├── core.py            # HookManager、HookRegistration、拓扑排序、三种分发、span,（含启动冻结入口 load_and_freeze，由 systemApp on_init 调用）
└── context.py         # HookContext 统一上下文、HookDecision 决策对象 
```

## 4. `@on` 装饰器设计

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

### 4.1 参数语义

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `event` | str | 必填 | 事件名，必须是 `events.py` 中已定义的 10 类事件（或 `internal/*`） |
| `name` | str | 函数名 | hook 唯一标识，`depends_on` 与查重均按此名 |
| `priority` | int | 100 | 拓扑排序中的**优先级决胜**：无依赖关系的 hook 之间按 priority 升序 |
| `depends_on` | list[str] | [] | 声明"我必须在哪些 hook 之后执行"，启动校验存在性与无环 |
| `mode` | str | 事件默认 | serial / parallel / waterfall，覆盖 EVENT_DEFINITIONS 的默认值 |
| `condition` | Callable | None | 谓词 `(ctx) -> bool`，False 则本次分发跳过该 hook |
| `once` | bool | False | 首次执行后自动移除 |

### 4.2 注册时机与幂等

- `@on` 在**模块 import 时**执行：只把 `HookRegistration` 放入 `hook_manager` 的待注册列表（pending），**不立即生效**——避免模块重复导入导致重复注册，也允许启动前统一校验。
- 启动时装载（见 §6）：`load_and_freeze()` 逐个正式注册 → 校验 → 拓扑排序 → 冻结；冻结后调用 `@on` 直接抛错（防止运行期热改注册表引发并发问题）。

## 5. 启动注册流程

```
main.py lifespan
  └─ AgentAdapterService.startup()
      ├─ systemApp.init_app()                # 各个组件imort  
      ├──────└─ hook_manager.load_and_freeze()
      │                                     # ① 内置监听器已随应用 import 链完成 @on 收集
      │                                     #    （惰性子模块由所属包 __init__ 门面装配）
      │                                     # ② 校验（任一失败 → 启动失败）：
      │                                     #    - 事件名合法（在 EVENT_DEFINITIONS 中）
      │                                     #    - hook name 无重复
      │                                     #    - depends_on 指向存在的 hook
      │                                     #    - 依赖图无环（Kahn 拓扑排序成功）
      │                                     # ③ 每个事件按 拓扑序(priority 决胜) 冻结监听器列表
      └─ runtime.start()
```

## 6. 分发协议

### 6.1 统一签名

监听器**只接收一个 `HookContext`**，返回值约定：

```python
async def hook(ctx: HookContext) -> HookDecision | None | dict
```

| 返回 | 语义 |
|---|---|
| `None` | 放行，继续后续 hook（绝大多数 hook 的正常返回） |
| `HookDecision(action="blocked", message=...)` | 短路：仅 `before_llm_invoke` / `before_tool_call` / `on_complete` 及 waterfall 有效；主流程按现有 blocked 协议处理（工具消息注入 "blocked" 并跳过执行） |
| `HookDecision(action="inject", message=...)` | 不阻断，但注入一条提示消息到上下文（现有 PreToolUse inject 协议保留） |
| waterfall 模式 | 签名为 `(ctx, call_next)`，**必须** `return await call_next(ctx)` 透传；不调用即短路，其返回值成为整个链的结果 |

### 6.2 三种分发的执行语义

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

## 7. HookContext 与 trace（洋葱模型）

### 7.1 HookContext

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

### 7.2 洋葱 span 树

session → turn → step → (llm_invoke | tool_call) 四层嵌套，与真实调用结构一致：
目前去掉了，与监控部分功能重合
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

## 10. 扩展示例

```python
# 业务方自定义钩子（如 app 级审计），无需改框架与配置：
# 只要在应用 import 链可达的任意模块里写 @on 即自动注册

from codegenx.ai_service.hook import on, HookContext

@on("on_session_start", priority=200)          # 内置(100)之后执行
async def audit_session(ctx: HookContext):
    await bi.report("session_open", user=ctx.session.user_id)

@on("after_tool_call", mode="parallel")
async def collect_tool_metrics(ctx: HookContext):
    tool = ctx.data["tool_call"].get("name")
    metrics.counter("tool_calls", tags={"tool": tool}).inc()
```

## 11. 测试计划

固化到 `backend/tests/`：

| 用例组 | 覆盖点 |
|---|---|
| `test_hook_registry.py` | @on 收集与幂等；重复 name / 非法事件名 / depends_on 悬空 / 依赖环 → 启动校验报错；拓扑排序正确性（依赖优先，平级按 priority） |
| `test_hook_dispatch.py` | serial 顺序与短路（blocked/inject）；parallel 全部执行且异常不扩散；waterfall 洋葱嵌套、不调 call_next 短路、内层 inner 返回值透传 |
| `test_hook_context.py` | HookContext.action 由分发器回填；errors 收集；once 注销 |
| `test_hook_runtime_integration.py` | mock LLM 跑一次完整请求，断言事件触发序列：`on_session_start → on_turn_start → [before_build → before_llm_invoke → after_llm_invoke → (before_tool_call → after_tool_call)*] → on_complete → on_turn_end → on_session_end`；span 树的父子关系与 duration 非空 |

