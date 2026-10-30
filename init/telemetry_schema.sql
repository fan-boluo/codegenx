-- ============================================================
-- 全链路追踪（PostgreSQL，schema=telemetry）
-- 定位：评测数据基座——session/turn/llm/tool 全链路落库，
--       离线评测（EX/VES）、用户反馈、迭代优化均从本库计算
-- 参考：Langfuse trace→observation 树 + OTel GenAI 语义约定
-- 依赖：PostgreSQL 13+（JSONB）
-- 使用：psql -f init/telemetry_schema.sql
--
-- 约定：
--   1. traces = 一轮请求（turn）的根，session 级聚合由 session_id 派生
--   2. trace_spans = span 树（评测数据主体）：turn → llm/tool/retrieval
--   3. 载荷已按配置截断（payload_max_chars / rows_preview），JSONB 存储
--   4. 量级上来再启用按月分区（预留）
-- ============================================================

CREATE SCHEMA IF NOT EXISTS telemetry;

-- ------------------------------------------------------------
-- 1. traces：一次根事件（问答 turn / 后台摘要等）的骨架；载荷细节在 spans/tags
--    trace_id = request.trace_id，缺省用 request_id
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry.traces (
  trace_id     TEXT PRIMARY KEY,
  session_id   TEXT        NOT NULL,
  app_id       TEXT        NOT NULL DEFAULT 'main',
  user_id      TEXT        NOT NULL DEFAULT '',
  event        TEXT        NOT NULL DEFAULT 'qa',  -- 根事件类型：qa 问答 / session_summary 后台摘要
  status       TEXT        NOT NULL DEFAULT 'running',  -- running/completed/stopped/failed
  error        TEXT,
  total_tokens INTEGER     NOT NULL DEFAULT 0,
  model        TEXT        NOT NULL DEFAULT '',     -- 本轮主力模型（调用次数最多）
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,    -- 本轮 prompt tokens 累计
  completion_tokens INTEGER NOT NULL DEFAULT 0,    -- 本轮 completion tokens 累计
  duration_ms  DOUBLE PRECISION,
  started_at   TIMESTAMPTZ,
  ended_at     TIMESTAMPTZ,
  tags         JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- tool_calls/context_usage/note 等汇总及外部注入（eval_set 等）
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE telemetry.traces IS '一次根事件的骨架（问答/后台任务）：输入输出快照不做，细节看 trace_spans';

-- ------------------------------------------------------------
-- 2. trace_spans：span 树（评测数据主体）
--    op_type: 根 = turn（问答轮）/ turn_end / session_end（后台任务触发事件）
--             子 = llm（messages 输入、completion 输出）
--             / tool（SQL/文件等工具）/ retrieval（语义层检索 5 工具）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry.trace_spans (
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT        NOT NULL,
  parent_span_id TEXT,                          -- 父 span（turn 为根）
  session_id     TEXT        NOT NULL,
  turn_id        TEXT,                          -- request_id
  op_type        TEXT        NOT NULL,
  name           TEXT        NOT NULL,          -- turn / agent_name / tool_name
  agent          TEXT        NOT NULL DEFAULT '',
  seq            INTEGER     NOT NULL DEFAULT 0,  -- turn 内时序
  started_at     TIMESTAMPTZ,
  duration_ms    DOUBLE PRECISION,
  status         TEXT        NOT NULL DEFAULT 'ok',  -- ok / error
  error          TEXT,
  input          JSONB,                          -- messages_ref / arguments + tool_call_id（截断后）
  output         JSONB,                          -- content/tool_calls / result（截断后）
  attributes     JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- model/usage/sql/rows_preview 等
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE telemetry.trace_spans IS 'span 树：llm/tool 输入输出全量（截断）落库，评测数据主体';

-- 索引：按 trace 取全树 / 按会话回放 / 按类型聚合
CREATE INDEX IF NOT EXISTS idx_traces_started_at ON telemetry.traces (started_at);
CREATE INDEX IF NOT EXISTS idx_traces_session    ON telemetry.traces (session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_spans_trace   ON telemetry.trace_spans (trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_session ON telemetry.trace_spans (session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_spans_type    ON telemetry.trace_spans (op_type, started_at);

-- ------------------------------------------------------------
-- 3. trace_scores：评分/反馈（T1）
--    一条 trace 可有多类评分：EX / VES / valid_sql（脚本）、llm_judge、user_feedback
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry.trace_scores (
  id         BIGSERIAL    PRIMARY KEY,
  trace_id   TEXT         NOT NULL,
  name       TEXT         NOT NULL,              -- EX / VES / valid_sql / user_feedback / llm_judge
  value      DOUBLE PRECISION,                   -- 0~1 或数值；纯反馈可空
  comment    TEXT,                               -- 失败归类（no_sql/sql_error/result_mismatch）或反馈文本
  evaluator  TEXT         NOT NULL DEFAULT '',   -- eval_script / llm_judge / user
  case_id    BIGINT,                             -- 关联 eval_cases.id（在线流量可空）
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE telemetry.trace_scores IS '评分与反馈：评测脚本/LLM judge/用户反馈统一写回入口';
CREATE INDEX IF NOT EXISTS idx_scores_trace ON telemetry.trace_scores (trace_id, name);
CREATE INDEX IF NOT EXISTS idx_scores_case  ON telemetry.trace_scores (case_id);

-- ------------------------------------------------------------
-- 4. eval_datasets / eval_cases：金标准题集（T1）
--    status 复用语义层状态机：draft（待校验）→ verified（可评测）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry.eval_datasets (
  id          BIGSERIAL    PRIMARY KEY,
  name        TEXT         NOT NULL UNIQUE,
  description TEXT         NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE telemetry.eval_datasets IS '评测题集（如开源 Spider/BIRD 子集或自建题）';

CREATE TABLE IF NOT EXISTS telemetry.eval_cases (
  id          BIGSERIAL    PRIMARY KEY,
  dataset_id  BIGINT       NOT NULL REFERENCES telemetry.eval_datasets(id),
  question    TEXT         NOT NULL,
  gold_sql    TEXT         NOT NULL DEFAULT '',   -- 金标准 SQL（评测重放后的 EX 基准）
  db_name     TEXT         NOT NULL DEFAULT '',   -- 目标业务库（postgres_business 参数）
  difficulty  TEXT         NOT NULL DEFAULT '',   -- simple / moderate / challenging
  ops         TEXT         NOT NULL DEFAULT '',   -- 操作类型：agg/join/window/...
  grain       TEXT         NOT NULL DEFAULT '',   -- 结果粒度
  multi_hop   BOOLEAN      NOT NULL DEFAULT FALSE,
  status      TEXT         NOT NULL DEFAULT 'draft',  -- draft / verified
  -- gold 结果缓存：gold_sql hash 一致时直接复用，免每次评分重执行（VES 用缓存耗时）
  gold_rows     JSONB,       -- gold 执行结果行（Decimal/timedelta 归一后序列化）
  gold_exec_ms  REAL,        -- gold 执行耗时（VES 计算）
  gold_sql_hash TEXT,        -- gold_sql 压空白 md5（不一致则缓存失效重算）
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE telemetry.eval_cases IS '金标准题目：重放产生 trace，评分与 gold_sql 对比写回 trace_scores';
CREATE UNIQUE INDEX IF NOT EXISTS uq_cases_dataset_question ON telemetry.eval_cases (dataset_id, question);
CREATE INDEX IF NOT EXISTS idx_cases_dataset_status ON telemetry.eval_cases (dataset_id, status);

-- ------------------------------------------------------------
-- 5. tool_semantics：工具调用语义数据（评测/归因消费，与 span 骨架分家）
--    采集：AFTER_TOOL_CALL 监听器（hook/tool_semantics.py）；
--    关联：span_id 直连 trace_spans（主通道）；tool_call_id = LLM 调用 id
--    （assistant.tool_calls[].id，同时记于 tool span input），协议层对账；
--    trace_id 保留：span 受 retention 清理后仍可溯源，评测按 trace 过滤免 join；
--    入参/结果语义分列（tool_args/tool_result）——只存提取后的结构化语义，
--    不重复存 span.input/output 已有的全文 IO；不受 retention 清理
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry.tool_semantics (
  tool_call_id  TEXT        PRIMARY KEY,   -- LLM 调用 id（非 LLM 发起时 uuid 兜底）
  trace_id      TEXT        NOT NULL,
  span_id       TEXT,                      -- 执行骨架外联键（trace_spans.span_id）
  tool_name     TEXT        NOT NULL,
  success       BOOLEAN     NOT NULL DEFAULT TRUE,
  tool_args     JSONB,                     -- 入参语义：execute_postgresql {sql, datasource}
  tool_result   JSONB,                     -- 结果语义：{row_count, rows_preview} / {recalled_tables}
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE telemetry.tool_semantics IS '工具调用语义数据：入参/结果分列，经 tool_call_id 与 span 外联，评测归因独立读取入口';
CREATE INDEX IF NOT EXISTS idx_tool_semantics_trace ON telemetry.tool_semantics (trace_id);
CREATE INDEX IF NOT EXISTS idx_tool_semantics_tool  ON telemetry.tool_semantics (tool_name, created_at);
