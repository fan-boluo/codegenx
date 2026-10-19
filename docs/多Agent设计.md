# 多智能体数据分析系统 · 架构设计（当前实现版）

> 本文档描述 **P1~P3 改造后** 的实际架构（并行派发 → 决策工具化 → 模板合一），
> 是当前代码的设计契约与对照基准。原 LangGraph 方案与早期 AgentRuntime 方案均已废弃。
> 遗留项见文末。

## 1. 系统总览

- 调度入口：`AgentRuntime` 已退役，`AutoPlanManager`（agent/auto_plan_manager.py）是唯一调度循环
- 跨 agent 路由：调度器主循环**直接方法接力**（`current_msg` 内存变量），无收件箱/队列，停止时无需 purge
- MessageBus 职责收窄：仅用于用户请求 inbound 与 SSE 事件下发（`publish_runtime_event`），不承载 agent 间消息
- 轮次上限：全局跨 agent 轮次取 `config.multi_agents.turn_max`（while 循环判断），超限 FORCE STOP；单 agent 内部 ReAct 步数取各自 `maxSteps`
- 系统仅走多 agent 链路（无单 agent 退化路径）

核心目录：

```
backend/src/codegenx/ai_service/
├── agent/
│   ├── auto_plan_manager.py      # 调度循环 + _execute_batch 并行执行
│   ├── runtime_schema.py         # RuntimeSessionState / ActivateTurn / TurnStoppedError / stop_signal
│   └── agents/
│       ├── agent.py              # AgentMessage 扁平协议
│       ├── base_react_agent.py   # ReAct 循环 + 原生 tool_calls + _reply 回传出口 + 失败兜底
│       ├── intention_agent.py    # SupervisorAgent（编排入口）
│       ├── supervisor_decision.py# SupervisorDecisionEngine（单一 profile 模板）
│       ├── supervisor_board.py   # TaskBoardCoordinator（看板协调）
│       └── data_explore_agent.py / csv_explore_agent.py / planner ...
├── context/session_context.py    # chat_messages 会话历史真相源
├── task/task_manager.py          # TaskBoardService（按 request 落盘）
├── session/session_pool.py       # 会话池（idle 回收 + 停止逻辑下沉）
├── tools/                        # execute_sql / list_csv_files / run_python / 决策工具描述符
├── tool_executor.py              # ToolExecutor 统一执行回填
└── tool_handler.py               # ToolRegistry + build_agent_tools 白名单
```

## 2. 消息协议（AgentMessage 扁平一等字段）

| 字段 | 语义 |
|---|---|
| `task` | 派发跳=任务文本；回传跳保留原值（追溯） |
| `result` | 回传跳=执行结果；终答跳（recipient="user"）=面向用户的答复 |
| `task_id` / `step_id` | 看板闭环握手字段（派发下传、回传保留、直答清除） |
| `direct_reply` | 直答标记（派发方打标、执行方消费） |
| `dispatch_batch` | 批信封载荷（recipient=`__batch__` 时携带多个派发跳） |
| `sender` / `recipient` / `success` | 每跳必设 |

跳向语义契约：**派发跳填 task、回传跳填 result、终答跳填 result**。
旧协议（NextMessageModel / MessagePayload / next/message dict）已删除，禁止复活。

## 3. BaseReActAgent（执行型智能体基类）

### ReAct 循环
`think → act/tool_calls → observation → verify`，循环边界为 observation 返回 `success=True` 或达到 `maxSteps`。

- **observation 协议**：把入参消息**原地改造**为下一跳（必设 sender/recipient/success），`run()` 尾部直接返回 observation，不再新建消息
- **_reply 统一回传出口**：回传调用方保留 task_id、清 direct_reply、task 保留原值；直答 user 清除 task_id/step_id 握手字段
- **verify 失败兜底按调用方分流**：
  - 入口 agent（sender=user，如 supervisor）→ 终答跳兜底（"执行失败，请稍后重试"）
  - 执行 agent → **失败回传调用方**：task 保留原值、task_id/step_id 闭环握手保留、`result="执行失败：{reason}"`、`success=False`——supervisor 保留审查/重试/换路决策权，看板任务不悬挂 in_progress

### 工具化执行
- 工具白名单：`config.json agents[].tools` → `build_agent_tools`（白名单非空才启用 function calling；supervisor/planner 的决策工具除外，见 §4）
- `_normalize_tool_call` 双格式归一化：非流式 OpenAI 嵌套格式与流式扁平格式统一
- ToolExecutor 统一执行并回填 tool 消息（成功回传 data、失败回传 message，`MAX_TOOL_RESULT_CHARS=8000` 截断；`_success` 记账字段回填前 pop 不进 LLM API）
- 工具执行结果固定 `ToolResult` 类型，仅按 `success` 字段判定状态

### 会话历史注入
- 执行型 agent（data_explore/csv_explore）**默认不注入**（任务自包含：只看 task 文本）
- Supervisor 覆盖注入 `chat_messages[:-1]`（末条内容由本轮输入承载，见 §4.5）

## 4. Supervisor 编排

三类拆分：**SupervisorAgent**（编排流程）/ **SupervisorDecisionEngine**（单一模板）/ **TaskBoardCoordinator**（看板确定性操作）。
`profile` 必须为类属性（`register()` 是 classmethod 读 `cls.profile`）。

### 4.1 决策协议（P2 工具化）
JSON 文本决策协议已退役，决策走 function-calling，两个**控制流工具**在 `SupervisorAgent._execute_tool_calls` 本地拦截执行（不走 ToolExecutor）：

- `dispatch_agent(agent, task, direct_reply?)`：登记看板 + 累积待派发跳
- `request_plan(task)`：登记计划请求（observation 路由 planner）
- **不调用工具且输出非空文本 = 终答**（direct_answer / FINISH 合一）

`observation` 三分支优先级：**计划请求 > 待派发（单项普通跳 / 多项批信封）> 终答**。

### 4.2 并行派发（P1 批信封）
- 同一回复多次调用 dispatch_agent → 批信封（recipient=`__batch__`，`dispatch_batch` 携带全部派发跳）
- 批 >1 **强制 `direct_reply=False`**（执行完必须回传审查汇总）
- 调度器 `_execute_batch`：`asyncio` 并行（`Semaphore(max_parallel)`），逐个看板闭环 + 写入会话历史，单失败隔离不炸批，停止信号传播
- 批摘要以 `sender="batch"` 回灌 supervisor（run 阶段1 跳过重复消费）

### 4.3 防重复派发守卫
`_exec_dispatch_agent` 两道判重（登记前）：
1. **同 run 判重**：本回复已登记相同 `(agent, task)` → 拒绝（qwen-flash 曾登记确认后原样重复 tool_calls）
2. **同 request 判重**：看板归一化 subject 判重（`find_same_task`，去除全部空白比较，**含已完成任务**——重跑相同任务只会产生重复/矛盾结果）

登记确认消息必须带累计数与停止指引：「全部任务登记完毕后直接输出简短说明文本结束本轮，不要再调用 dispatch_agent」。矛盾核实最多一次（新口径任务描述）。

### 4.4 双模式编排
- **自主编排（direct，默认）**：路由/审查 think → dispatch_agent 派发 → 收结果 → 再决策 → 终答
- **planner-executor**：request_plan → planner 产出计划（PlanOutput JSON 放 `msg.result`）→ `consume_planner_plan` 落看板（step→TaskRecord，depends_on→blocked_by DAG）→ `dispatch_next_ready` 波次确定性派发（免 think）→ 无就绪任务才 think 审查
  - 计划模式僵局逃生：dispatch_agent 优先 `pick_task_for_agent` 派看板待办（状态机天然防重）
  - 跨 run 编排上下文：`SessionContext.orchestration`（mode/plan/step_task_map/turn_task_ids/last_task_id/execution_trace），turn 级重置

### 4.5 模板合一与输入组装（P3）
- **单一 profile**（无 route/review 双模板、无 `_is_review` 标志、无 `_prepare_agent` 二次覆盖）：约束合并为决策三选一 / 直接答复时机 / dispatch 场景 / direct_reply 判据 / request_plan 场景+僵局逃生 / 汇总职责 / task 自包含 / 矛盾核实一次 / 每任务只调用一次
- 路由与审查的区分**完全由输入侧承载**：`_extract_task_text` 按 `msg.sender` 分支
  - `user`/空 → 用户请求原文（路由）
  - `batch` → 批审查框架（全部子任务结果摘要）
  - 其它（执行智能体/planner）→ 单任务审查框架（最新回传以最高显著性呈现，result 空回落 task）
- `_prepare_prompt_params` 统一注入 `agents_list_desc`（含 planner，request_plan 可用性由运行时校验）+ `board_desc`
- `[:-1]` 历史切片的正确性依赖上述改写：route 末条=请求原文、单回传末条=最新回传、batch 末条=批内最后子任务回传（批摘要已含其内容）

### 4.6 职责分层与直答
- 取数类 agent 只产出其数据源内的原始统计结果；**跨数据源对比/汇总/换算由 supervisor 终答完成**，禁止塞进派发 task 或 planner 步骤
- 派发 task 必须完全自包含（执行 agent 看不到对话历史与其它任务）
- **direct_reply 直答模式**：恰好 1 个子任务且结果无需跨任务汇总 → `direct_reply=true` 下传；执行 agent `_resolve_final_target` 判定（`msg.direct_reply` 且本轮工具未全失败 → 直答 user，省审查轮）；工具全失败自动降级回传 supervisor
- 直答已知代价（可接受）：审查兜底丢失（靠降级+多轮追问纠正）、看板任务停留 in_progress 至下一轮 reset（仅展示层）、execution_trace 不记录该轮

## 5. 任务看板

- 按 **request 维度**存储：`.data/{user}/{app}/session/{sid}/.tasks/{request_id}.json` 单文件（该请求全部 TaskRecord 列表）
- `task_id` 按 request 从 1 计数，`(request_id, task_id)` 复合键天然唯一（跨请求不串号）
- `get_board` 仅渲染当前 request 任务，历史请求不进 prompt
- **task_id 精确闭环握手**：supervisor 三条派发路径（CALL_EXECUTOR 登记 / 计划模式 dispatch_board_task / NEXT_STEP）均下传 task_id；执行 agent `_reply` 回传原样保留（直答 user 清除）；`consume_child_result` 按 task_id 精确闭环，仅缺失 task_id 的异常消息才按 executor_agent+turn_ids 兜底

## 6. 会话与停止机制

- `stop_request` 仅停止单个请求、保留会话；`stop_session` 停止会话所有任务并关闭会话——**分离实现**
- 统一 `stop_signal` 检查点机制 + `TurnStoppedError` 异常处理复用
- 停止/状态管理逻辑统一下沉 `session_pool.py`，chat_service 为薄委托层
- 会话池 idle 回收带 `_is_quiescent` 活跃检查（仅回收无在途任务的空闲会话）
- **会话复用不改写 `session.request`**：request 由 worker pop 时统一设置；入队时改写会致执行中请求的 request_id/publish_context/db_name 串号

## 7. 执行智能体

- **data_explore**：元信息查询当前为硬编码测试桩（见遗留项 H1）；SQL 查询结果默认截断保留前 N 行 + 总行数 + 截断标记，避免大表撑爆上下文
- **csv_explore**：`_resolve_csv_context` 统一路径解析（裸文件名/相对路径→用户项目 code_dir，绝对路径直接用，越界拒绝）
- **run_python**：`__CSV_DIR__` 占位符；dev 本地 subprocess / 非 dev docker 分支，与 SQL 复用同一执行容器
- SQL rows 序列化必须 `json.dumps(..., default=str)`（Decimal/datetime）
- 会话历史默认不注入（任务自包含）

## 8. 遗留项（P4 设计对照审查，2026-10-17）

### 已确认保留的现状
- **H1**：data_explore 元信息查询为硬编码测试桩（prj_ec 三表，真实 information_schema 实现存于 `_query_available_db_info_bak`）——**用户确认有意保留**，后续将转向向量数据库按提示词检索相关表，恢复前不得当作缺陷修复

### 中危待办
1. `data_mysql.compute_correlation` 未导入——全量+2~10 数值列时必 NameError，被 except 吞为「相关性计算失败」；补 import
2. `BaseReActAgent._reply` 恒 `success=True`——子任务失败信号丢失，consume_child_result 全按成功闭环；observation 应传入成败
3. `supervisor_board` execution_trace 固定 `"success": True`——失败轨迹失真；改 `bool(msg.success)`
4. 看板每操作全文件读+全量回写——N 任务一次闭环 O(N²) IO；建议进程内缓存+合并写
5. `_execute_batch` 批内每子任务各计 1 轮——多波计划（15 任务）易提前触发 FORCE STOP(turn_max=20)；建议批计 1 轮或校准上限

### 低危待办
1. `agent/runtime.py` 死文件（无引用，与 auto_plan_manager 重复）——删除
2. `tool_executor.parallel/sequence_execute` 空壳、`data_mysql._resolve_sample_pct` 导入未用——删除
3. `max_tool_iterations/max_same_tool_calls` 配置读后未落地——删除或落地
4. `data_mysql` SQL 行截断 50 与设计值 20 不一致——对齐口径

### 其它已知代价（可接受，暂不处理）
- 直答模式：审查兜底丢失 / 看板停留 in_progress / execution_trace 缺记录（见 §4.6）
- 每跳 `spec.cls()` 重建 agent 实例（supervisor 每跳重建 engine+coordinator）——量级小可接受，量大时缓存
