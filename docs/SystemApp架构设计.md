# SystemApp 运行时架构设计

> 版本 v2.0（2026-09-29，按落地实现重写）。本文档描述**已实现**的组件化运行时架构：
> 框架层（component.py）+ 组合根（system_app.py）+ 分散在各自模块内的组件实现与
> 生命周期钩子，以及 hook 事件系统、会话层、多智能体扩展的最终形态。
>
> 历史版本：v1.0~v1.2 为设计方案（容器收编零散单例 → 会话对象瘦身 → 多智能体扩展），
> 已按 P0~P4 全部落地；本文在其基础上按最终代码形态重写，设计原则（§2 三层模型）
> 保持不变。

---

## 0. 结论先行

进程内所有长生命周期对象收敛为一个全局容器 `SystemApp`：

| 改造前（v0 现状） | 改造后（v2 落地） |
|---|---|
| 10+ 个零散单例（`get_tool_registry()` lru_cache、类属性缓存、模块级字典……5 种风格并存） | 15 个组件统一注册进容器，`ComponentType` 枚举查表，风格唯一 |
| 生命周期散落：main.py lifespan 手写七步启动、逐个收尾 | `init_app()`（同步装配）→ `start_app()`（异步启动）→ `stop_app()`（逆序关闭）；组件钩子实现在**各自模块内**，容器只做广播 |
| 会话每建一次就 new 一批"只持 ids 的伪对象"（SessionManager/TaskManager/MemoryManager/Summary/CompactionEngine/Assembler） | 全部服务化：**方法保留，实例消失，ids 进方法签名**（§6） |
| `get_context_assembler()` 陷阱单例（lru_cache + 每轮可变状态 = 跨会话串话事故） | 已删除；无状态逻辑进 ContextService，可变状态进 TurnPrompts（随 SessionContext 生灭） |
| hook 监听器集中注册（runtime 末尾长 import 列表） | `@on` 随**模块加载**收集，`initialize_components()` 末尾统一校验/拓扑排序/冻结 |
| subagent 每次运行重扫工具目录、重建注册表 | `ToolRegistry.child_view()` 过滤视图，零重扫 |

组件数与会话数无关（N 会话共享一份），加智能体 = agents 段加一条配置（§8）。

---

## 1. 文件地图

```
backend/src/codegenx/ai_service/
├── component.py            # 框架层：LifeCycle / ComponentType / BaseComponent / SystemApp(注册表+广播)
├── system_app.py           # 组合根：SystemApp(具体容器+类型化访问器) / initialize_components /
│                           #         init_app / start_app / stop_app / get_app / reset_app
├── llm/facade.py           # LLMFacade        —— llm/ 调用层生命周期门面（调用面仍是模块函数）
├── agent/
│   ├── tool_handler.py     # ToolRegistry     —— 工具注册表（构造即扫描）+ child_view
│   ├── agent_registry.py   # AgentRegistry    —— AgentSpec 存取 + 启动校验（§8）
│   ├── runtime.py          # AgentRuntime     —— 对话引擎（LLMRecoveryMixin + BaseComponent）
│   ├── session_pool.py     # SessionPool + init_session_objects / persist_chat_snapshot 监听器
│   └── tool_executor.py    # ToolExecutor + safe_path_guard 监听器
├── skill/skill_loader.py   # SkillManager     —— skill 注册表
├── context/context_service.py  # ContextService + TurnPrompts + inject_dynamic_prompts 监听器
├── session/manager.py      # SessionPersistence + persist_tool_log 监听器
├── task/task_manager.py    # TaskBoardService —— 任务看板（磁盘持久）
├── memory/memory_manager.py    # MemoryFacade（含类体内无 self 的 memory_session_end 监听器）
├── compact/session_summary.py  # SessionSummaryService + SummaryState（会话级阈值状态）
├── compact/compact.py      # CompactionService（三路压缩 + make_breaker 会话级熔断器）
├── chat_message/store.py   # ChatMessageStore —— MySQL DAO
├── monitor/monitor_pipeline.py      # MonitorPipeline（9 个类体内无 self 监听器）
├── monitor/maintenance_service.py   # MonitorMaintenanceService（周期维护）
├── schedule/memory.py      # MemoryScheduler  —— 记忆离线任务调度
├── hook/{core,context,events}.py    # 事件总线（@on 收集 / 冻结校验 / 三种分发）
└── services/agent_adapter_service.py  # 薄壳：startup→init_app+start_app；shutdown→stop_app
```

---

## 2. 设计原则：三层模型（不变）

```
┌─────────────────────────────────────────────────────────┐
│ SystemApp（进程级，全局一份，启动装配/关闭回收）              │
│  组件 = 无状态服务 | 注册表 | 客户端池 | 后台任务            │
├─────────────────────────────────────────────────────────┤
│ SessionState（会话级，SessionPool 管理，随会话生灭）          │
│  chat_messages / TurnPrompts / 锁 / turn / compact_breaker │
├─────────────────────────────────────────────────────────┤
│ TurnState（请求级，已有 ActivateTurn）                      │
└─────────────────────────────────────────────────────────┘
```

**四问判别法**（新代码决定东西放哪时逐条过）：

1. 它随会话生灭吗？（否 → 候选组件）
2. 它的属性里除了 `session_id/app_id/user_id/路径` 还有什么？（只有 ids → **伪会话对象**，服务化，ids 作调用参数）
3. 它有每轮被覆写的可变字段吗？（有 → TurnPrompts/会话状态；无 → 组件）
4. 它持有连接/线程/后台任务吗？（有 → 必须是组件，由容器广播生命周期）

---

## 3. 框架层（component.py）

纯框架模块：**不 import 任何业务组件**。

### 3.1 LifeCycle —— 3 个生命周期钩子（重写需要的即可，其余空实现）

| 钩子 | 方向 | 广播入口 | 顺序 | 典型用途 |
|---|---|---|---|---|
| `on_init` | 启动（同步） | init_app() | 注册序 | 轻量装配/同步装载（如 SkillManager.load） |
| `async_before_start` | 启动（异步，**主钩子**） | start_app() | 注册序 | 连接/后台任务/预热/校验 |
| `async_before_stop` | 关闭（异步，**主钩子**） | stop_app() | **注册逆序** | 后台任务/连接池释放 |

### 3.2 ComponentType —— 组件名注册表（15 项）

`llm_facade / tool_registry / skill_manager / agent_registry / context_service /
session_persistence / task_board / memory_manager / session_summary / compaction /
chat_message_store / monitor_pipeline / monitor_maintenance / memory_scheduler /
agent_runtime`

`name` 用枚举；容器内以 `.value` 字符串为 key，落日志统一转字符串。

### 3.3 BaseComponent

```python
class BaseComponent(LifeCycle, ABC):
    name: Union[str, ComponentType] = "base_component"

    def __init__(self, system_app=None):          # 容器经 register 传入时立即 init_app
        if system_app is not None:
            self.init_app(system_app)

    @abstractmethod
    def init_app(self, system_app): ...           # 持有容器引用（唯一必须实现的抽象方法）

    @classmethod
    def get_instance(cls, system_app, ...) -> T   # 按组件名查表（缺省 fail fast）
```

**构造约定（重要）**：`SystemApp.register(cls)` 以 `component(system_app, *args, **kwargs)`
构造实例——因此**组件 `__init__` 的第一个位置参数必须是 `system_app`**，其余构造参数一律
关键字传参（如 `CompactionService(system_app=None, llm_fn=None)`）。

### 3.4 SystemApp（框架侧）—— 注册表 + 生命周期广播

```python
class SystemApp(LifeCycle):
    components: Dict[str, BaseComponent]

    def register(cls_, *args, **kwargs) -> 实例          # 构造 + 登记 + init_app
    def register_instance(instance) -> 实例               # name 重复 → RuntimeError（fail fast）
    def get_component(name, type, ...) -> 实例            # 缺失 → ValueError（fail fast）

    # 启动广播：注册顺序串行（顺序即依赖，非 gather）
    on_init            # init_app() 同步阶段
    async_before_start # start_app() 事件循环内（主启动钩子）
    # 关闭广播：注册逆序，异常逐组件隔离记录，保证全部组件都有机会收尾
    async_before_stop  # stop_app() 内；_stop_event 守卫：进程级幂等
```

关闭异常隔离：单组件 `async_before_stop` 抛错只记录日志，其余组件照常收尾（§5.4 用例覆盖）。

---

## 4. 组合根（system_app.py）

### 4.1 具体容器与类型化访问器

```python
class SystemApp(ComponentSystemApp):
    def __init__(self):
        super().__init__()
        self._components_ready: bool = False   # initialize_components 幂等守卫
        self._started: bool = False            # start_app/stop_app 幂等守卫

    @property
    def llm(self) -> "LLMFacade":              # 15 个类型化访问器之一
        from codegenx.ai_service.llm.facade import LLMFacade      # 惰性 import
        return self.get_component(ComponentType.LLM_FACADE, LLMFacade)
    # tools / skills / agents / context / session_io / tasks / memory / summary /
    # compaction / chat_messages / monitor / monitor_maintenance / memory_scheduler / runtime
```

- 访问器**惰性 import + 按名查表**：业务代码零感知组件化，`get_app().runtime` 即引擎；
- 未注册即访问 → `ValueError`（宁可显式失败也不静默兜底）。

### 4.2 initialize_components —— 注册清单与顺序（顺序即依赖，勿随意调整）

```python
def initialize_components(app) -> None:        # app._components_ready 幂等
    initialize_llm(app)                        # 1  llm_facade
    initialize_tools(app)                      # 2  tool_registry（构造即扫描目录）
    initialize_skill(app)                      # 3  skill_manager
    initialize_agents(app)                     # 4  agent_registry
    initialize_context(app)                    # 5  context_service
    initialize_session_io(app)                 # 6  session_persistence
    initialize_tasks(app)                      # 7  task_board
    initialize_memory(app)                     # 8  memory_manager
    initialize_summary(app)                    # 9  session_summary
    initialize_compaction(app)                 # 10 compaction
    initialize_chat_messages(app)              # 11 chat_message_store
    initialize_monitor(app)                    # 12 monitor_pipeline
    initialize_monitor_maintenance(app)        # 13 monitor_maintenance
    initialize_memory_scheduler(app)           # 14 memory_scheduler
    initialize_runtime(app)                    # 15 agent_runtime（构造取 tools，必须在其后）

    hook_manager.load_and_freeze()             # hook 注册表校验/拓扑排序/冻结（§7）
    app._components_ready = True
```

每个组件模块提供 `initialize_xxx(system_app)`（DB-GPT 风格），构造知识留在组件模块内。

**导入约束（防循环）**：`system_app.py` 模块级只 import `component.py`；具体组件模块的
import 全部发生在 `initialize_components()`/访问器**内部**（延迟）；组件模块允许在模块级
import 本模块的框架再导出（`BaseComponent/ComponentType`）或 `component.py` 本体，
也可 import `system_app`（运行期经 `get_app()` 横向取其他组件）。

### 4.3 两段式启动与逆序关闭

```
main.py lifespan
├─ startup:  AgentAdapterService.startup()
│    ├─ app = init_app()        # 同步：安装全局容器 → initialize_components（注册15组件+hook冻结）
│    │                          #       → 广播 on_init（同步钩子，如 skill 装载）
│    └─ await start_app(app)    # 异步：广播 async_before_start（主启动钩子）；置 _started=True
│                               # 幂等：已 _started 直接返回（防重复拉起后台任务）
└─ shutdown: await stop_app()
     ├─ 未 _started → 直接返回（lifespan 只在成功启动后才调 shutdown）
     ├─ 广播 async_before_stop（逆序，异常隔离，stop_event 幂等守卫）
     └─ 基础设施收尾（各 suppress）：redis.aclose → qdrant.shutdown → mysql.shutdown
        （db/ 模块级单例不属于任何业务组件，由容器统一兜底释放，close 幂等）
```

### 4.4 全局访问与测试缝

```python
get_app()      # 未初始化 → RuntimeError（fail fast）；进程内任意处获取容器
reset_app()    # 仅测试：卸载容器（注意：绝不调 hook_manager.reset()，会永久清空监听器）
app_started()  # 诊断：容器是否已完成启动
```

`AgentAdapterService` 是薄壳：`startup() → init_app() + start_app()`、
`shutdown() → stop_app()`、`stream_message/stop_session → get_app().runtime`。
容器未启动（lifespan 未跑完）时 `get_app()` 抛 RuntimeError，`stop_session`/alive 端点
捕获后按"不受理/不活跃"返回（与旧行为一致）。

---

## 5. 组件清单（职责 / 生命周期钩子 / 失败语义）

失败语义两种，沿袭旧 startup 七步约定：

- **降级**（suppress）：失败仅记日志，不阻断启动（预热/调度类）；
- **fail fast**：校验失败抛错，拒绝带病启动（注册表校验/配置校验）。

| # | 组件 | 实现类 | 启动钩子（async_before_start 除注明） | 失败语义 |
|---|---|---|---|---|
| 1 | llm_facade | LLMFacade | 预热默认模型（`preheat_default_model`）；关闭时 `close_llm_clients()` | 降级 |
| 2 | tool_registry | ToolRegistry | 构造即扫描 tools/ 目录（无钩子）；`child_view()` 产出过滤子视图 | — |
| 3 | skill_manager | SkillManager | `on_init()`（同步）→ load() | — |
| 4 | agent_registry | AgentRegistry | `load_from_config(config)` + `validate_against(tools, skills)`：工具/skill 名真实存在、persona 非空、name 不重复 | **fail fast** |
| 5 | context_service | ContextService | 无（无状态组装 + 骨架 TTL 缓存 60s/容量 200） | — |
| 6 | session_persistence | SessionPersistence | 无（纯落盘 + 按 (user,app,session) 锁表） | — |
| 7 | task_board | TaskBoardService | 无（磁盘持久任务图，ids 走参数） | — |
| 8 | memory_manager | MemoryFacade | qdrant 预热 + ensure_warm_collection | 降级 |
| 9 | session_summary | SessionSummaryService | 无（阈值状态 SummaryState 随会话生灭） | — |
| 10 | compaction | CompactionService | 无（llm_fn 默认韧性链；`make_breaker(session_id)` 产出会话级熔断器） | — |
| 11 | chat_message_store | ChatMessageStore | 无（MySQL DAO） | — |
| 12 | monitor_pipeline | MonitorPipeline | 无（监听器随模块加载注册） | — |
| 13 | monitor_maintenance | MonitorMaintenanceService | `start_periodic_maintenance`；关闭时 `stop_periodic_maintenance` | **fail fast** |
| 14 | memory_scheduler | MemoryScheduler | `startup()`（崩溃恢复 + 轮询循环）；关闭时 `shutdown(grace=10)` | 降级 |
| 15 | agent_runtime | AgentRuntime | 配置校验（默认智能体 model 空 / models 空 → RuntimeError）→ `start()`（session pool 清理循环 + dispatcher）；关闭时 `stop()` | 校验 **fail fast**，start 失败传播 |

---

## 6. 会话层（瘦身后形态）

### 6.1 运行期对象

```
RuntimeSessionState（SessionPool 管理，纯会话状态）
├─ request / 锁 / pending_requests / active_tasks / worker_task / stop_signal
├─ state / processing / closed / swapped_out / started_at / agent_name
└─ context_manager: SessionContext
     ├─ ids（session_id/app_id/user_id/db_name/agent_name）
     ├─ chat_messages: list[dict]          # 唯一大对象（P3 idle swap-out 卸载）
     ├─ prompts: TurnPrompts               # 每轮组装产物（memory/skill/task/summary_prompt、workspace_metadata）
     ├─ summary_state: SummaryState        # 摘要提取阈值状态
     └─ compact_breaker: CircuitBreaker    # 会话级压缩熔断器（compact:{session_id}，
                                           #   一个会话压缩失败不熔断别的会话）
```

### 6.2 会话生命周期监听器（hook 编排，实现于 session_pool.py）

- **SESSION_START `init_session_objects`**（p10）：创建 SessionContext、加载聊天历史快照、
  用户消息入库（MySQL，失败不阻断）、更新会话索引、state→RUNNING、按
  `metadata.agent_name` 归属智能体（空=默认）。
- **TURN_END `persist_chat_snapshot`**（p10）：chat_messages 快照落盘（经 `app.session_io`）。
- **swap-out（P3）**：闲置超 `swap_idle_seconds` 且无在途任务 → 卸载 chat_messages、标记
  `swapped_out`；下次请求 `_restore_swapped_session` 从快照按需重载。

### 6.3 服务化清单（方法保留，实例消失，ids 进签名）

| 旧每会话对象 | 新组件访问 | 备注 |
|---|---|---|
| SessionManager | `get_app().session_io` | 快照/工具日志/记忆日志/turn 快照/会话索引 |
| TaskManager（曾被双建） | `get_app().tasks` | 兜底重建问题天然消灭 |
| MemoryManager | `get_app().memory` | `load(user_id, app_id, query)` |
| SessionSummaryService | `get_app().summary` | LLM 走 `resilient_invoke(SCENARIO_SUMMARY)` |
| CompactionEngine | `get_app().compaction` | 熔断器留 SessionContext（会话级语义） |
| ContextAssembler | `get_app().context` | 无状态逻辑；`get_context_assembler()` 陷阱单例已删除 |

约定：服务方法签名用**关键字参数** `user_id=` / `app_id=` / `session_id=`，防维度错配
（hot 层是 app+user，快照/看板是 user+app+session）。

---

## 7. Hook 事件系统

### 7.1 收集 → 冻结 → 分发

```
模块加载：@on(event, name=, priority=, depends_on=) 注册裸函数对象到 hook_manager._pending
启动：    initialize_components() 末尾 load_and_freeze()
          ├─ 校验：事件已定义 / (event, name) 不重复 → 否则 ValueError 拒绝带病启动
          ├─ 按事件 Kahn 拓扑排序（depends_on 优先，同级按 priority+收集序）
          └─ 冻结：运行期注册 → RuntimeError
分发：    emit（serial，可短路）/ emit_parallel（并发隔离）/ waterfall（洋葱）
```

**触发方**（runtime.py）：`TURN_START / BEFORE_BUILD(waterfall) / BEFORE_LLM_INVOKE /
AFTER_LLM_INVOKE / BEFORE_TOOL_CALL / AFTER_TOOL_CALL / ON_COMPLETE / TURN_END /
SESSION_START / SESSION_END / INTERNAL_ON_ERROR`。
blocked 短路协议：BEFORE_TOOL_CALL blocked → 工具不执行，注入 blocked 工具消息继续对话。

### 7.2 监听器约束（**关键陷阱**）

- `@on` 装饰时注册**裸函数对象**，分发器直接 `callback(ctx)` 调用——**监听器必须无 self**：
  模块级函数，或类体内定义但不带 self 参数（内部经 `get_monitor_pipeline()` 等
  组件 getter 取实例）。带 self 的方法监听器每次分发都会 TypeError；
- 返回协议：`None`=继续；`HookDecision.block(message)`=短路阻断（返回普通字符串会被
  `normalize_decision` 静默忽略）；
- 新增监听器模块必须位于**组件依赖图或应用 import 链**上，否则收集不到
  （当前全链：main → router → 组件/工具模块）。

### 7.3 监听器清单（冻结后 10 事件 / 16 监听器）

| 事件 | 监听器（priority 序） | 模块 |
|---|---|---|
| session_start | init_session_objects(10) / report_session_start(100) | session_pool / monitor_pipeline |
| turn_start | report_turn_start(100) | monitor_pipeline |
| before_build | inject_dynamic_prompts（waterfall 透传层） | context_service |
| before_llm_invoke | report_prompt_tokens(100) | monitor_pipeline |
| after_llm_invoke | report_llm_usage(100) | monitor_pipeline |
| before_tool_call | file_tool_param_guard(20) / safe_path_guard(30) / report_tool_start(100) | tools/base / tool_executor / monitor_pipeline |
| after_tool_call | persist_tool_log(10) / report_tool_end(100) | session/manager / monitor_pipeline |
| on_complete | output_safety_check(10) | guardrail |
| turn_end | persist_chat_snapshot(10) / memory_turn_signal(20, depends_on snapshot) / report_turn_end(100) | session_pool / memory/trigger / monitor_pipeline |
| session_end | memory_session_end(20) / report_session_end(100) | memory_manager / monitor_pipeline |
| internal/on_error | report_error(100) | monitor_pipeline |

`file_tool_param_guard`（参数空值校验）与 `safe_path_guard`（safe_paths 越界/危险命令拦截，
迁自旧 ToolExecutor 内联安全检查）分工不同、命名区分；工具安全边界整体前置于 hook。

---

## 8. 多智能体（AgentSpec 注册表，已落地）

### 8.1 三条红线

1. **组件无状态**：组件不得持有 per-agent / per-session 可变字段（只读 spec 视图除外）；
2. **会话状态进 SessionContext**；
3. **智能体差异进 AgentSpec**：加一个智能体 = agents 段加一条配置，不改代码。

### 8.2 实现（agent/agent_registry.py）

```python
@dataclass
class AgentSpec:
    name / description / persona / tools / skills
    model_override: dict[scenario -> str | list[str]]
    limits: AgentConfig（max_steps/max_tool_iterations/temperature 覆盖）
    memory: MemoryPolicy(read_types, write_enabled, write_types)

class AgentRegistry(BaseComponent):        # 组件 #4
    get(name) -> spec     # None/未知名回落 default（不配 agents 段 = 单智能体现行为）
    default() / all_specs()
    async_before_start:  load_from_config(config) + validate_against(tool_names, skill_names)
                         # 校验 fail fast：name 重复/非默认智能体 persona 必填/工具与 skill 名存在
```

### 8.3 差异注入点

| 差异 | 注入点 |
|---|---|
| persona | `ContextService.render_turn_context(prompts, persona=spec.persona)`；空=内置模板 |
| 工具可见 | `app.tools.child_view(excluded, allowed=spec.tools)`（subagent_runner 复用） |
| 限额 | `spec.limits.max_steps/max_tool_iterations` 覆盖 runtime 默认 |
| 模型路由 | 解析序：`spec.model_override[scenario]` → `model_roles["{agent}:{scenario}"]` → `model_roles[scenario]` → 默认模型（`config.get_model_chain(scenario, agent=, agent_override=)`） |
| 记忆策略 | hot/warm 仍按 app+user 共享（用户级记忆跨智能体共享是特性）；防污染/防矛盾靠 MemoryPolicy 元数据过滤 + source_agent 标注，**起步不分片** |
| 会话归属 | 请求 `metadata.agent_name` → `RuntimeSessionState.agent_name`（空=默认） |

subagent 编排：`SubagentContext.agent_name` → `app.agents.get()` 取 spec → child_view
工具视图 + persona + 限额；主/子智能体只是调用拓扑差异，同一 AgentRuntime。

---

## 9. LLM 调用层（收编不重构）

调用面保持**模块函数**（不改为 `get_app().llm.xxx`）：

```
resilient_invoke[_stream](scenario, ...)   # 场景路由 + 三态熔断 + fallback 链（llm/resilience.py）
get_llm(model)                             # 模型级客户端缓存（复用 provider 级共享连接池）
```

SystemApp 只接管生命周期：`LLMFacade.async_before_start` 预热默认模型（降级）、
`async_before_stop` 调 `close_llm_clients()`（自 main.py lifespan 收编，幂等）。
会话侧不持有任何 LLM 客户端；compact/summary/memory 场景直达韧性层。

---

## 10. 最终形态一览

```
启动：main.py lifespan → AgentAdapterService.startup()
      → init_app()   [注册 15 组件 + hook 冻结 + 同步钩子]
      → start_app()  [异步钩子：预热(降级) / 注册表校验(fail fast) / 周期维护 / 调度器 / runtime]

运行期任意处：from codegenx.ai_service.system_app import get_app
      get_app().agents.get(...) / .tools.child_view(...) / .memory.load(...) / .tasks / ...
      LLM 调用：resilient_invoke(SCENARIO_X, ...)（模块函数，不经过 app）

关闭：stop_app() [组件逆序 async_before_stop（异常隔离）→ redis/qdrant/mysql]

会话数增长：每会话新增成本 ≈ chat_messages（固有，idle swap-out 压峰）+ 若干锁与小对象；
      组件/连接池/注册表实例数与会话数无关。
智能体数增长：组件零新增，agents 段加一条 AgentSpec。
```

### 红线速查

| 红线 | 原因 |
|---|---|
| 组件 `__init__` 首参必须是 `system_app` | `register` 以 `component(system_app)` 构造 |
| hook 监听器必须无 self | `@on` 注册裸函数，分发器直接 `callback(ctx)` |
| 新监听器模块必须在 import 链上 | 收集发生在模块加载，冻结后不可再注册 |
| initialize_components 注册顺序不可乱 | 顺序即依赖（如 runtime 构造取 tools；agents 校验依赖 tools/skills） |
| 业务代码只经 `get_app().xxx` 访问组件 | 防止第二套模块级单例 |
| 服务方法 ids 用关键字传参 | 防 user/app/session 维度错配 |
| 组件不得持有会话/智能体可变状态 | 多租户多智能体下串话比崩溃更难排查 |

---

## 11. 测试

| 文件 | 覆盖 |
|---|---|
| tests/test_system_app.py | 未初始化 fail fast；init_app 装配 15 组件 + hook 冻结 + 幂等；访问器缺组件 ValueError；start/stop 幂等；广播顺序（注册序/逆序）；关闭异常隔离；runtime 空 model 拒启动；框架再导出等价 |
| tests/test_p4_agent_registry.py | AgentSpec/AgentRegistry 校验、from_config 装配、模型路由三级覆盖、persona 渲染 |
| tests/test_p3_subagent_swap.py | child_view 过滤视图零重扫；swap-out 条件/幂等/恢复 |
| tests/test_hook_*.py | 注册校验/分发语义/事件序列/span 层级 |
| tests/test_system_services.py | 服务化回归（任务看板/摘要/压缩等） |
| tests/integration_test.py | 需本地起真实服务（uv run python -m codegenx）后运行 |

注意：`hook_manager` 全局唯一且监听器只在 import 时收集——测试中**严禁**调用
`hook_manager.reset()`（会永久清空监听器）；需要隔离容器时只用 `reset_app()`。
