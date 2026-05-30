# CodeGenX 设计文档

## 1. 项目架构

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    前端 (Vue 3)                       │
└─────────────────────────┬───────────────────────────┘
                          │ HTTP/SSE
                          ▼
┌─────────────────────────────────────────────────────┐
│                 API Gateway :8000                     │
│  JWT 鉴权 │ IP 黑名单 │ 限流 │ 请求路由 │ TraceId 注入   │
└──────┬──────────────────┬──────────────────┬────────┘
       │ gRPC             │ HTTP             │ HTTP/SSE
       ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ user-service │  │ app-service  │  │  ai-service  │
│    :8001     │  │    :8004     │  │    :8002     │
│              │  │              │  │              │
│ 用户注册/登录  │  │ 应用 CRUD    │  │ Agent 代码生成 │
│ 用户信息查询   │  │ 代码持久化    │  │ LLM 调用编排  │
│             │  │ 聊天历史     │  │ 上下文/记忆   │
└──────────────┘  └──────────────┘  │ 压缩/监控    │
                                    └──────────────┘
```

### 1.2 服务通信

- **user-service ↔ gateway**: gRPC + Nacos 服务发现
- **app-service ↔ gateway**: HTTP + Nacos 服务发现
- **ai-service ↔ gateway**: HTTP/SSE + Nacos 服务发现（chat streaming 走直连）


### 1.3 基础设施

| 组件 | 用途 |
|------|------|
| MySQL | session_metrics / turn_metrics / spans / monitor_alerts / 聊天历史 |
| Redis | session 状态缓存、限流计数器 |
| Nacos | 服务注册与发现 |
| Prometheus | 指标采集 + 告警规则 |
| COS | 代码文件/截图云存储 |   ？？？

---

## 2. 微服务功能

### 2.1 api-gateway

入口网关，所有外部请求经过此服务：

- **鉴权**: JWT Token 解析，按用户角色（普通用户 / 管理员）控制访问
- **限流**: Redis + Lua 滑动窗口，按 API 路径 + 用户粒度
- **IP 黑名单**: 可配置的黑名单拦截中间件
- **请求路由**: 按路径前缀将请求代理至下游服务（AppProxy / AiMonitorProxy）
- **TraceId**: 每个请求注入 X-Trace-Id，全链路追踪

### 2.2 user-service

gRPC 用户服务：

- 注册、登录（JWT 签发）
- 用户信息查询
- Proto 定义 → pb2 自动生成 stub/servicer

### 2.3 app-service

应用管理层：

- 应用 CRUD（创建/编辑/删除/列表）
- 生成的代码持久化（MySQL）
- 代码下载、部署触发
- 聊天历史（list/delete by appId）
- 静态资源服务 + 截图服务

### 2.4 ai-service（核心）

Agent 代码生成服务，详见第 3 节。

---

## 3. ai-service 核心 Agent 实现

### 3.1 整体架构

```
app.py                           ← FastAPI 入口 / 端点定义
  └─ services/agent_adapter_service.py  ← 服务编排层
       └─ bot/agent/runtime.py          ← Runtime 主循环
            ├─ bot/agent/session_pool.py     ← Session 池管理
            ├─ bot/agent/hook/               ← Hook 管线（生命周期回调）
            ├─ bot/context/                  ← 上下文组装
            ├─ bot/compact/                  ← 上下文压缩
            ├─ bot/memory/                   ← 长期/短期记忆
            ├─ bot/llm/                      ← LLM 客户端 + 重试
            ├─ bot/tools/                    ← 工具注册与执行
            ├─ bot/skill/                    ← 技能加载
            └── monitor/                     ← 监控管线
```

### 3.2 Runtime 架构

采用**双循环、双层 Queue**的消息驱动架构：

```
dispatch_loop (外层, message_bus 消费)
  │
  ├─ 接收用户请求 → 查找/创建 SessionState
  ├─ 将请求放入 session.queue
  └─ 若无活跃 worker → 启动 session_worker (内层)

session_worker (内层, session 独占)
  │
  ├─ 从 queue 取出 request
  ├─ Turn 循环:
  │   ├─ on_turn_start
  │   ├─ { pre_llm_call → LLM 调用 → post_llm_call }
  │   ├─ { pre_tool_use → Tool 执行 → post_tool_use } (多个)
  │   ├─ on_turn_end
  │   └─ 若 needs_followup → 继续下一轮
  └─ on_session_end
```

**消息总线** (message_bus): 采用请求-订阅模式，响应通过 session 专属 queue 返回。

**Session 生命周期**:
- **创建**: 请求到达时无活跃 session → 新建 SessionState → 启动 worker
- **空闲超时**: 30 分钟无新消息 → 自动关闭
- **主动停止**: 用户发送 stop 请求 → grace_seconds 内关闭
- **异常终止**: turn 执行异常 → 状态设为 FAILED

### 3.3 上下文管理 (Context)

```
bot/context/
├── session_context.py    ← SessionContext (会话级上下文容器)
└── assembler.py          ← ContextAssembler (组装 system prompt + 消息)
```

- **SessionContext**: 存储 app_id、user_id、code_gen_type、workspace 元数据
- **ContextAssembler**: 按优先级组装：
  1. System Prompt（从 prompt/ 模板加载 + runtime 动态参数）
  2. 热记忆（近期对话 + 工具结果）
  3. 冷记忆（Qdrant 检索的历史知识）
  4. 用户消息

### 3.4 记忆系统 (Memory)

```
bot/memory/
├── memory_manager.py     ← MemoryManager (统一入口)
├── hot.py               ← 热记忆 (近期上下文, 内存 LRU)
├── warm.py              ← 温记忆 (会话摘要, Redis)
├── session.py           ← 会话持久化
├── compact.py           ← 记忆压缩
└── paths.py             ← 记忆路径配置
```

三层记忆架构:
- **Hot**: 当前 Turn 范围内的工具调用结果、中间消息，存内存
- **Warm**: 跨 Turn 的会话摘要，存 Redis，用于 token 预算评估
- **Cold**: 跨 Session 的知识，存 Qdrant 向量库，语义检索

### 3.5 上下文压缩 (Compact)

```
bot/compact/
├── compact.py           ← CompactManager (压缩编排)
├── micro.py             ← 微压缩 (单个超长工具结果截断)
├── large_output.py      ← 大输出处理
├── prompt.py            ← 压缩提示词
└── thresholds.py        ← 阈值配置
```

**压缩流程**:
1. **pre_llm_call**: 检查 assembled context 的预估 token 数
2. 若超过 token_budget × 阈值 → 触发压缩
3. 压缩策略:
   - **MicroCompact**: 超长工具输出替换为占位标记 (`[已被压缩...]`)
   - **SummaryCompact**: 旧消息通过 LLM 摘要替换原文
4. 压缩后重新组装 → 再次检查 → 递归直到满足预算

**消息格式**: 统一使用 OpenAI-compatible 格式 (`tool_call_id` / `function.name`)

### 3.6 Hook 管线

```
bot/agent/hook/
├── registry.py          ← Hook 注册表
├── runner.py            ← HookRunner (按优先级执行)
└── handlers.py          ← 默认处理器
```

**生命周期 Hook**:

| Hook | 触发时机 | 职责 |
|------|---------|------|
| on_session_start | Session 创建 | 初始化 telemetry、span record |
| on_turn_start | Turn 开始 | 创建 turn_telemetry、turn span |
| pre_llm_call | LLM 调用前 | 更新 context token 指标 |
| post_llm_call | LLM 调用后 | 更新 prompt/completion 指标、告警检查 |
| pre_tool_use | Tool 执行前 | 创建 tool span |
| post_tool_use | Tool 执行后 | 记录 tool 结果、memory hits |
| on_turn_end | Turn 结束 | 落库 turn_metrics、更新 session 累加 |
| on_session_end | Session 结束 | 落库 spans + session_metrics |
| on_error | 异常 | 标记 error 状态 |

Hook 按优先级排序执行，支持注册自定义处理器。

### 3.7 监控管线 (Monitor)

```
monitor/
├── monitor_pipeline.py       ← MonitorPipeline (门面)
├── metric_collector.py       ← MetricCollector (turn 级别指标缓存)
├── span_collector.py         ← SpanCollector (span 缓存, OrderedDict)
├── prometheus_metrics.py     ← Prometheus 指标定义 & 记录函数
├── alert_evaluator.py        ← AlertStreakTracker (连续告警状态)
├── monitor_store.py          ← MonitorStore (MySQL 持久化)
├── monitor_query_service.py  ← 查询服务 (overview/sessions/turns)
├── maintenance_service.py    ← 定时维护 (DB 清理 + streak 清理)
├── orm_models.py             ← SQLAlchemy ORM 模型
├── telemetry_schema.py       ← Pydantic 模型
└── monitor.sql               ← 建表 DDL
```

**数据流**:

```
Hook 触发
  → MonitorPipeline 方法
    → MetricCollector (内存缓存 turn_telemetry)
    → SpanCollector (内存缓存 spans, OrderedDict)
    → Prometheus helpers (实时推送 Counter/Histogram/Gauge)
    → AlertStreakTracker (连续状态 → Prometheus Gauge)
    
Session 结束时:
  → SpanCollector → MonitorStore.insert_spans() → MySQL spans 表
  → MetricCollector.session_telemetry → MonitorStore.upsert_session_metrics()
  → SpanCollector.derive_turn_metrics() → MonitorStore.replace_turn_metrics()
```

**Prometheus 指标清单**:

| 指标 | 类型 | 标签 |
|------|------|------|
| codegenx_sessions_total | Counter | app_id, status |
| codegenx_active_sessions | Gauge | app_id |
| codegenx_session_duration_seconds | Histogram | app_id |
| codegenx_turns_total | Counter | app_id, status |
| codegenx_turn_duration_seconds | Histogram | app_id |
| codegenx_llm_calls_total | Counter | app_id, model, status |
| codegenx_llm_latency_seconds | Histogram | model |
| codegenx_llm_first_token_seconds | Histogram | model |
| codegenx_llm_prompt_tokens_total | Counter | app_id, model |
| codegenx_llm_completion_tokens_total | Counter | app_id, model |
| codegenx_llm_recoveries_total | Counter | app_id, model, recovery_kind |
| codegenx_tool_calls_total | Counter | app_id, tool_name, status |
| codegenx_tool_latency_seconds | Histogram | app_id, tool_name |
| codegenx_memory_hits_total | Counter | app_id |
| codegenx_errors_total | Counter | app_id, scope, error_type |
| codegenx_llm_recovery_streak | Gauge | model |
| codegenx_tool_failure_streak | Gauge | tool_name |
| codegenx_context_breach_streak | Gauge | session_id |
| codegenx_llm_last_call_latency_seconds | Gauge | model |

**告警**: 由 Prometheus 规则 (`infra/monitoring/prometheus_alerts.yml`) 基于以上指标触发，连续状态（recovery streak、tool failure streak、context breach streak）由 `AlertStreakTracker` 维护。

### 3.8 定时任务

`MonitorMaintenanceService` 在 `AgentAdapterService.startup()` 时启动后台 asyncio task：

1. **DB 历史清理** (`cleanup_history`): 每 5 分钟清理超过 7 天的 spans / turn_metrics / session_metrics / monitor_alerts
2. **Alert Streak 清理** (`cleanup_stale_sessions`): 每 5 分钟移除已结束 session 的 streak 状态，防止内存泄漏

正常路径下 `on_session_end` 会清理对应 session 的 streak 状态；定时清理作为兜底。
