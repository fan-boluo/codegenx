# LLM 调用设计方案

> 范围：`backend/src/codegenx/ai_service/llm/` 及四个使用大模型的组件（Agent 运行时、上下文压缩、会话摘要、记忆提炼）
> 定位：描述当前 LLM 调用层的架构与运行时行为，作为开发与排障的基准参考

---

## 1. 总体架构

LLM 调用按职责分三层，上层只声明自己的模型链，传输细节全部下沉：

```
┌───────────────────────────────────────────────────────────────────┐
│ 组件层：每个组件在初始化时从自身配置解析模型链                         │
│                                                                   │
│  AgentRuntime     CompactionService   SessionSummaryService       │
│  MemoryScheduler                                                  │
│      │                 │                   │            │          │
│      └─── chain=[...]  └── chain=[...]     └── chain   └── chain   │
└──────────┬────────────────────────────────────────────────────────┘
           ▼
┌───────────────────────────────────────────────────────────────────┐
│ 韧性层（llm/resilience.py）                                        │
│                                                                   │
│  resilient_invoke / resilient_invoke_stream(messages, chain=...)  │
│    1. 熔断 acquire（per provider:model，已打开的模型快速跳过）        │
│    2. 同模型重试（指数退避 + 抖动，尊重 Retry-After）                 │
│    3. 逐级 fallback（链上每个模型独立熔断）                          │
│    4. 观测埋点（调用/重试/降级/熔断指标 + 结构化日志）                 │
│                                                                   │
│  错误分类（llm/errors.py）：四类错误决定重试/降级/抛出               │
└──────────┬────────────────────────────────────────────────────────┘
           ▼
┌───────────────────────────────────────────────────────────────────┐
│ 客户端层（llm/）                                                    │
│                                                                   │
│  get_llm(model_name)  → AsyncLLMClient 包装器（按 model 缓存）      │
│  ClientRegistry      → 每 provider 一个 AsyncOpenAI 单例           │
│                        （httpx 连接池复用，应用停止时统一关闭）        │
└───────────────────────────────────────────────────────────────────┘
```

职责边界：

| 关注点 | 归属 | 说明 |
|--------|------|------|
| 模型选择 | 各组件 | 从自身配置字段解析，不经全局路由 |
| 重试 / 退避 / 降级 | 韧性层 | 业务层不感知重试细节 |
| 业务结果校验（输出是否可用） | 各业务组件 | 通用层只管调用成败；校验分层与兜底出口见 §5 |
| 熔断（模型级） | 韧性层 | per `provider:model`，全局注册表 |
| 压缩失败兜底 | CompactionService | 压缩失败/无效统一保守截断（保底可用），不属模型故障、不进熔断 |
| 连接池 / 客户端生命周期 | 客户端层 | per-provider 单例，应用级启停 |
| 截断续写（finish_reason=length） | Agent 侧业务恢复 | 业务语义，不是传输问题 |
| 上下文超长恢复 | Agent 侧业务恢复 | 依赖会话上下文管理器 |
| 摘要提取触发 / 压缩调度 | 各组件自身 | 阈值与时机判断留在业务内 |

---

## 2. 组件与模型配置

### 2.1 模型来源

不再有全局场景→模型的路由配置。每个使用模型的组件从**自己的配置字段**解析模型：

| 组件 | 模型配置字段 | 解析规则 | 观测 label |
|------|-------------|----------|-----------|
| AgentRuntime | `agents[].model`（默认智能体）与各智能体覆盖 | 默认链 = 默认智能体的 model；会话智能体有配置则整链替换 | `agent` |
| CompactionService | `compact.model_name` | 空 / 未配置回落默认模型（`models` 列表第一个） | `compact` |
| SessionSummaryService | `compact.model_name` | 摘要与压缩同族，共用压缩模型配置 | `summary` |
| MemoryScheduler | `memory.store.model_name` | 空 / 未配置回落默认模型 | `memory` |

模型名可带 `provider/` 前缀（如 `dashscope/qwen3-coder-plus`），解析时剥离前缀得到纯模型名。

### 2.2 组件生命周期

组件经 `SystemApp` 组合根注册（`initialize_components` 顺序即依赖顺序：llm → tools → skills → agents → context → session_io → tasks → memory → summary → compaction → chat_messages → monitor → monitor_maintenance → memory_scheduler → runtime）。

每个使用 LLM 的组件在 **`__init__`** 时：

1. 从自身配置解析出模型链 `self._model_chain`；
2. 连接池经客户端层惰性单例按需建立（模型级熔断器统一由韧性层注册表管理，无自建业务熔断器）。

在 **`async_before_start`**（引擎启动阶段）时：

- **预热**：对链上每个模型执行 `get_llm(model)` + `get_breaker(model)`，让 provider 客户端与熔断器在首个请求前就绪（构造即注册，不发起网络请求）；
- 预热包裹在 `suppress(Exception)` 内：预热失败不阻断应用启动（离线环境 / key 未配置时允许带病启动，首个真实调用再暴露）；
- AgentRuntime 例外：启动前做最小配置校验（默认智能体必须配置 model），坏配置**快速失败**；
- MemoryScheduler：预热后照常执行自身 `startup()`（启动离线任务消费循环）。

组件随系统服务停止而销毁；LLM 客户端由 `llm` 门面在 `async_before_stop` 统一关闭（见 §3.3）。

### 2.3 会话级智能体覆盖

`AgentRuntime.resolve_agent_chain(agent_name)` 决定一次调用的模型链：

1. 会话绑定了智能体名，且注册表中该智能体的 `AgentConfig.model` 非空 → 用它（整链替换，单模型链）；
2. 否则 → 默认智能体链。

智能体注册表（`agent/agent_registry.py`）在装配时校验：非默认智能体必须配置 persona；`agents[].model` 引用的模型无需全局预注册（用到时按名查 provider）。

---

## 3. LLM 客户端层

### 3.1 ClientRegistry：per-provider 单例

`llm/client_registry.py` 按 provider 缓存 `AsyncOpenAI` 实例。每个实例持有独立的 httpx 异步连接池：

| 参数 | 值 | 说明 |
|------|-----|------|
| `max_connections` | 100 | 全局连接上限 |
| `max_keepalive_connections` | 20 | 空闲保活数 |
| `keepalive_expiry` | 55s | 上游网关通常 60s 断空闲连接，主动先断避免撞死连接 |
| 超时 | total 600s / read 300s / write 30s / connect 5s | connect 短超时让不可达 provider 快速失败进入降级 |
| `max_retries` | 0 | SDK 内部重试关闭，重试决策收敛到韧性层 |
| `default_headers` | `ProviderConfig.extra_headers` | 透传 provider 专属头 |

同 provider 的所有模型共享同一个客户端实例——连接池复用的单位是 provider，不是 model。

### 3.2 get_llm：模型级包装器

`llm/async_client.py` 提供 `get_llm(model_name)`，带 LRU 缓存（maxsize=8）：同模型名返回同一个 `AsyncLLMClient` 包装器，内部从 ClientRegistry 取该模型的 provider 客户端。单例效果由此成立。

包装器暴露两个入口：

- `invoke(messages, **kwargs)`：非流式，返回文本内容；
- `invoke_stream(messages, **kwargs)`：流式，逐 chunk 产出（含 tool_calls 增量）。

### 3.3 门面（llm/facade.py）

`llm` 门面是组件容器中的 LLM 组件，**启动阶段不做任何动作**（各组件自行预热），职责只有：

- `async_before_stop`：调用 `close_llm_clients()` 关闭全部 provider 客户端与连接池；
- `circuit_snapshot()`：暴露全部熔断器状态快照（管理 / 排障用）。

---

## 4. 韧性层

`llm/resilience.py` 是所有 LLM 调用的必经执行器。业务层调用签名：

```
resilient_invoke(messages, *, chain, label, tools?, max_tokens?, temperature?)
resilient_invoke_stream(messages, *, chain, label, tools?, max_tokens?, temperature?, timeout?)
```

- `chain`：调用方（组件）已解析好的模型序列，第一个为主模型，其余为降级顺序；空串项被过滤；
- `label`：仅作观测标签（指标键沿用 `scenario` 字段），不参与任何路由决策；
- 韧性层不查询配置中的模型归属——模型选择完全发生在组件层。

### 4.1 错误分类（llm/errors.py）

所有异常先分类再决策。分类基于 OpenAI SDK 类型化异常，非 SDK 异常按消息特征兜底：

| 类别 | 典型来源 | 动作 | 是否计熔断失败 |
|------|---------|------|---------------|
| `RETRYABLE` | 超时、连接失败、429、5xx | 同模型退避重试；429 尊重 `Retry-After`；耗尽后切下一级模型 | 是 |
| `CONTEXT_OVERFLOW` | 上下文超长（400 带 length 类特征） | 不重试，直接切下一级模型（更长窗口的模型可能救回）；无下一级则抛给调用方 | 否 |
| `FATAL` | 401 / 403 / 404、内容过滤等确定性拒绝 | 不重试，切下一级模型（换 provider 可能解决）；不计熔断失败 | 否 |
| `LOGIC` | 本地代码 bug（KeyError、序列化失败等） | 立即抛出，绝不重试或降级掩盖 | 否 |

`asyncio.CancelledError`（用户停止 / 任务取消）永不被重试或吞掉，直接向上传播。

### 4.2 熔断器

模型级熔断器为三态状态机（`CircuitBreaker`）：

```
            连续失败 ≥ 阈值
  CLOSED ─────────────────────► OPEN
    ▲  ◄── 探测达标(成功率) ──   │
    │                           │ 冷却期(30s)到
    │      允许少量探测请求       ▼
    └───────────────────── HALF_OPEN（探测失败回 OPEN）
```

| 参数（`config.llm`） | 默认 | 说明 |
|---------------------|------|------|
| `breaker_failure_threshold` | 5 | CLOSED 下连续失败次数阈值 |
| `breaker_recovery_timeout_seconds` | 30 | OPEN 冷却期 |
| `breaker_half_open_max_calls` | 2 | 半开态并发探测名额 |
| `breaker_half_open_success_rate` | 0.5 | 探测成功率达标即闭合 |
| `max_attempts` | 2 | 同模型重试次数上限 |
| `backoff_base_seconds` / `backoff_max_seconds` | 1 / 30 | 指数退避基值 / 封顶 |

行为要点：

- 注册表按 `provider:model` 键全局唯一（`get_breaker(model)`），模型故障是全局性的；
- OPEN 期 `acquire()` 返回 False，韧性层立即跳过该模型走链上下一级（0ms 快速失败）；
- HALF_OPEN 放行限量探测，成功过半闭合、任一失败重开；
- 状态迁移经 `asyncio.Lock` 串行化；状态变化打日志并更新指标（`llm_breaker_opens_total`、`llm_circuit_state` gauge）。

**熔断粒度**：只有模型级一层。

| 层 | 键 | 持有者 | 保护对象 |
|----|-----|--------|---------|
| 模型级 | `provider:model` | 韧性层注册表 | 传输层故障（超时/限流/不可达），全局生效 |

压缩失败与压缩无效（压缩后 token 未下降）是业务结果而非模型故障，不进熔断；由 CompactionService 统一保守截断兜底（session-memory 快速路径不经 LLM，永远可用）。

### 4.3 非流式执行（resilient_invoke）

```
for model in chain:
    breaker.acquire() 失败 → 跳过该模型
    for attempt in 0..max_attempts:
        调用成功 → breaker.record_success()，记录指标，返回
        分类错误：
            LOGIC           → 立即抛出
            RETRYABLE       → 记熔断失败；未耗尽则按退避延迟重试
            OVERFLOW/FATAL  → 不重试，跳出内层
            耗尽            → 跳出内层
    → 链上下一级模型
全部失败 → 抛最后一个异常（链上无可用模型时抛 LLMNoModelAvailableError）
```

退避延迟 = min(base × 2^attempt, cap) 与 429 `Retry-After` 取较大值，再加 25% 以内抖动。

### 4.4 流式执行（resilient_invoke_stream）

与非流式相同的「重试 → 降级」结构，外加一个关键约束——**重试窗口只到首个 chunk 之前**：

- 首 chunk 尚未产出：任何失败按错误分类正常重试 / 换模型（对上层透明）；
- 首 chunk 已透传给消费者：任何失败**立即显式抛出**，不做任何透明重放——否则用户会看到重复内容。

`timeout` 参数透传给底层流式读取（逐 chunk 间隔超时，防僵死流）。

### 4.5 业务恢复（Agent 侧）

`agent/llm_recovery.py` 中的 `LLMRecovery` 是 AgentRuntime 的协作器对象（组合，非继承），负责韧性层不处理的**业务语义**恢复：

| 情形 | 恢复动作 |
|------|---------|
| finish_reason=length（截断） | 追加 continuation 消息让模型续写，循环拼接至完成或步数上限 |
| CONTEXT_OVERFLOW 且链上无更长模型 | 触发会话上下文强制压缩后重试一次 |
| 其他传输类错误 | 已由韧性层处理，不重复介入 |

LLMRecovery 通过 `resilient_invoke_stream(chain=runtime.resolve_agent_chain(...), label="agent")` 调用，temperature 取自智能体规格（`spec.limits.temperature`）。

---

## 5. 错误处理方案（两层：通用 + 业务）

异常处理分两层，各自回答一个问题：

- **第一层·通用层**（`llm/errors.py` + `llm/resilience.py`）：**这次模型 API 调用本身成不成立**——连接失败、超时、限流、鉴权、参数错误、上下文超长。输入是消息列表，输出是「成功的内容」或「已分类异常」；对返回内容的业务含义不做任何判断。
- **第二层·业务层**（四个业务组件各自）：**返回的内容能不能用于本业务**——空响应、格式坏、效果不达标。校验逻辑与兜底动作全部写在各组件内，通用层不感知。

四个业务：记忆提炼、会话摘要、上下文压缩（均非流式）、Agent 运行时（流式）。

**衔接原则**：通用异常与业务校验失败**同路归一**——业务侧先把通用异常 catch 成「无效结果」，再与「内容校验不通过」汇入同一个兜底出口。每个业务只有一个失败终点，排障按出口归因。

### 5.1 通用层的流式 / 非流式差异

通用层异常处理按流式/非流式拆成两个入口（`resilient_invoke` / `resilient_invoke_stream`），错误分类共用 `errors.py` 四分类，不重复实现：

| 阶段 | 非流式 | 流式 |
|------|--------|------|
| 调用前 | 熔断 acquire，打开即跳过该模型 | 相同 |
| 有产出之前（返回前 / 首 chunk 前） | 同模型退避重试 → 链上降级，对上层透明 | 相同（重试窗口只到此为止） |
| 已有产出之后 | ——（无此阶段） | 任何失败**立即显式抛出**，不透明重放（防用户看到重复内容） |
| 用户停止 / 取消 | 直接上抛 | 直接上抛 |

### 5.2 业务校验的分层标准

业务侧对「成功返回」的校验按由廉到贵四层组织，逐层只在前一层通过后进行：

| 层 | 判定内容 | 成本 |
|----|---------|------|
| 句法 | 能否解析：非空文本、JSON 可解析 | 确定性，零成本 |
| 结构 | 形状正确：字段/类型/枚举白名单 | 确定性，零成本 |
| 语义 | 效果达标：如压缩后 token 下降 | 按业务规则计算 |
| 策略 | 安全/合规 | hook 层（`output_safety_check`），不在 LLM 调用层内 |

### 5.3 四个业务的校验点与兜底（安插位置）

| 业务 | 调用位置 | 通用异常到达点 | 业务校验点 | 兜底出口 |
|------|---------|---------------|-----------|---------|
| memory | `MemoryScheduler._invoke_llm` → `resilient_invoke(label="memory")` | 上抛至 `_process` → `mark_failed` 退避重试（1m/5m/30m），超限置 `dead`（死信）；水位不推进（at-least-once，重跑幂等） | `parse_extracted_memories` / `parse_conflict_pairs`（句法：剥包裹+JSON 容错截取；结构：类型白名单、推断置信度封顶）；坏条目跳过 | 空候选 = 合法 no-op：清账推进水位，不重试（「没提炼出记忆」是正常业务结果） |
| summary | `SessionSummaryService._summarize` → `resilient_invoke(label="summary")` | 上抛至 `_extract` 的 catch-all → 记日志、`extracting` 复位，不重试 | 句法层：空输出即无效；摘要结构由提示词模板保证，不做结构强校验 | 回退保留旧摘要 `current_notes`，等下次阈值再触发（异步可重算） |
| compact | `_call_llm_for_summary` → `resilient_invoke(label="compact")` | `_llm_compact` catch → 无效结果 | 句法：空摘要；语义：压缩无效（`tokens_after ≥ tokens_before`） | `compact_if_needed` 统一出口 = 保守截断（确定性本地操作，保底可用，绝不向上抛错） |
| agent | `LLMRecovery.invoke` → `resilient_invoke_stream(label="agent")` | 首 chunk 前已由通用层透明处理；首 chunk 后显式抛出 → LLMRecovery **不做透明重放**，直接上抛至 runtime turn 级收尾（失败状态 + 事件上报） | 业务语义恢复：`finish_reason=length` → continuation 续写；`CONTEXT_OVERFLOW` → 压缩后重试（`force_compact`） | 恢复次数耗尽即失败返回（部分内容），不再重试 |

选型依据按业务性质区分：

| 业务性质 | 兜底选型 | 实例 |
|---------|---------|------|
| 同步必答（在线对话 / 压缩） | 确定性本地兜底，永不向上抛 | 保守截断 / 拦截消息回复 |
| 异步可重算（摘要） | 保留旧值，等下轮 | `current_notes` 回退 |
| 离线有账本（记忆） | 任务状态机重试 / 死信 + 水位不推进 | `memory_task` 退避 + `dead` + 卡死回收巡检 |

### 5.4 通用异常 → 业务兜底的衔接模式

固定四步（各业务一致，安插在「调用点之后的第一个 await 边界」）：

1. 通用层职责终点：抛已分类异常或返回原始输出，不做业务判断；
2. 业务组件设两道闸：
   - **异常闸**：catch 通用层异常，按 `LLMErrorClass` 记因，转「无效结果」；
   - **校验闸**：对成功返回做 §5.2 分层校验，不通过同样转「无效结果」；
3. 「无效结果」汇入该业务**唯一兜底出口**（§5.3 选型表）；
4. 兜底动作必须可观测：warn/error 日志 + 指标（通用层 outcome 分类、`memory_extract_total`、`memory_write_rejected_total`、`llm_recoveries_total`）。

告警按错误类别区分：通用层 429/5xx 激增 → 调退避/熔断参数；业务校验失败激增 → 提示词或模型回归。两类指标分开看。

### 5.5 Agent 业务侧恢复的通用范式

`llm_recovery.py` 的业务恢复机制可抽象为三条范式；未来其他业务需要同类恢复时，照此安插在各自调用点之后：

1. **有界恢复**：每类恢复独立计数上限（`max_continuation_attempts` / `max_compact_attempts`），防恢复风暴；
2. **无副作用恢复**：恢复动作不修改输入源（continuation 消息在本地重建，不写入 `context.chat_messages`），失败可干净退出；
3. **恢复可观测**：每次恢复计数上报（`turn_state.llm_recovery_count` + `llm_recoveries_total` 指标）。

### 5.6 与业界机制的对应

| 业界机制 | 本项目落点 |
|---------|-----------|
| 传输类 / 语义类错误双清单（Portkey、LiteLLM 等） | 通用层四分类（`errors.py`）+ 业务校验闸（§5.4） |
| 校验四分层（句法→结构→语义→策略） | §5.2 标准；memory 落实句法+结构，compact 落实句法+语义，策略层在 hook |
| Instructor 式「带校验反馈重问」 | 暂不采用：离线任务坏输出按 no-op 处理（重问成本不划算）；若未来 JSON 产出率低，可对 memory 解析失败加一次带反馈重问 |
| 结构化输出约束（JSON mode / 约束解码） | 提示词约定 + 容错解析（剥包裹/截取括号）；provider 支持 JSON mode 时可在 memory 提炼启用 |
| Guardrails AI / NeMo Guardrails 输出护栏 | 轻量对应：memory 类型白名单校验、agent 侧 `output_safety_check`（hook 层，策略级） |
| 死信队列 / 重试队列 | `memory_task` 退避重试 + `dead` 状态 + 卡死回收巡检 |
| 舱壁隔离（Bulkhead） | `MemoryScheduler` 信号量限并发 + 在线让位 |
| 有界自愈（Bounded self-healing） | LLMRecovery 每类恢复独立次数上限（§5.5） |

---

## 6. 可观测性

每次调用产出结构化记录（不打消息内容，只记元数据）：

指标（Prometheus，经 `monitor/prometheus_metrics`）：

| 指标 | 标签 | 含义 |
|------|------|------|
| `llm_scenario_calls_total` | scenario(=label), model, outcome | 调用计数；outcome 为 `ok` 或 `error_{类别}` |
| `llm_retry_total` | scenario, model | 同模型重试计数 |
| `llm_fallback_total` | scenario, model | 降级到该模型的计数 |
| `llm_breaker_opens_total` | circuit | 熔断打开计数 |
| `llm_circuit_state` | circuit | 熔断状态 gauge（0=closed, 1=half_open, 2=open） |

日志：`[llm_call]`（debug 级，每次调用一条：label/model/attempt/outcome/fallback/latency）、`[llm_breaker]`（状态迁移，warning/info 级）、`[llm]`（重试与熔断跳过，warning 级）。

埋点全部守卫式（内部 try/except 吞错），指标故障绝不阻断业务调用。

usage（prompt/completion tokens）由 Agent 侧 hook（`report_prompt_tokens` / `report_llm_usage`）经事件总线采集上报。

---

## 7. 配置参考

```
models:                          # 模型列表；第一个为默认模型
  - name / provider / api_key? / extra_headers?

agents[]:
  - id / defaults? / persona / tools? / skills?
    model                        # 本智能体的模型（可带 provider/ 前缀）
    max_tool_iterations / max_steps / ...

compact:
  model_name                     # 上下文压缩模型（摘要同族共用）

memory:
  store:
    model_name                   # 记忆提炼模型

llm:
  max_attempts                   # 同模型重试次数
  backoff_base_seconds / backoff_max_seconds
  breaker_failure_threshold
  breaker_recovery_timeout_seconds
  breaker_half_open_max_calls
  breaker_half_open_success_rate
  llm_stream_timeout_seconds     # 流式逐 chunk 间隔超时
```

模型解析优先级：组件自身配置字段 > 默认模型（`models[0]`）。无任何「场景→模型」全局映射。

---

## 8. 参考资料

- [Portkey — Retries, Fallbacks, and Circuit Breakers in LLM Apps](https://portkey.ai)
- [Maxim AI — Retries, Fallbacks, and Circuit Breakers](https://www.getmaxim.ai)
- [TrueFoundry — LLM Gateway multi-provider fallback / circuit breakers](https://www.truefoundry.com)
- [LiteLLM — Routing & Load Balancing（cooldowns / weighted routing / fallbacks）](https://docs.litellm.ai)
- [OpenAI Python SDK — client 复用与连接池生命周期](https://developers.openai.com)
- [httpx — Clients & connection pooling](https://www.python-httpx.org)
- [Azure Architecture Center — Circuit Breaker Pattern（三态参考实现）](https://learn.microsoft.com)
- [groundcover — Circuit Breaker half-open 机制](https://www.groundcover.com)
- [Splunk — Agent 成本优化 via Model Routing](https://www.splunk.com)
- [Instructor — 结构化输出校验与带反馈重试](https://python.useinstructor.com)
- [Guardrails AI — 输出护栏与 re-ask 修复循环](https://github.com/guardrails-ai/guardrails)
- [NVIDIA NeMo Guardrails — 输入/输出/对话三段 rails](https://github.com/NVIDIA/NeMo-Guardrails)
