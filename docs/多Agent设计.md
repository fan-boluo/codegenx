# 多智能体数据分析系统 · 架构设计方案

> **方案模式**：A — Supervisor + Planner-Executor（主管调度式）
> **日期**：2026-10-09（v3，合入 LangGraph 实现机制映射、三项决策落定，移除备选方案章节）
> **状态**：已经用户确认通过
> **系统目标**：辅助用户进行数据探查、代码生成、图表可视化及分析报告撰写

## 0. 需求约束

| 维度  | 结论  |
| --- | --- |
| 数据来源 | 用户上传文件（Excel/CSV/JSON）+ 数据库/数仓（SQL 检索） |
| 编排技术 | LangGraph（StateGraph + conditional edges + interrupt + checkpointer） |
| 交互形态 | 两者都要：对话式多轮追问 + 一次性端到端自动出报告 |
| 既有基础设施 | MySQL、Redis、Qdrant（存储方案基于此选型，不引入 SQLite） |
| 产出物 | 架构设计方案（本文档） |

## 1. 架构总览

```
                         ┌─────────────┐
        用户消息 ───────▶│  Supervisor │◀─────── 澄清/确认（human-in-the-loop）
                         └──────┬──────┘
                     简单请求 │  │ 复杂任务
                 ┌────────────┘  └────────────┐
                 ▼                            ▼
          直达单个 Agent               ┌────────────┐
        （轻量对话模式）               │   Planner  │──▶ 生成执行计划(DAG，动态)
                                      └─────┬──────┘
                                            ▼
        ┌───────────────── 执行循环（LangGraph StateGraph）─────────────────┐
        │                                                                   │
        │  Schema Profiler ─▶ Data Retriever ─▶ Code Executor ─▶ Analyst    │
        │       （数据探查）      （取数/SQL）      （沙箱执行）    （统计分析）│
        │                                                   │               │
        │                              ┌────────────────────┤               │
        │                              ▼                    ▼               │
        │                         Visualizer            Critic ──不通过──▶ 回│
        │                          （图表）          （校验/反思）    Planner │
        │                              │                │通过       重规划   │
        │                              └────────┬───────┘                   │
        └───────────────────────────────────────┼───────────────────────────┘
                                                ▼
                                           ┌─────────┐
                                           │ Reporter │──▶ 分析报告 / 总结输出
                                           └─────────┘
        共享层：Global State（LangGraph State）+ Artifact Store（中间产物：DataFrame/图表/SQL）
```

**核心数据流**：用户需求 → Planner 拆解为步骤计划（DAG）→ 各 Worker 按计划顺序/并行执行，产物写入共享 State → Critic 逐步校验（数字对不对、SQL 合不合理、图表是否匹配结论）→ 不合格带着失败原因回 Planner 局部重规划（最多 N 次，默认 2）→ Reporter 汇总所有产物生成报告。

## 2. Agent 职责定义（9 个）

| Agent | 职责  | 输入  | 输出  |
| --- | --- | --- | --- |
| **Supervisor** | 唯一对话入口；意图识别与路由：闲聊直接回、简单请求直达单 agent、复杂任务转 Planner；管理多轮对话上下文与澄清 | 用户消息、会话历史 | 路由决策 / 直达回复 |
| **Planner** | 把任务拆解为带依赖关系的步骤计划（DAG）；接收 Critic 的失败反馈做局部重规划 | 用户需求、数据源元信息、Critic 反馈 | 结构化执行计划 |
| **Schema Profiler** | 数据探查：上传文件做画像（列类型、缺失率、分布、样例值）；数据库拉取 schema、表关系、行数、枚举值字典 | 文件路径 / 库连接配置 | 数据画像报告（元数据 JSON） |
| **Data Retriever** | 依据画像和需求生成取数方案：数据库走 Text-to-SQL，文件走 pandas 读取代码；SQL 先 EXPLAIN/dry-run 校验 | 执行计划步骤、数据画像 | SQL / 取数代码 + 结果集引用 |
| **Code Executor** | 沙箱执行 Retriever/Analyst 生成的 Python 代码；捕获异常、超时、内存越界；返回 stdout/错误栈 | 代码 + 数据引用 | 执行结果 / 错误信息（错误回传生成方自修复，最多 3 次） |
| **Analyst** | 生成分析代码（描述统计、分组对比、相关性、趋势、异常检测），解读执行结果，提炼洞察结论 | 计划步骤、结果集 | 分析代码 + 洞察结论（含数字溯源） |
| **Visualizer** | 按洞察选图表类型，生成绘图代码（matplotlib/pyecharts），产出图片 artifact + 图表配置 | 分析结果、洞察 | 图表文件 + 说明 |
| **Critic** | 独立校验环：核对结论中每个数字能否溯源到执行结果、SQL 语义是否匹配需求、图表与结论是否一致；输出通过/驳回+具体理由 | 各步骤产物 | 校验报告（pass / fail + reason） |
| **Reporter** | 汇总全流程产物，按模板撰写分析报告（背景→数据说明→发现→图表→结论建议）；对话模式下则输出轻量总结 | 全部 artifacts + 洞察 | Markdown/PDF 报告 |

**设计要点**：

- **Code Executor 独立成 agent** 而非 Analyst 的内嵌工具：所有代码（取数、分析、绘图）统一走一个受控沙箱，安全边界清晰，且执行错误有统一的自修复回路。
- **Critic 与生成方分离**：校验者不看生成过程的"心路历程"，只对照产物和原始数据，避免自己给自己打分。
- **Artifact Store**：DataFrame、图片、SQL、中间结论都带 ID 存共享层，报告里的每个数字可溯源——这是数据分析系统可信度的关键。

## 3. Supervisor 路由三档判定

Supervisor 对每条消息做意图分类（structured output 输出 `route: chat | single_agent | plan`），LangGraph 用 conditional edge 分发：

1. **闲聊/元问题** → Supervisor 自己回复（hello、能力介绍、澄清追问），不触发 Planner，不调用任何 Worker
2. **简单数据请求** → 直达单个 Worker（"这表有多少行" → Schema Profiler；"刚才的图换成饼图" → Visualizer），复用已有 artifact，不重复取数
3. **复杂分析任务** → 转 Planner 走完整计划执行流程（"帮我做完整的销售分析报告"）

约 90% 的日常对话走轻量路径，只有真正需要端到端分析时才付完整流程的成本。

## 4. 动态执行计划（Plan 是数据，不是拓扑）

**执行计划图不是固定的**。固定的是「有哪些 agent 可用 + 消息协议 + 状态结构」，动态的是「每次任务激活哪几个 agent、以什么顺序和依赖关系执行」。Planner 每次根据任务动态生成 DAG，用不到的 agent 不会被调度。

**实现机制**：

1. **Plan 是 State 里的数据结构，不是图的拓扑**。LangGraph 的 StateGraph 包含所有 agent 节点，但执行时按 `plan: list[PlanStep]` 里的依赖关系走——每个节点执行前检查"计划里有没有我的步骤"，没有就直接跳过（conditional edge 按 plan 分发）。
2. **Planner 的裁剪原则写进提示词**：只安排完成目标所必需的步骤，产物形态跟着用户意图走（要图给图、要数给数、要报告才走 Reporter）。Critic 重规划时只重排出问题的局部步骤，不推倒重来。

**典型任务的计划示例**：

| 用户请求 | 生成的计划 |
| --- | --- |
| 「帮我看看各区域销售额，画个图就行」 | Schema Profiler（首次接触数据才需要）→ Data Retriever → Code Executor → Analyst → Visualizer → 轻量总结。**无 Reporter，不产报告** |
| 「这个月的毛利率是多少」（单指标查询） | Data Retriever → Code Executor → 返回。甚至不需要 Planner 建正式计划，Supervisor 判定为简单请求直达路由 |
| 「做一份完整的季度销售分析报告」 | Schema Profiler → Data Retriever → Analyst（多维分析，可并行拆子任务）→ Visualizer → Critic → Reporter。全流程，Critic 不过触发局部重规划 |
| 对话中途「刚才那张图换成按月份的折线图」 | Visualizer（复用已有 artifact，跳过取数） |

## 5. 共享状态（LangGraph State Schema）

```python
class AnalysisState(TypedDict):
    messages: Annotated[list, add_messages]   # 对话历史
    user_request: str                          # 当前任务描述
    data_sources: list[DataSource]             # 文件/数据库连接元信息
    data_profile: dict | None                  # Schema Profiler 产物
    plan: list[PlanStep]                       # 当前计划（含依赖、状态）——动态生成
    artifacts: dict[str, Artifact]             # ID → 产物（df/图/sql/结论）
    critic_report: CriticReport | None         # 最近一次校验结果
    replan_count: int                          # 重规划次数（上限 2）
    final_report: str | None                   # Reporter 产物
```

## 6. Agent 间消息协议

统一用结构化消息，不走自由文本，降低歧义：

```json
{
  "task_id": "step-003",
  "from": "planner", "to": "data_retriever",
  "action": "retrieve_data",
  "payload": { "requirement": "按月汇总各区域销售额", "depends_on": ["step-001"], "source_hint": "sales_db.orders" },
  "constraints": { "max_rows": 100000, "timeout_s": 60 }
}
```

Worker 返回统一为 `{status: ok|error, artifacts: [id...], summary, error_detail?}`。失败时 `error_detail` 直接回给生成方触发自修复，不上抛给用户。

## 7. 两种交互模式的流程

**对话式（轻量路径）**：用户问"这份表里缺失值多吗" → Supervisor 判定为简单请求 → 直达 Schema Profiler → 结果口语化返回。不建计划、不走 Critic 全流程（但涉及数字仍过一遍溯源检查）。追问"再按月份拆一下"→ Supervisor 携带上下文路由给 Analyst，复用已有 artifact，不重复取数。

**一次性任务（完整路径）**：用户提交数据集+"帮我做完整销售分析" → Planner 出计划 →（可选）计划先展示给用户确认（human-in-the-loop interrupt，可开关）→ 执行循环 → Critic 把关 → Reporter 出报告。中途执行进度通过 State 流式推送给前端。

## 8. 技术栈与存储选型（基于既有 MySQL + Redis + Qdrant）

**原则：不引入 SQLite，充分复用既有三件套。** Checkpointer 的本质是"按 thread_id 存每一步的 State 快照"，负载特征是高频小写入，MySQL 完全胜任，且相比 SQLite 具备两个必需能力：服务重启/容器漂移后状态不丢（SQLite 本地文件在多实例部署下直接失效）、多 worker 共享同一份状态。

| 组件  | 在本系统中的角色 |
| --- | --- |
| **MySQL** | ① LangGraph Checkpointer（`langgraph-checkpoint-mysql`，会话状态、任务中断恢复）② Artifact Store 的元数据表（artifact_id → 类型/来源步骤/溯源信息）③ 业务数据源本身 |
| **Redis** | ① 热数据缓存：大 DataFrame 的序列化结果（避免每次追问都重新取数）② 流式进度推送（pub/sub，前端实时看到"正在执行第 3/7 步"）③ 沙箱任务的分布式锁/限流 |
| **Qdrant** | ① 数据画像向量化：Schema Profiler 产物入库，支持"哪张表有销售额字段"类语义检索 ② 历史 SQL/洞察库：Text-to-SQL 时检索相似历史查询做 few-shot，越用越准 ③ 跨会话长期记忆 |

> **Qdrant 存历史 SQL 是 Text-to-SQL 准确率的最有效手段之一**：相似问题直接参考已验证过的 SQL，比裸生成可靠得多。

Checkpointer 初始化示例：

```python
from langgraph.checkpoint.mysql import MySQLSaver

checkpointer = MySQLSaver.from_conn_string("mysql://user:pass@host:3306/agent_db")
graph = workflow.compile(checkpointer=checkpointer)
```

**选型风险提示**：`langgraph-checkpoint-mysql` 的成熟度略低于 Postgres 版（Postgres 是 LangGraph 一等公民），实施时锁定版本号、跑通中断恢复的集成测试再上生产。若发现阻塞性问题，兜底方案是自己实现 `BaseCheckpointSaver` 接口（仅四个方法，工作量一天以内），而不是换数据库。

**其余关键依赖**：

- **编排**：LangGraph（StateGraph + conditional edges + interrupt + checkpointer）
- **沙箱**：Docker 容器执行 Python（受限网络、资源限额）
- **数据层**：pandas/polars + SQLAlchemy（多数据库方言）
- **可视化**：matplotlib（报告静态图）+ ECharts 配置（前端交互图）
- **模型分级**：Supervisor/Planner/Critic 用强模型，Executor 类（SQL/代码生成）可用快模型，按 agent 分级配置

## 9. **决策与开放问题**

- 已定决策 1：数据库权限强制只读，不提供写库通道，目前在skill中是这样设计的
- 已定决策 2：报告先做通用模板，不做行业定制，skill中有一个报告的
- 保留开放问题：前端形态待定——现有三栏代码展示界面与本系统形态不匹配，需重新设计，届时同步确定 Redis pub/sub 订阅端方案

## 10 当前与多智能体相关

AgentSpec 注册表  ai_service/agent/agent_registry.py
子agent工具：ai_service/tools/subagent
核心循环AgentRuntime：ai_service/agent/runtime.py，重要改造点

### 10.1 差异注入点

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