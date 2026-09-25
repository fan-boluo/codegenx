# SystemApp 运行时架构设计

> 版本 v1.1（2026-09-25）。目标：引入全局容器 SystemApp 统一持有工具/记忆/skill 等组件，
> 启动时注册、全局生命周期管理、项目任意处可获取；把 RuntimeSessionState 与 SessionContext
> 瘦身为「纯会话状态」，消除每会话重复创建的无状态对象与扫描浪费。
>
> v1.1 修订：rebase 至 LLM 调用层 P0/P1 落地之后（3d9cd83 连接池复用+场景路由、
> ebcf1e6 韧性层、18d65be 压缩熔断统一）。原 v1 设计的 LLMHub（按模型缓存客户端池）
> 已被 `llm/client_registry.py` + `get_llm()` + 韧性层实现并超出（多了熔断/fallback 链/
> 场景路由），本方案对 LLM 从「新建池」改为「收编生命周期」（§5）。
>
> v1.2 修订：新增 §10 多智能体扩展（AgentSpec 注册表）——规划/数据探查/分析/输出展示
> 等多个智能体共用全局组件，差异做成声明式规格而非代码分支；最终形态一览移至 §11。

---

## 0. 结论先行

**方向是对的，但要对症下药。** 盘点结论（证据见 §1）：

| 问题 | 真实严重度 | 说明 |
|---|---|---|
| ~~每会话 new 一个 `AsyncLLMClient`（httpx 连接池）~~ | **已解决（LLM P0/P1）** | `client_registry.get_openai_client` provider 级共享连接池 + `get_llm()` 按模型 lru_cache；各调用方已迁 `resilient_invoke`（场景路由+熔断+fallback 链） |
| subagent 每次运行重建 `ToolRegistry`（目录扫描）+ `AgentRuntime` + `SessionPool` | **高** | 每次子代理调用都做一遍启动级装配（subagent_runner.py:44-52 现状未变） |
| `chat_messages` 完整驻留内存 | **高（内存真正大头）** | 这是会话固有状态，不能全局化，只能做 idle 卸载（§7） |
| 每会话 new `MemoryManager`/`TaskManager`/`SessionManager`/`SessionSummaryService`/`CompactionEngine`/`ContextAssembler` | **低（内存）/ 高（架构）** | 这些对象只持 id 字符串和路径（各几百字节），内存上无感；但 5 种单例风格并存、生命周期散落、`TaskManager` 被创建两次、`get_context_assembler` 是个一旦使用就跨会话串话的陷阱单例 |
| skill 缓存挂在 `SessionContext` 类属性上 | 中（归属错位） | 全局数据放在会话类上，语义颠倒 |

因此方案分三条线：
1. **SystemApp 容器**（§3）：把代码里已自发形成的 10+ 个零散单例显式化，统一注册、生命周期、访问入口——解决的是架构正确性与资源复用；
2. **会话对象瘦身**（§4）：会话只留真状态，"只持 ids 的伪对象"全部服务化——解决的是职责混乱与扫描浪费；
3. 内存峰值若仍是痛点，再上 **idle 会话卸载**（§7）。

LLM 调用层不在改造范围内——它已经是「全局组件」的正确形态（provider 级连接池 +
模型级客户端缓存 + `provider:model` 粒度全局熔断器 + 场景模型链），SystemApp 只接管
其生命周期收尾与观测聚合（§5）。

---

## 1. 现状盘点

### 1.1 每会话实例化链（证据）

会话创建入口 `SessionPool.get_or_create`（agent/session_pool.py:114）→ `on_session_start`
hook `init_session_objects`（agent/runtime.py:921-962）：

```
RuntimeSessionState(session_id, request, runtime)
├─ SessionManager(user_id, app_id, session_id)        runtime.py:933  纯落盘服务，只持目录+Lock
├─ TaskManager(app_id, session_id, user_id)           runtime.py:936  只持磁盘路径；又被 SessionContext 兜底重建一次（session_context.py:48）
└─ SessionContext(...)                                 runtime.py:939
    ├─ MemoryManager(session_id, app_id, user_id)      session_context.py:47  只有 3 个 id 字符串，无状态
    ├─ TaskManager（兜底第二次创建）                    session_context.py:48
    ├─ SessionSummaryService(...)                      session_context.py:49  ids；LLM 走 resilient_invoke(SCENARIO_SUMMARY)
    ├─ CompactionEngine(llm_fn=resilient_invoke 链式)  session_context.py:51  ★ llm_fn 已走韧性层（P1），无客户端问题
    │                                                   ★ 引擎除熔断器外无状态；熔断器为韧性层三态
    │                                                     CircuitBreaker("compact:{session_id}")，每会话一个（compact.py:302-310）
    └─ ContextAssembler()                              session_context.py:33  有每轮可变状态（memory_prompt/workspace_metadata…）
```

LLM 客户端已无每会话问题（LLM P0/P1）：`AsyncLLMClient.__init__` 复用 provider 级
共享 `AsyncOpenAI`（llm/async_client.py + llm/client_registry.py:23），
`get_llm()` 按模型 `@lru_cache(maxsize=8)` 缓存（async_client.py:247），
agent 流式/compact/summary/memory 四场景统一走 `resilient_invoke[_stream]`
（llm_recovery.py:65、session_context.py:51、session_summary.py:214、schedule/memory.py:542）。

### 1.2 已是全局的组件（无需搬进 SystemApp 的实现，只需收编引用）

| 组件 | 现有形态 | 位置 |
|---|---|---|
| config（含 `model_roles` 场景路由 + `llm.*` 韧性参数） | 模块级单例 | utils/config.py |
| **LLM 调用层**（P0/P1）：provider 级共享连接池 / 模型级客户端缓存 / `provider:model` 粒度全局熔断器 / fallback 链 | `get_openai_client()` / `get_llm()` / `get_breaker()` / `resilient_invoke[_stream]` 模块函数 | llm/client_registry.py、llm/async_client.py、llm/resilience.py |
| hook_manager | 模块级单例 | hook/core.py |
| ToolRegistry / 工具实例 | `get_tool_registry()` 惰性单例，工具实例无会话状态（user/app 每次调用注入） | agent/tool_handler.py:151 |
| chat_message store | `get_chat_message_store()` | chat_message/store.py:265 |
| memory scheduler | `get_memory_scheduler()` | schedule/memory.py:552 |
| monitor 全家 | `get_monitor_pipeline()` 等 | monitor/ |
| memory hot/warm/store/vector | 模块级函数，Redis key 按 `app+user` 维度（**本来就不是 session 维度**） | memory/ |
| embedding 客户端 | `@lru_cache` 单例 | memory/embedding.py:88 |
| redis / mysql / qdrant 连接池 | 模块级 | db/ |
| skill 缓存 | `SkillLoader._skills_cache` 类属性（全局一份，但挂在会话类上） | context/session_context.py:35-36 |

LLM 调用层是「全局组件」的**现有最佳样板**：模块函数 + 进程级字典，无类单例样板代码，
`close_llm_clients()` 由 main.py lifespan 调用（main.py:69）——SystemApp 落地后这类
生命周期挂接统一收进容器（§3.3/§5）。

### 1.3 内存量化（诚实评估）

以 100 并发会话估算：

| 项 | 单会话占用 | 100 会话 | 归属 |
|---|---|---|---|
| 无状态小对象（MemoryManager/TaskManager/SessionManager/Summary/Assembler/压缩熔断器） | ~几 KB | ~几百 KB | 服务化后→0 |
| ~~AsyncLLMClient（httpx pool 空载/满载）~~ | ~~~几十 KB / 池内最多 10 连接~~ | **已解决** | LLM P0：provider 级共享，池数=provider 数 |
| chat_messages（长会话，有 micro_compact 控制工具结果但历史本身不清） | 数百 KB~数 MB | **数十~数百 MB** | §7 idle 卸载 |
| RuntimeSessionState 锁/队列/turn | 固有 | — | 保留 |

结论：**「每会话一份无状态组件」主要矛盾不是内存而是架构**；连接池项已被 LLM P0 消掉，
内存剩余大头只有 chat_messages（§7）。

---

## 2. 设计原则：三层模型

```
┌─────────────────────────────────────────────────────────┐
│ SystemApp（进程级，全局一份，启动装配/关闭回收）              │
│  组件 = 无状态服务 | 注册表 | 客户端池 | 后台任务            │
├─────────────────────────────────────────────────────────┤
│ SessionState（会话级，SessionPool 管理，随会话生灭）          │
│  chat_messages / system_prompt / TurnPrompts / 锁 / turn  │
├─────────────────────────────────────────────────────────┤
│ TurnState（请求级，已有 ActivateTurn）                      │
└─────────────────────────────────────────────────────────┘
```

**四问判别法**（新代码决定东西放哪时逐条过）：

1. 它随会话生灭吗？（否 → 候选组件）
2. 它的属性里除了 `session_id/app_id/user_id/路径` 还有什么？（只有 ids → **伪会话对象**，服务化，ids 作调用参数）
3. 它有每轮被覆写的可变字段吗？（有 → TurnPrompts/会话状态；无 → 组件）
4. 它持有连接/线程/后台任务吗？（有 → 必须进 SystemApp，由全局生命周期管理）

---

## 3. SystemApp 容器设计

### 3.1 结构

新文件 `backend/src/codegenx/ai_service/system_app.py`：

```python
"""SystemApp —— 全局组件容器（组合根）。

启动时装配全部进程级组件并管理生命周期；项目任意处通过 get_app() 获取。
分层原则与判别法见 docs/SystemApp架构设计.md §2。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class SystemApp:
    # ── 配置与总线 ────────────────────────────────────────────
    config: Config                       # 引用 utils/config.py 的单例（不复制）
    hooks: HookManager                   # hook/core.py 单例

    # ── LLM ──────────────────────────────────────────────────
    llm: LLMFacade                       # §5：llm/ 调用层的生命周期与观测门面（不复制状态，
                                         #   调用面仍是 resilient_invoke / get_llm 模块函数）

    # ── 注册表 ────────────────────────────────────────────────
    tools: ToolRegistry                  # 迁自 runtime 内部持有，启动扫描一次
    skills: SkillRegistry                # 迁自 SessionContext 类属性
    agents: AgentRegistry                # §10：多智能体规格（不配置=单一默认智能体，现行为）

    # ── 无状态服务（ids 作参数）────────────────────────────────
    context: ContextService              # §5.4：workspace 元数据/骨架/组装
    compaction: CompactionService        # §5.3：压缩逻辑（熔断器留会话）
    summary: SessionSummaryService       # §5.3：ids 参数化
    session_io: SessionPersistence       # §5.3：迁自 SessionManager
    tasks: TaskBoardService              # §5.3：迁自 TaskManager
    memory: MemoryFacade                 # §5.3：迁自 MemoryManager（只读门面）

    # ── 存储与后台任务 ────────────────────────────────────────
    chat_messages: ChatMessageStore      # get_chat_message_store() 收编
    monitor: MonitorPipeline
    memory_scheduler: MemoryScheduler

    _started: bool = False
    _startup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...


_app: SystemApp | None = None


def get_app() -> SystemApp:
    """进程内任意处获取容器。未初始化时抛错（宁可 fail fast 也不静默兜底）。"""
    if _app is None:
        raise RuntimeError("SystemApp 未初始化：请在应用启动入口调用 init_app()/startup()")
    return _app


def init_app(app: SystemApp | None = None) -> SystemApp:
    """创建并安装容器（main.py 启动时调用；测试可传入自制实例）。"""
    ...


def reset_app() -> None:
    """仅测试用：卸载容器，配合 pytest fixture。"""
```

### 3.2 访问方式：为什么是 `get_app()` 而不是逐层依赖注入

- 深层异步代码（hook 监听器、工具执行、后台 worker）没有 request 上下文可挂依赖，
  FastAPI `Depends` 只在路由层有效；把 app 作为参数层层传递会把签名全部污染。
- 进程级单容器 + 显式 init/reset 是标准折中；可测试性通过 `init_app(自制实例)` /
  `reset_app()` 保留（等价于手动 DI 的测试缝）。
- 约束：**只有组件装配（system_app.py）允许 import 具体实现类；业务代码一律
  `get_app().xxx` 访问**，避免出现第二套模块级单例。

### 3.3 生命周期：startup 七步（顺序即依赖）

收编 `AgentAdapterService.startup()`（services/agent_adapter_service.py:34-58）的散落逻辑：

| 步 | 内容 | 现状来源 |
|---|---|---|
| 1 | config 校验（agent/model/embedding 必填项） | 新增 |
| 2 | `hooks.load_and_freeze()` | startup() 现有 |
| 3 | 基础设施 warmup：qdrant 预热 + ensure_warm_collection（失败降级不阻断） | startup() 现有 |
| 4 | 注册表装载：ToolRegistry 目录扫描一次、SkillRegistry 加载 | 现散在首次使用时 |
| 5 | 服务装配：LLMFacade（收编 llm/ 模块生命周期）+ context/compaction/summary/session_io/tasks/memory 服务实例化 | 新增（llm/ 模块函数不动） |
| 6 | 引擎启动：`AgentRuntime.start()`（session pool 清理循环、dispatcher） | startup() 现有 |
| 7 | 后台任务：monitor 周期维护、memory scheduler（宽限关闭） | startup() 现有 |

`shutdown()` 严格逆序：后台任务 → runtime.stop → 服务层 → **LLM 共享连接池
（`close_llm_clients()`，从 main.py lifespan 移入）** → redis/qdrant/mysql 连接池。

`AgentAdapterService` 瘦身为薄壳：`startup() → init_app().startup()`，
`shutdown() → get_app().shutdown()`；路由层接口不变。

### 3.4 与现有 get_xxx() 单例的关系：收编为薄委托

不做一刀切大改（34 处 `session_manager|task_manager` 引用、大量 `get_monitor_pipeline()`
调用）。策略：**get_xxx() 函数签名保留，内部改为从 SystemApp 取**，例如：

```python
@lru_cache(maxsize=1)  # 删除
def get_monitor_pipeline() -> MonitorPipeline: ...
# →
def get_monitor_pipeline() -> MonitorPipeline:
    return get_app().monitor
```

P0 阶段它们暂时维持原样（SystemApp 引用它们），P2 起逐步反转方向。最终形态：
模块级 get_xxx() 仅保留高频使用的少数几个，其余直接 `get_app().xxx`。

---

## 4. 会话对象瘦身

### 4.1 RuntimeSessionState：before → after

| 字段 | 处置 | 理由 |
|---|---|---|
| `runtime: AgentRuntime` | **删除** | 引擎是全局组件；全库仅 telemetry_schema.py 1 处使用 `session.runtime`，改为 `get_app()`/参数传入 |
| `session_manager` | **删除** | 伪会话对象 → `app.session_io`（ids 作参数），34 处引用随服务化改写 |
| `task_manager` | **删除** | 同上 → `app.tasks`；顺带消灭「创建两次」问题 |
| `context_manager` | **保留（改名 context）** | 真会话状态载体 |
| request/锁/队列/active_tasks/activate_turn | 保留 | 真会话状态 |
| session_record/root_span_*/监控字段 | 保留 | 真会话状态 |
| tool_iterations/last_tool_signature/consecutive_same_tool_calls | 保留 | 真会话状态 |
| 生命周期字段（state/processing/closed/stop_signal/worker_task…） | 保留 | 真会话状态 |

### 4.2 SessionContext：before → after

```python
@dataclass
class SessionContext:
    # ── 身份（定位数据与目录）──────────────────────────────
    session_id: str = ""
    app_id: str = ""
    user_id: str = ""
    db_name: str | None = None

    # ── 真会话状态 ────────────────────────────────────────
    system_prompt: str = ""
    chat_messages: list[dict[str, Any]] = field(default_factory=list)   # 唯一大对象
    prompts: TurnPrompts = field(default_factory=TurnPrompts)           # 每轮组装产物
    compact_breaker: CircuitBreaker | None = None                       # 压缩熔断器（会话级语义）
    # compact_breaker 即韧性层三态 CircuitBreaker（P1 已在用，key="compact:{session_id}"，
    # compact.py:302-310）——服务化时从 CompactionEngine 迁到此处，实现与语义均不变

    # ── 方法：全部委托 SystemApp 服务 ─────────────────────
    async def build_system_prompt(self, query): → app.context + app.memory + app.skills + app.tasks
    async def compact_after_step(self):     → app.compaction.compact_if_needed(self.key, self.chat_messages, self.compact_breaker)
    ...
```

删除的实例字段与去向：

| 现字段 | 去向 |
|---|---|
| `memory: MemoryManager` | `app.memory.load(user_id, app_id, query)` |
| `task: TaskManager`（及入参 task_manager） | `app.tasks.xxx(app_id, session_id, user_id)` |
| `_session_summary` | `app.summary.xxx(ids)` |
| `_compaction` | `app.compaction.xxx(...)`（三态熔断器留会话，见 §4.3） |
| `assembler: ContextAssembler` | 拆分，见 §4.4 |
| 类属性 `skill_loader` / `skills` | `app.skills`（全局注册表） |

```python
@dataclass
class TurnPrompts:
    """每轮组装产物（原 ContextAssembler 的可变字段）。"""
    memory_prompt: str = ""
    skill_prompt: str = ""
    task_prompt: str = ""
    session_summary_prompt: str = ""
    workspace_metadata: dict = field(default_factory=dict)
```

### 4.3 伪会话对象服务化清单

统一模式：**方法保留，实例消失，ids 进签名**。

| 现对象 | 新服务 | 改造点 |
|---|---|---|
| `SessionManager`（session/manager.py） | `app.session_io: SessionPersistence` | 3 个 id 从 `__init__` 移到方法参数；`@on(AFTER_TOOL_CALL) persist_tool_log`（manager.py:145）改从 `get_app()` 取服务 |
| `TaskManager`（task/task_manager.py） | `app.tasks: TaskBoardService` | 路径推导改由每次调用传 ids；tools/task.py 13 处引用改写 |
| `MemoryManager`（memory/memory_manager.py） | `app.memory: MemoryFacade` | `load(query)` → `load(user_id, app_id, query)`；内部模块函数不动 |
| `SessionSummaryService`（compact/session_summary.py） | `app.summary` | ids 参数化；LLM 已走 `resilient_invoke(SCENARIO_SUMMARY)`（P1），无需再动 |
| `CompactionEngine`（compact/compact.py:280） | `app.compaction: CompactionService` | 逻辑+llm_fn 全局（llm_fn 已是 `resilient_invoke(SCENARIO_COMPACT)` 链式调用）；三态熔断器 `compact:{session_id}` 留在 SessionContext（会话级熔断语义：一个会话压缩失败不应熔断别的会话；P1 已统一为韧性层 CircuitBreaker，迁移即可） |

### 4.4 ContextAssembler 拆分（含陷阱删除）

`ContextAssembler` 现在混着两类东西：

- **无状态逻辑**（可全局）：`build_directory_skeleton`、`_normalize_history`、
  `assemble` 拼接、`prepare_turn_context` 的模板渲染 → `app.context: ContextService`。
  骨架扫描可加 `(user_id, app_id) → skeleton` 的 TTL 缓存（LRU，容量 ~200）。
- **每轮可变状态**（必须会话级）：`memory_prompt/skill_prompt/task_prompt/
  session_summary_prompt/workspace_metadata/base_prompt` → `SessionContext.prompts:
  TurnPrompts`。

**删除 `get_context_assembler()`（assembler.py:221-223）**：它是 `lru_cache` 单例，
但 ContextAssembler 有每轮可变状态——现在无人调用是运气，一旦被用就是跨会话串话事故。
`BEFORE_BUILD` 洋葱监听器 `inject_dynamic_prompts`（assembler.py:229）保留在
ContextService 侧。

### 4.5 SkillRegistry

`SkillLoader._skills_cache` 类属性（skill/skill_loader.py:23）→ `app.skills:
SkillRegistry`，启动时装载一次。三处各自 `SkillLoader()` 的使用点
（session_context.py:35、skill_loader.py:106、tools/skill.py:35）统一改从 `app.skills`
取；skill 文件热更新（若需要）通过 Registry 的显式 `reload()` 而不是再 new loader。

---

## 5. LLM 调用层：已由 P0/P1 落地，SystemApp 只做收编

> v1 原方案设计了 LLMHub（按模型缓存客户端池）。LLM 调用层改造（docs/LLM调用设计方案.md
> P0+P1）已把它完整实现并扩展，**本方案不再新建任何 LLM 设施**。

现状（llm/ 模块，全部进程级）：

| 能力 | 实现 | 位置 |
|---|---|---|
| provider 级连接池 | `get_openai_client(provider, cfg)`：每 provider 一个共享 `AsyncOpenAI`（httpx 池），`max_retries=0`，坏配置快速失败 | llm/client_registry.py:23 |
| 模型级客户端缓存 | `get_llm(model)`：`@lru_cache(maxsize=8)`，包装共享 provider 客户端 + usage 采集 | llm/async_client.py:247 |
| 场景路由 + fallback 链 | `config.model_roles` → `get_model_chain(scenario)`；agent_main/compact/summary/memory 四场景 | utils/config.py + llm/resilience.py |
| 三态熔断器 | `get_breaker(model)`：`provider:model` 粒度全局字典，closed→open→half_open | llm/resilience.py:66-168 |
| 韧性执行 | `resilient_invoke[_stream](scenario, ...)`：分类重试 → 逐级 fallback；流式仅首 chunk 前可重试 | llm/resilience.py:232/297 |
| 生命周期 | `close_llm_clients()` 释放连接池 | llm/client_registry.py:57（现挂 main.py lifespan） |

SystemApp 对 LLM 的全部动作：

1. **`app.llm: LLMFacade`**（轻门面，不复制状态）：`startup()` 可选预热默认模型
   （首次请求免冷启动）；`shutdown()` 调 `close_llm_clients()`（从 main.py lifespan
   移入，main.py 相应行删除）；聚合观测 `circuit_snapshot()`（管理端点用）。
2. **调用面保持模块函数**：业务代码继续 `resilient_invoke(SCENARIO_X, ...)` /
   `get_llm(model)`，**不改成 `get_app().llm.xxx`**——llm/ 是模块函数形态的成熟组件
   （本方案的样板），再包一层只增加间接性。
3. 会话侧无需任何 LLM 字段：SessionContext 不持有客户端（v1 问题的根源已消失），
   compact/summary 场景经 `llm_fn`/`resilient_invoke` 直达韧性层。

---

## 6. subagent 复用

现状（agent/subagent_runner.py:44-52）：每次运行 new `ToolRegistry()`（触发目录扫描）
+ new `AgentRuntime`（连带新 SessionPool/MessageBus）+ new `ToolExecutor`。

改造：

```python
class SubagentRunner:
    async def run(self, subagent_context: SubagentContext) -> dict[str, Any]:
        app = get_app()
        tools_handler = app.tools.child_view(excluded=DEFAULT_CHILD_EXCLUDED_TOOLS,
                                             allowed=subagent_context.allowed_tools)
        runtime = AgentRuntime(tool_executor=ToolExecutor(tools_handler))  # 轻量，复用注册表
        ...
```

- `ToolRegistry.child_view()`：基于全局注册表产出过滤视图（浅拷贝 tool 列表，**不重扫目录**）。
- AgentRuntime 实例本身很轻（引用都是全局资源），每次 new 可接受；随 P2 可进一步共享。
- subagent 的 LLM 调用已随 P0/P1 走 `resilient_invoke`（经 CompactionEngine/Summary 继承），
  无客户端问题；剩余改造只有 CompactionEngine/Summary 的服务化跟随 §4.3。

---

## 7. idle 会话内存卸载（可选增强，压内存峰值的关键）

SystemApp 落地后，会话内存剩余大头只有 `chat_messages`。现有机制已具备条件：

- idle 超时（默认 1800s）会直接 close 会话（session_pool 清理循环）；
- 快照落盘已存在（`persist_chat_snapshot`，turn_end 每轮保存）；
- 快照重载已存在（`init_session_objects` 从 `get_turn_chat_message_snapshot()` 恢复）。

新增中间档 **swap-out**：闲置超过 `swap_idle_seconds`（如 300s，远小于 close 超时）且无
active_tasks 的会话，`chat_messages = []`、标记 `swapped_out=True`；下次请求到来时
`on_session_start` 已有的快照重载逻辑按需恢复。收益：长闲置会话内存归零，同时保留
session 连续性（不必等 close 后重建整个 RuntimeSessionState）。

---

## 8. 实施计划

| 阶段 | 内容 | 风险 | 验证 |
|---|---|---|---|
| **P0** 容器骨架 | system_app.py + startup/shutdown 收编（引用现有单例，不改它们）；`close_llm_clients()` 从 main.py lifespan 移入 shutdown；AgentAdapterService 变薄壳 | 低（纯装配移动，行为不变） | 新增 test_system_app（startup 幂等/shutdown 逆序/未初始化 fail fast）；现有 31 个 hook 测试全绿 |
| **P1** 陷阱删除 + assembler 拆分 | 删 get_context_assembler；ContextAssembler 无状态部分 → ContextService（骨架 TTL 缓存）、可变部分 → TurnPrompts | 低 | 组装单测（同入参同产物）；对话冒烟 |
| **P2** 会话瘦身 | RuntimeSessionState 去 runtime/session_manager/task_manager；五对象服务化（§4.3，含压缩熔断器迁 SessionContext）；SkillRegistry 迁移 | 中（34 处引用改写，集中在 6 文件） | 全量回归：对话冒烟（快照存/读、压缩触发+熔断恢复、任务看板、记忆注入）+ 4 套 hook 测试 |
| **P3** subagent 复用 + swap-out | ToolRegistry.child_view；闲置卸载档 | 中 | subagent 并发冒烟；swap-out/恢复用例 |
| **P4** 多智能体（§10，依赖 P2/P3） | AgentSpec/AgentRegistry + config agents 段扩展；ContextService 按 spec 参数化；SkillRegistry.for_agent；模型路由 agent 覆盖；记忆 source_agent 元数据 + MemoryPolicy；subagent 工具接 agent_name | 中 | 多 agent 冒烟（规划→探查→分析链路）；记忆来源过滤用例；模型路由覆盖用例；单 agent 回归（不配新字段=现行为不变） |

> 原 v1 的「P1 LLMHub」阶段整体删除——LLM 调用层 P0/P1 已在 3d9cd83/ebcf1e6/18d65be
> 落地（连接池/场景路由/韧性层/压缩熔断统一），本方案只剩生命周期收编（并入 P0）。

依赖关系：P0 → P1 → P2 → P3 串行；P1 可与 P2 的 assembler 拆分并行。

### 测试缝

```python
@pytest.fixture
def app():
    test_app = SystemApp(config=test_config, hooks=hook_manager, ...)  # 或轻量 stub
    init_app(test_app)
    yield test_app
    reset_app()
```

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| `get_app()` 在 import 期被调用（此时未 init） | 约定只在运行期调用；init 前调用抛 RuntimeError，测试可显式 init；启动链路里组件装配全部延迟到 startup() |
| 服务化后 ids 传错维度（hot 层是 app+user，快照是 user+app+session） | 服务方法签名用关键字参数 `user_id=`, `app_id=`, `session_id=`，禁位置传参 |
| TaskManager 双建的历史行为差异（兜底创建掩盖了 hook 未执行的 bug） | 服务化后天然消灭；回归时确认 on_session_start 失败路径不再静默 |
| `close_llm_clients()` 收编时双重关闭（main.py lifespan 与 SystemApp.shutdown 都调） | 迁移时删除 main.py 的直接调用；且该函数本身幂等（close 后 clear 字典），残留调用无害 |
| 压缩熔断器迁移（CompactionEngine → SessionContext 字段）丢失会话熔断状态 | P2 迁移保持 `compact:{session_id}` key 与三态参数不变；用「连续失败→open→冷却后半开恢复」用例回归（P1-6 行为） |
| ContextService 骨架缓存失效（工作区目录变化） | TTL 60s + 显式 invalidate 钩子（文件工具写盘后可选触发） |
| AgentSpec 配置错误（工具名/skill 名写错、persona 空、name 重复） | AgentRegistry 启动校验 fail fast（沿用 hook 冻结校验的思路），拒绝带病启动 |
| 记忆 source_agent 过滤拖慢注入 | 召回仍按 app+user 不加维度；来源过滤放在重排/格式化阶段（数据已在内存，零额外 IO） |
| 多智能体下组件混入智能体态（比会话串话更隐蔽） | §10.1 红线 + code review 约束：组件只读 spec 视图，差异一律走 AgentSpec |

---

## 10. 多智能体扩展（AgentSpec 注册表）

> 场景：规划 / 数据探查 / 分析 / 输出展示 等多个专职智能体。核心结论：**全局组件
> 在多智能体下不是问题而是前提**（N 个智能体共享一份基础设施，成本 O(1)）；缺的不是
> 「分」，而是一层**声明式的智能体规格**——差异做成数据（AgentSpec），不做成代码分支。
> 本节是 v1.1 的叠加，不推翻任何已有设计。

### 10.1 三条红线（先立规矩）

1. **组件无状态**：SystemApp 组件不得持有 per-agent 或 per-session 可变字段（只读
   spec 视图除外）——多智能体下组件串话比会话串话更隐蔽；
2. **会话状态进 SessionContext**：每个智能体的每个会话都只是轻状态，引擎与组件共享；
3. **智能体差异进 AgentSpec**：persona、工具/skill 可见范围、模型链、限额、记忆策略
   全部是注册表里的一条数据；加一个智能体 = 加一条配置，不改代码。

### 10.2 AgentSpec 结构

```python
@dataclass
class MemoryPolicy:
    """智能体的记忆读写策略（hot/warm 仍按 app+user 全局共享，见 §10.5）。"""
    read_types: list[str] | None = None    # 注入时允许的类型过滤；None=全部
    write_enabled: bool = True             # 是否参与记忆提炼（纯执行类智能体可关写，防污染）
    write_types: list[str] | None = None   # 允许提炼成的记忆类型；None=不限


@dataclass
class AgentSpec:
    name: str                              # 唯一标识：planner / data_explore / analyze / visualize ...
    description: str = ""                  # 给规划智能体的派活描述（subagent 工具的 description 来源）
    persona: str = ""                      # system prompt 模板（替代硬编码 DEFAULT_PROMPT_TEMPLATE）
    tools: list[str] | None = None         # 工具 allowlist；None=全部（默认智能体行为）
    skills: list[str] | None = None        # skill allowlist；None=全部（现状 build_skill 全量注入）
    model_override: dict[str, str | list[str]] = None  # 场景→模型/链覆盖，见 §10.4
    limits: AgentConfig | None = None      # max_steps/temperature/max_tool_iterations（沿用 AgentConfig）
    memory: MemoryPolicy = field(default_factory=MemoryPolicy)
```

`SystemApp` 增加一个注册表组件：

```python
class AgentRegistry:
    """AgentSpec 存取 + 启动校验（工具名/skill 名存在、persona 非空、name 不重复）。"""
    def get(self, name: str | None) -> AgentSpec   # None/未知名回落 default spec（现单智能体行为）
    def default(self) -> AgentSpec                  # 不配置任何新字段时的等价 spec
```

现有 `config.agents`（AgentConfig）只表达 LLM 参数；扩展为 AgentSpec 的超集
（新增 persona/tools/skills/modelOverride/memory 段），旧配置无新字段时与现状完全等价。

### 10.3 差异注入点：全部参数化，不新建机制

| 差异 | 现状 | 扩展后 |
|---|---|---|
| persona | `DEFAULT_PROMPT_TEMPLATE` 硬编码（assembler.py:70） | `ContextService.build(spec, ...)` 按 spec.persona 渲染 |
| 工具可见 | 全量（subagent 已有 excluded/allowed 雏形） | `app.tools.child_view(spec.tools)`（§6 为 subagent 设计，直接复用） |
| skill 可见 | `build_skill()` 全量注入（skill_loader.py:89） | `app.skills.for_agent(spec)` 按 allowlist 过滤 |
| 限额 | AgentConfig 全局一份 | spec.limits 覆盖默认值 |
| 会话归属 | 无智能体概念 | `RuntimeSessionState.agent_name` + 请求路由（缺省 default） |

### 10.4 模型路由：scenario → agent 覆盖，向后兼容

`config.model_roles` 现按场景路由（agent_main/compact/summary/memory）。多智能体下
路由维度是 **智能体 × 场景**，解析顺序（`config.get_model_chain` 扩展）：

```
spec.model_override[scenario]      # 该智能体对该场景的显式配置（最高）
→ model_roles["{agent}:{scenario}"]  # 全局按智能体细分（可选写法）
→ model_roles[scenario]            # 现有全局场景路由（现状不变）
→ [默认模型]
```

典型用法：规划用强模型、探查/展示用便宜模型——只写 override，不动全局链。

### 10.5 记忆：全局共享 + 来源标注，**起步不分片**

hot/warm 按 `app+user` 是**用户级**记忆，跨智能体共享是特性：规划智能体记下的硬约束，
分析/展示智能体必须遵守；用户偏好不该每个智能体重复学习。按 agent 分 key 会碎片化
（重复提取、协作断裂、hot 层预算被稀释成 N 份）。

要防的两个问题都用元数据解决，不动 key：

1. **污染**（探查智能体的大量 schema 细节干扰规划）：提炼管道写入时带 `source_agent`
   元数据（现有 type 字段体系直接扩展）；`MemoryFacade.load` 注入时按 spec.memory 过滤
   （read_types / 来源可见性）。`write_enabled=False` 的纯执行智能体不产生新记忆。
2. **矛盾**（不同智能体对同一事实提取出冲突记忆）：现有冲突消解规则已覆盖
   （hot>warm>时间新>用户当前指令最高），无需新机制。

将来确需智能体私有记忆（如探查的临时 schema 笔记），先用元数据过滤表达；真不够再分
namespace——**起步不要分片**。

### 10.6 编排：subagent 模式升格，不新建框架

`SubagentTaskTool`（tools/subagent.py，带 allowed_tools/max_turns/描述/计划锁定）已经
是「规划智能体派活给执行智能体」的原型。扩展为：

- `SubagentContext.agent_name` 指定目标智能体；`SubagentRunner` 从 `app.agents.get()`
  取 spec → child_view 工具视图 + for_agent skill 视图 + spec.persona；
- 规划智能体把探查/分析/展示当工具调用；主/子智能体只是调用拓扑差异，不是两套运行时；
- 任务看板（`app.tasks`，服务化后全局可取）作为跨智能体的共享工作面。

### 10.7 配置示例（config.json agents 段扩展）

```json
{
  "agents": [
    {
      "id": "planner", "name": "规划", "defaults": true,
      "persona": "你是数据应用的规划师：拆解需求、制定计划、通过 subagent 工具派发探查/分析/展示任务并验收结果。",
      "description": "规划与任务派发",
      "tools": ["subagent", "task", "file"],
      "skills": ["planning"],
      "modelOverride": { "agent_main": ["qwen3.8-plus", "qwen3.8-flash"] },
      "memory": { "writeEnabled": true }
    },
    {
      "id": "data_explore", "name": "数据探查",
      "persona": "你负责探查数据源的结构与质量：表结构、字段分布、缺失率……只陈述事实，不做业务结论。",
      "description": "数据源探查（表结构/分布/质量）",
      "tools": ["data_mysql", "data_csv", "find", "grep", "file"],
      "skills": ["data-profiling"],
      "modelOverride": { "agent_main": "qwen3.8-flash" },
      "maxSteps": 30,
      "memory": { "writeEnabled": false, "readTypes": ["hard_constraint"] }
    }
  ]
}
```

---

## 11. 最终形态一览

```
启动：main.py → AgentAdapterService.startup() → init_app() → SystemApp.startup()
      （hook 冻结 → 基础设施 → 注册表 → 服务 → runtime → 后台任务）

运行期任意处：from codegenx.ai_service.system_app import get_app
      get_app().agents.get(...) / .tools.child_view(...) / .skills.for_agent(...)
              / .memory.load(...) / .tasks / .context / ...
      LLM 调用：resilient_invoke(SCENARIO_X, ...) / get_llm(model)（模块函数，不经过 app）

会话：RuntimeSessionState（纯状态 + 锁 + turn + context + agent_name）
      └─ SessionContext（ids + chat_messages + TurnPrompts + compact_breaker）

会话数增长时：每会话新增成本 ≔ chat_messages（固有）+ 若干锁与小对象；
连接池/注册表/服务实例数与 provider 数、组件数挂钩，与会话数无关
（LLM 连接池=provider 数，客户端=模型数，均已由 llm/ 调用层保证）。
智能体数增长时：组件零新增，加一个智能体 = agents 段加一条 AgentSpec（§10）。
```
