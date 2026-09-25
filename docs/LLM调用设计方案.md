# LLM 调用设计方案

> 范围：`backend/src/codegenx/ai_service/llm/` 及其四个调用场景（Agent 运行时、上下文压缩、会话摘要、记忆提炼）
> 目标：修掉现有 bug → 建立生产级 LLM 调用层（连接池复用、统一重试/熔断/降级、按场景分模型、可观测）

---

## 1. 现状盘点

全项目共 4 处直接调用大模型，全部经由 `llm/async_client.py` 的 `AsyncLLMClient`：

| # | 场景 | 调用点 | 当前模型 | 客户端生命周期 | 重试 | 并发控制 |
|---|------|--------|----------|----------------|------|----------|
| 1 | Agent 运行时 | `llm/llm_recovery.py:56` | **默认模型**（agent 配置的 model 被忽略） | **每轮 while 循环 new 一个，从不 close** | RecoveryMixin：continuation / compact / transport 三策略 | 无 |
| 2 | 上下文压缩 Path B | `context/session_context.py:50` → `compact/compact.py` | 默认模型 | **每个 SessionContext new 一个，从不 close** | PTL 截断重试（对**所有异常**生效，无退避） | 每会话熔断器（有缺陷） |
| 3 | 会话摘要/笔记 | `compact/session_summary.py:213` | 默认模型 | **每次调用 new 一个，从不 close** | 无（失败放弃，等下次触发） | 无 |
| 4 | 记忆提炼 | `schedule/memory.py:531-547` | `memory.store.model_name`（唯一有独立配置的场景） | 惰性单例（四者中唯一正确的） | 无（依赖任务队列 stuck 回收） | 信号量(2) + 在线让位 ✓ |

模型解析链：`AsyncLLMClient.__init__` → `config.get_provider_by_model_name(model)` → 未命中则落到 `providers.custom`；`get_default_model()` 取 `config.models` 列表**第一个**。

---

## 2. Bug 清单（按严重程度）

### P0-1 客户端即用即弃，连接池完全失效 + 连接/FD 泄漏

`AsyncLLMClient.__init__` 每次都 `AsyncOpenAI(...)` 新建一个 httpx 连接池，且从不调用 `await client.close()`：

- `llm_recovery.py:56`：**在重试 `while True` 循环体内**实例化。一次 turn 中 N 轮工具调用 + M 次重试 = N+M 个连接池。Agent 长会话下持续泄漏 socket/FD，直到 GC 兜底（asyncio 下 httpx 会打 `Unclosed client` 告警）。
- `session_summary.py:213`：每次摘要提取 new 一个 —— 最频繁的离线路径恰恰是浪费最严重的（每次都付出完整 TCP+TLS 握手）。
- `session_context.py:50`：每个会话 new 一个，SessionContext 销毁后客户端悬挂。

后果：连接复用率为 0（延迟 +1~2 RTT/次）、句柄泄漏、无全局连接数上限。

### P0-2 模型路由失效 —— "四个场景一个模型"

- Agent 运行时：`llm_recovery.py:56` 调 `AsyncLLMClient()` 不传 `model_name` → 永远用默认模型。`AgentConfig.model` / `resolved_model_name`（`utils/config.py:73`）**全项目无一处调用，是死配置**——agent 配置文件里写的模型实际不生效。
- 两处压缩（上下文压缩、会话摘要）也都落默认模型。
- 只有记忆提炼支持 `memory.store.model_name`。用户感知"他们应该用不一样的模型"，但当前配置体系只支持一处不一样。

### P0-3 错误分类靠字符串匹配，重试策略会误判

`llm_recovery.py:121` 用 `except (asyncio.TimeoutError, Exception)` 兜底 + `str(exc).lower()` 关键词匹配：

- `"timeout"` / `"connection"` 等关键词可能出现在 4xx 的报错消息里 → **不可重试的错误被当 transport 错误反复重试**。
- 代码 bug（KeyError/AttributeError 等）只要错误文本碰巧含关键词也会进入重试循环。
- OpenAI SDK 本身有类型化异常（`APITimeoutError`、`APIConnectionError`、`RateLimitError`、`InternalServerError`、`BadRequestError`），应按类型分类，字符串只做兼容兜底。

### P1-4 重试放大：SDK 内部重试 × 业务重试，无人协调

- `AsyncOpenAI` 默认 `max_retries=2`，SDK 内部已对连接错误/429/5xx 自动重试两次。
- 之上 `LLMRecoveryMixin` 又做 `max_transport_attempts` 次重试 → 实际最多 `(N+1)×3` 次请求。429 场景下重试放大反而加剧限流。
- `compact.py:_llm_compact` 对**所有异常**执行"截断最老 20% 消息再试"，没有 sleep 退避：遇到 429 时截断消息毫无帮助还丢失上下文；连续快速重试等于自我 DDoS。

### P1-5 流式中断重试导致用户看到重复内容

`invoke_stream` 超时/取消时已把部分内容作为 `LLM_RESPONSE_CHUNK` 事件推给前端；`llm_recovery` 随后按 transport 错误重试，新一轮从头生成 → **同一轮回答内容重复出现在前端**。且部分 tool_calls 的 yield（`async_client.py:191-200`）对 recovery 消费方是死代码——异常抛出时 `round_response` 被重建，partial 结果直接丢弃。

### P1-6 熔断器：无半开状态，一旦打开永不恢复

`compact.py:_CircuitBreaker` 只有 `consecutive_failures / disabled` 两个字段：

- 打开后 `compact_if_needed` 直接 return，`record_success` 永远不可达 → **会话存续期内压缩永久禁用**，无探测恢复。
- 熔断打开时连零成本零风险的 Path A（session-memory 快速路径）也被一起跳过，与模块头注释"Path A is not gated by the circuit breaker"矛盾。
- `_default_breaker`（`compact.py:93`）定义后从未使用，是死代码。
- 普通 dataclass 无锁，`record_failure` 在并发 compact 下有竞态（低概率但存在）。

### P2-7 ProviderConfig.extra_headers 配置了但从未生效

`utils/config.py:86` 定义了 `extra_headers`（注释明确写 AiHubMix 的 APP-Code），但 `async_client.py:89` 构造 `AsyncOpenAI` 时没有传 `default_headers` → **该配置静默失效**。

### P2-8 无任何用量观测

`completion.usage`（prompt/completion tokens）全链路丢弃；没有按场景/模型的调用次数、延迟、失败率、Token 消耗、成本指标。熔断触发也没有事件上报。

### P2-9 `invoke_stream` 的 timeout 语义与文档不符

docstring 写"整个流式调用的超时秒数"，实现是 `_stream_chunk_generator` 的**逐 chunk 间隔超时**（每个 chunk 重置计时）。行为本身合理（防止僵死流），但与 `httpx.Timeout` 的总 600s/read 120s 叠加后语义混乱，调用方无法据此推算最坏耗时。

### P2-10 配置健壮性

- `get_provider_by_model_name`：`getattr(self.providers, provider)` 在 provider 名不存在时直接 `AttributeError`（配置手误 → 运行时崩）。
- api_key 为空时无启动校验，直到第一次请求 401 才暴露。
- `invoke()` 遇 `choices` 为空返回 `""`，与"模型真的返回了空串"无法区分，掩盖内容过滤/上游异常。

---

## 3. 调用大模型失败怎么办：错误分类与分级策略

失败不是单一事件，必须先分类再决策。按"重试是否有意义"分三类：

| 类别 | 典型错误（SDK 类型） | 正确动作 | 反例（当前代码） |
|------|---------------------|----------|------------------|
| **瞬态错误**（重试有意义） | `APITimeoutError`、`APIConnectionError`、`RateLimitError(429)`、`InternalServerError(5xx)` | 指数退避 + 抖动重试；429 尊重 `Retry-After`；重试耗尽后走降级 | 字符串匹配；无退避的截断重试 |
| **确定性错误**（重试无意义） | `BadRequestError(400)`、`AuthenticationError(401)`、`PermissionDeniedError(403)`、`NotFoundError(404)`、上下文超长、内容过滤 | 不重试。上下文超长→压缩后重试一次；其余直接报错/降级 | 含 "long" 的 400 也可能被误判重试 |
| **逻辑错误**（代码 bug） | 本地 KeyError、序列化失败等 | 崩出来，绝不能进重试循环 | `except Exception` 全兜住 |

**分层韧性模型**（每一层只做自己该做的事，不越界）：

```
请求 → 超时(连接/读/总) → 重试(同模型,退避+抖动,≤2次)
     → 降级(fallback 链:同 provider 备模型 → 备 provider)
     → 熔断(连续失败打开,半开探测恢复)
     → 舱壁(场景级信号量,在线/离线隔离)
     → 兜底(业务降级:压缩退化截断/摘要放弃/记忆任务重投)
```

关键原则：
1. **重试与降级的分工**：重试解决"抖动"，降级解决"该模型/通道不可用"。重试 1-2 次仍失败就该换通道，而不是继续加倍等待。
2. **流式请求的重试窗口**：只在**收到第一个 chunk 之前**允许透明重试；已经开始吐字给用户后失败，只能终结本轮并向用户提示，不能静默重放（否则就是 P1-5 的重复内容 bug）。
3. **幂等性**：chat completion 本身无副作用，重试安全；但同一请求重试两次结果不同是正常的（温度>0 时），调用方逻辑不能依赖确定性。

---

## 4. 要不要连接池？——要，而且是"每 Provider 单例"形态

**结论：不需要自建连接池组件，需要的是修复客户端生命周期，让 httpx 的池真正被复用。**

设计要点：

1. **每 provider 一个进程级 `AsyncOpenAI` 单例**，由 `ClientRegistry` 持有。httpx `AsyncClient` 内建连接池，单例即池化；多实例即零复用。
2. **显式配置池参数**（当前全部默认值）：

```python
# 每个 provider 一个长生命周期客户端；池参数按"在线优先、离线受限"调优
http_client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_connections=100,            # 全局连接上限
        max_keepalive_connections=20,   # 空闲保活数，超过即关
        keepalive_expiry=55.0,          # 保活 55s：上游网关通常 60s 断空闲连接，主动先断避免撞上死连接
    ),
)
client = AsyncOpenAI(
    api_key=...,
    base_url=...,
    default_headers=provider_config.extra_headers or None,  # 修复 P2-7
    timeout=httpx.Timeout(600.0, read=300.0, write=30.0, connect=5.0, pool=10.0),
    max_retries=0,              # 关闭 SDK 内部重试（修复 P1-4），重试收敛到统一韧性层
    http_client=http_client,
)
```

   - `max_retries=0` 是关键：SDK 隐式重试与上层重试叠加是重试放大的根源；重试决策必须收口到一处。
   - `connect=5.0`（当前 30s 过长，连不上的服务快速失败才能触发降级）。
3. **生命周期绑定应用**：在 FastAPI lifespan 中创建 + 关闭，禁止业务代码 `AsyncLLMClient()` 即用即弃。
4. **流式注意**：老版本 openai SDK 有流式响应不归还连接的 bug，升级 SDK 至新版；确保流消费完或异常时关闭 stream 对象。
5. **离线任务不限连接**：离线并发用**信号量/队列**限（memory 现有 semaphore 模式是对的），而不是靠拆客户端池。

---

## 5. 熔断器设计

现有 `_CircuitBreaker` 是两态开关（正常/永久禁用）。生产熔断器应为**三态状态机**：

```
            失败率/连续失败超阈值
  CLOSED ────────────────────────► OPEN
   ▲  ◄────── 探测成功(达标) ─────── │
   │                                │ 冷却期(如 30s)到
   │         允许少量探测请求        ▼
  └─────────────────────────── HALF_OPEN（探测失败则回 OPEN）
```

实现骨架：

```python
class CircuitBreaker:
    """按 (provider, model) 粒度的三态熔断器（asyncio 安全）。"""
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,     # 连续失败阈值（CLOSED→OPEN）
        recovery_timeout: float = 30.0, # OPEN 冷却期（秒）
        half_open_max_calls: int = 2,   # 半开态允许的并发探测数
        half_open_success_rate: float = 0.5,  # 探测成功率达标则闭合
    ):
        self._state = "closed"
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_calls = 0
        self._half_open_success = 0
        self._half_open_total = 0
        self._lock = asyncio.Lock()      # 修复并发竞态

    async def acquire(self) -> bool:
        """请求进入前调用。返回 False = 快速失败（调用方走降级链）。"""
        async with self._lock:
            now = time.monotonic()
            if self._state == "open":
                if now - self._opened_at >= self._recovery_timeout:
                    self._state = "half_open"          # 冷却结束，放行探测
                    self._half_open_calls = self._half_open_success = self._half_open_total = 0
                else:
                    return False                        # OPEN 期：0ms 快速失败
            if self._state == "half_open":
                if self._half_open_calls >= self._half_open_max_calls:
                    return False                        # 探测名额已满，其余请求直接降级
                self._half_open_calls += 1
            return True

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                self._half_open_success += 1
                self._half_open_total += 1
                if self._half_open_total >= 2 and \
                   self._half_open_success / self._half_open_total >= self._half_open_success_rate:
                    self._state = "closed"              # 恢复
                    self._consecutive_failures = 0
            else:
                self._consecutive_failures = 0

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                self._half_open_total += 1
                self._state = "open"                    # 探测失败，重新打开
                self._opened_at = time.monotonic()
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    self._state = "open"
                    self._opened_at = time.monotonic()
                    # TODO: 上报 metrics + 告警事件
```

关键决策：

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 粒度 | `(provider, model)`，而不是每会话 | 模型故障是全局性的；per-session 熔断只能保护单会话且状态无法共享。若后续引入 Redis，可升级为进程间共享的分布式熔断 |
| 触发条件 | 连续失败 N 次（简化版）；有 metrics 后可升级滑动窗口失败率 | 先做简单版，数据驱动再进化 |
| OPEN 期行为 | `acquire()` 快速返回 False，调用方立即走 fallback 链 | 熔断的价值就是 0ms 快速失败 + 给上游喘息 |
| HALF_OPEN | 限流探测（并发 ≤2），成功过半才闭合 | 防止恢复瞬间流量打爆刚苏醒的上游，也防反复震荡 |
| 谁记录成败 | 韧性层统一记录，业务无感 | 现状是 compact 内部各记各的，agent 侧完全没有 |
| 与降级关系 | 熔断是降级链的"门卫"：主模型熔断打开 → 直接试备模型 | 修复"熔断打开连零成本 Path A 也被跳过"的问题——业务兜底路径永远可用 |

---

## 6. 生产级智能体 LLM 调用总体设计

### 6.1 目标架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        业务层（不感知模型/重试细节）                │
│  AgentRuntime     CompactionEngine    SessionSummary   Memory    │
│      │                 │                   │            │        │
│      │  scenario="agent_main"  "compact"      "summary"  "memory" │
└──────┼─────────────────┼───────────────────┼────────────┼────────┘
       ▼                 ▼                   ▼            ▼
┌─────────────────────────────────────────────────────────────────┐
│              LLMService（统一入口，按场景路由）                     │
│                                                                  │
│  ScenarioRouter: scenario → ModelSpec(主模型, fallback链, 参数)   │
│                                                                  │
│  ResilientExecutor:                                             │
│    1. 舱壁信号量（在线/离线两档）                                  │
│    2. 熔断 acquire（per provider+model）                         │
│    3. 超时控制（连接/首 chunk/整体）                                │
│    4. 重试（同模型，指数退避+抖动，≤2 次）                          │
│    5. 降级（fallback 链逐级切换，每级独立熔断）                      │
│    6. 观测埋点（次数/延迟/tokens/成本/熔断事件）                     │
│                                                                  │
│  ClientRegistry: provider → AsyncOpenAI 单例（连接池复用）         │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 按场景分模型（修复"四个场景一个模型"）

| 场景 | 模型档位 | 理由 | 关键参数 |
|------|----------|------|----------|
| agent 主对话 | **强模型**（代码生成质量关键） | 工具调用、长链路推理 | temperature 0~0.3、流式、max_tokens 8k |
| 上下文压缩 | **小/快模型** | 纯摘要任务，对推理要求低，但对**上下文长度**敏感（需 ≥ agent 上下文窗口） | temperature 0、非流式、max_tokens 2k |
| 会话摘要 | **小/快模型** | 增量笔记更新，输出短 | temperature 0、max_tokens 4k |
| 记忆提炼 | **小/便宜模型** | 结构化抽取，JSON 输出 | temperature 0、并发受限 |

配置落地（`config.json` 的 models 已是 name+provider 列表，增加场景映射即可）：

```json
{
  "models": [
    { "name": "qwen3-coder-plus", "provider": "dashscope", "role": "agent_main" },
    { "name": "qwen-flash",       "provider": "dashscope", "role": "compact" },
    { "name": "qwen-flash",       "provider": "dashscope", "role": "summary" },
    { "name": "qwen-flash",       "provider": "zhipu",     "role": "memory" }
  ],
  "model_roles": {
    "agent_main": { "primary": "qwen3-coder-plus", "fallbacks": ["deepseek-chat"] },
    "compact":    { "primary": "qwen-flash", "fallbacks": ["glm-4-flash"] },
    "summary":    { "primary": "qwen-flash" },
    "memory":     { "primary": "qwen-flash" }
  }
}
```

- `AgentConfig.model` 保留但语义改为**覆盖 agent_main 的 primary**（让 `resolved_model_name` 真正生效）。
- `memory.store.model_name` 向后兼容，优先级高于 role 默认值。
- 路由规则：scenario → role → primary；熔断打开或重试耗尽 → fallbacks 逐个尝试（每个 fallback 有独立熔断器；已熔断的直接跳过）。

### 6.3 核心接口设计

```python
class LLMService:
    """统一 LLM 入口。业务层只说"我是哪个场景"，不碰模型名/重试/熔断。"""

    async def invoke(self, scenario: str, messages, *,
                     tools=None, max_tokens=None, temperature=None,
                     priority: Literal["online", "offline"] = "online") -> LLMResult:
        """非流式。LLMResult 含 content / tool_calls / finish_reason / usage / model_used。"""

    async def invoke_stream(self, scenario: str, messages, *, ...) -> AsyncGenerator:
        """流式。首个 chunk 前失败可透明重试/降级；首 chunk 后失败直接抛给上层。"""


class Bulkhead:
    """舱壁：在线永远优先于离线。
    - online: 信号量高配或不设
    - offline: 信号量 = 2~4，且在线忙时让位（沿用 memory 现有模式）"""
```

职责归属（修复"到处各管一段"的现状）：

| 关注点 | 现状位置 | 目标位置 |
|--------|----------|----------|
| 重试/退避 | LLMRecoveryMixin（仅 agent）+ compact PTL 循环 + 无 | ResilientExecutor 统一 |
| 超时 | httpx 隐式 + per-chunk queue hack | executor 显式（连接/首 chunk/整体三层） |
| 熔断 | compact per-session 两态开关 | Registry per (provider, model) 三态 |
| 模型选择 | 默认第一个 + memory 特例 | ScenarioRouter |
| 并发隔离 | memory semaphore | Bulkhead（在线/离线两档） |
| 截断续写（length） | RecoveryMixin | **保留在 agent 侧**（这是业务语义，不是传输问题） |
| 上下文超长压缩 | RecoveryMixin | **保留在 agent 侧**（依赖 context_manager） |

### 6.4 可观测性（生产必备）

每次调用产出一条结构化记录（log + metrics）：

```
scenario, model, provider, attempt, fallback_used,
latency_ms(首token/整体), prompt_tokens, completion_tokens,
finish_reason, error_type, circuit_state, session_id, request_id
```

- **Token/成本**：从 `completion.usage` 采集，按 scenario×model 聚合（修 P2-8）。离线批量场景（memory/summary）单列成本预算告警。
- **熔断事件**：open/half-open/closed 转换打 warning 日志 + 计数器，接现有 `monitor` 模块。
- **错误分类计数**：`llm_errors_total{scenario, error_class}` —— 判断"该加重试还是该换通道"的数据依据。

---

## 7. 落地路线图

### P0（本迭代，修 bug）

1. **客户端单例化**：`llm/` 下新增 `client_registry.py`，按 provider 缓存 `AsyncOpenAI`；`AsyncLLMClient` 改为从 Registry 取客户端；lifespan 中统一 close。四个调用点全部改走 Registry。
2. **模型路由修复**：`llm_recovery.py:56` 传入 agent 配置的模型；compact/session_summary 接入场景配置（新增 `model_roles` 或临时加 `compact.model_name` / `summary.model_name`）。
3. **错误分类类型化**：新增 `llm/errors.py`，基于 SDK 异常类型分类（`retryable / fatal / context_overflow`），字符串匹配仅作非 OpenAI 兼容端点的兜底。
4. **重试收敛**：`AsyncOpenAI(max_retries=0)`，重试统一到 executor；`_llm_compact` 的重试区分 PTL（截断）与传输错误（退避），且加 sleep。
5. 顺手修：`extra_headers` 传入 `default_headers`；`get_provider_by_model_name` 容错回退。

### P1（韧性层）

6. 新增 `llm/resilience.py`：三态 `CircuitBreaker`（§5 骨架）+ 重试执行器（退避+抖动+Retry-After）。
7. fallback 链：`model_roles` 配置 + ScenarioRouter；每级独立熔断。
8. 流式重试窗口：首 chunk 前可重试/换模型，首 chunk 后只报错（修 P1-5 重复内容）。
9. 用量采集：usage → metrics/log 结构化字段。

### P2（生产增强）

10. 分布式熔断（Redis 共享状态，跨进程生效）——单进程部署可暂缓。
11. 离线任务优先级队列与配额（在线高峰自动暂停 memory/summary 提炼）。
12. 成本看板与预算告警。
13. 启动时配置探测（校验 api_key / models 可用性，坏配置快速失败）。

---

## 8. 参考资料

- [Portkey — Retries, Fallbacks, and Circuit Breakers in LLM Apps](https://portkey.ai)
- [Maxim AI — Retries, Fallbacks, and Circuit Breakers](https://www.getmaxim.ai)
- [TrueFoundry — LLM Gateway multi-provider fallback / circuit breakers](https://www.truefoundry.com)
- [LiteLLM — Routing & Load Balancing（cooldowns / weighted routing / fallbacks）](https://docs.litellm.ai)
- [OpenAI Python SDK — client 复用与连接池生命周期](https://developers.openai.com)
- [Microsoft Learn — openai _base_client 重试与共享 client](https://learn.microsoft.com)
- [httpx — Clients & connection pooling](https://www.python-httpx.org)
- [NVIDIA NeMo — 多轮 agent 小模型优先 + 失败升级大模型](https://developer.nvidia.com)
- [AWS — Multi-LLM routing strategies for generative AI](https://aws.amazon.com)
- [Azure Architecture Center — Circuit Breaker Pattern（三态参考实现）](https://learn.microsoft.com)
- [groundcover — Circuit Breaker half-open 机制](https://www.groundcover.com)
- [Splunk — Agent 成本优化 via Model Routing](https://www.splunk.com)
