-- ============================================================
-- 语义层元数据（PostgreSQL，schema=semantic）
-- 定位：自然语言数据分析的“数据地图”真源，检索与关系遍历同库完成
-- 依赖：PostgreSQL 13+ / pgvector / pg_trgm
-- 使用：psql -f init/semantic_schema.sql
--
-- 约定：
--   1. 业务库以只读方式接入，连接串不落本库（dsn_secret_ref 存配置引用）
--   2. embedding 维度必须与 backend/config.json 的 embedding.dimensions 一致（默认 512）
--   3. status: draft=未审核（不参与自动召回）/ verified=可召回 / deprecated=下架
--   4. 关系边是“无向”的：查询时按 from_table_id / to_table_id 双向遍历
-- ============================================================

CREATE SCHEMA IF NOT EXISTS semantic;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 幂等补齐向量列：扩展被重装（DROP EXTENSION CASCADE）后旧表会丢失 embedding 列，
-- 仅靠 CREATE TABLE IF NOT EXISTS 无法恢复，故显式补列（列已存在时跳过）。
ALTER TABLE IF EXISTS semantic.meta_table
  ADD COLUMN IF NOT EXISTS embedding vector(512);
ALTER TABLE IF EXISTS semantic.meta_column
  ADD COLUMN IF NOT EXISTS embedding vector(512);
ALTER TABLE IF EXISTS semantic.meta_metric
  ADD COLUMN IF NOT EXISTS embedding vector(512);
ALTER TABLE IF EXISTS semantic.meta_verified_query
  ADD COLUMN IF NOT EXISTS embedding vector(512);

-- ------------------------------------------------------------
-- 1. 数据源注册（跨集市/跨库的入口）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_datasource (
  id             BIGSERIAL PRIMARY KEY,
  name           TEXT        NOT NULL UNIQUE,
  dialect        TEXT        NOT NULL DEFAULT 'postgres',
  dsn_secret_ref TEXT        NOT NULL DEFAULT '',
  description    TEXT        NOT NULL DEFAULT '',
  enabled        BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE semantic.meta_datasource IS '数据源注册表（name 是工具参数 datasource 的取值）';

-- ------------------------------------------------------------
-- 2. schema / 主题域
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_schema (
  id            BIGSERIAL PRIMARY KEY,
  datasource_id BIGINT      NOT NULL REFERENCES semantic.meta_datasource(id) ON DELETE CASCADE,
  name          TEXT        NOT NULL,
  domain        TEXT        NOT NULL DEFAULT '',
  description   TEXT        NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (datasource_id, name)
);
COMMENT ON TABLE semantic.meta_schema IS '库内 schema，domain 用于主题域路由（如 销售/会员/库存）';

-- ------------------------------------------------------------
-- 3. 表卡：检索召回的主单元
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_table (
  id          BIGSERIAL PRIMARY KEY,
  schema_id   BIGINT      NOT NULL REFERENCES semantic.meta_schema(id) ON DELETE CASCADE,
  name        TEXT        NOT NULL,
  comment     TEXT        NOT NULL DEFAULT '',
  business_desc TEXT      NOT NULL DEFAULT '',
  grain       TEXT        NOT NULL DEFAULT '',
  primary_key TEXT[]      NOT NULL DEFAULT '{}',
  row_estimate BIGINT     NOT NULL DEFAULT 0,
  tags        TEXT[]      NOT NULL DEFAULT '{}',
  owner       TEXT        NOT NULL DEFAULT '',
  status      TEXT        NOT NULL DEFAULT 'draft'
              CHECK (status IN ('draft', 'verified', 'deprecated')),
  source_hash TEXT        NOT NULL DEFAULT '',
  embedding   vector(512),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (schema_id, name)
);
COMMENT ON TABLE semantic.meta_table IS '表卡：业务含义/粒度/主键/主题域标签，召回单元';
COMMENT ON COLUMN semantic.meta_table.grain IS '粒度：一行数据代表什么（人工或建模阶段补全）';

CREATE INDEX IF NOT EXISTS idx_meta_table_ds
  ON semantic.meta_table (schema_id);
CREATE INDEX IF NOT EXISTS idx_meta_table_name_trgm
  ON semantic.meta_table USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_meta_table_desc_trgm
  ON semantic.meta_table USING gin (business_desc gin_trgm_ops);
-- 向量索引：pgvector>=0.5 用 HNSW；老版本请改成 ivfflat (lists=100)
CREATE INDEX IF NOT EXISTS idx_meta_table_embedding
  ON semantic.meta_table USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- ------------------------------------------------------------
-- 4. 列卡
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_column (
  id            BIGSERIAL PRIMARY KEY,
  table_id      BIGINT      NOT NULL REFERENCES semantic.meta_table(id) ON DELETE CASCADE,
  name          TEXT        NOT NULL,
  ordinal       INT         NOT NULL DEFAULT 0,
  data_type     TEXT        NOT NULL DEFAULT '',
  nullable      BOOLEAN     NOT NULL DEFAULT TRUE,
  comment       TEXT        NOT NULL DEFAULT '',
  business_desc TEXT        NOT NULL DEFAULT '',
  synonyms      TEXT[]      NOT NULL DEFAULT '{}',
  is_pii        BOOLEAN     NOT NULL DEFAULT FALSE,
  cardinality   BIGINT      NOT NULL DEFAULT 0,
  sample_values JSONB,
  embedding     vector(512),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (table_id, name)
);
COMMENT ON TABLE semantic.meta_column IS '列卡：业务含义/同义词/枚举值/是否敏感字段';

CREATE INDEX IF NOT EXISTS idx_meta_column_name_trgm
  ON semantic.meta_column USING gin (name gin_trgm_ops);

-- ------------------------------------------------------------
-- 5. 关系边（JOIN 路径）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_relation (
  id            BIGSERIAL PRIMARY KEY,
  datasource_id BIGINT      NOT NULL REFERENCES semantic.meta_datasource(id) ON DELETE CASCADE,
  from_table_id BIGINT      NOT NULL REFERENCES semantic.meta_table(id) ON DELETE CASCADE,
  to_table_id   BIGINT      NOT NULL REFERENCES semantic.meta_table(id) ON DELETE CASCADE,
  from_columns  TEXT[]      NOT NULL DEFAULT '{}',
  to_columns    TEXT[]      NOT NULL DEFAULT '{}',
  rel_type      TEXT        NOT NULL DEFAULT 'inferred'
                CHECK (rel_type IN ('fk', 'declared', 'inferred', 'mined')),
  confidence    REAL        NOT NULL DEFAULT 0.5,
  provenance    TEXT        NOT NULL DEFAULT '',
  verified      BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE semantic.meta_relation IS '表间关系边：fk=外键 / declared=语义模型声明 / inferred=命名推断 / mined=查询日志挖掘';
COMMENT ON COLUMN semantic.meta_relation.confidence IS '0~1，供模型判断 JOIN 可信度';

CREATE INDEX IF NOT EXISTS idx_meta_relation_from
  ON semantic.meta_relation (datasource_id, from_table_id);
CREATE INDEX IF NOT EXISTS idx_meta_relation_to
  ON semantic.meta_relation (datasource_id, to_table_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_meta_relation_edge
  ON semantic.meta_relation (from_table_id, to_table_id, rel_type, provenance);

-- ------------------------------------------------------------
-- 6. 指标卡（业务口径）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_metric (
  id             BIGSERIAL PRIMARY KEY,
  datasource_id  BIGINT      NOT NULL REFERENCES semantic.meta_datasource(id) ON DELETE CASCADE,
  name           TEXT        NOT NULL,
  synonyms       TEXT[]      NOT NULL DEFAULT '{}',
  definition     TEXT        NOT NULL DEFAULT '',
  expression     TEXT        NOT NULL DEFAULT '',
  default_filters TEXT       NOT NULL DEFAULT '',
  grain          TEXT        NOT NULL DEFAULT '',
  dimensions     TEXT[]      NOT NULL DEFAULT '{}',
  owner          TEXT        NOT NULL DEFAULT '',
  version        INT         NOT NULL DEFAULT 1,
  status         TEXT        NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'verified', 'deprecated')),
  embedding      vector(512),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (datasource_id, name)
);
COMMENT ON TABLE semantic.meta_metric IS '指标卡：口径定义 + SQL 表达式 + 可下钻维度（指标即代码）';
COMMENT ON COLUMN semantic.meta_metric.expression IS 'SQL 片段，如 sum(CASE WHEN status=1 THEN amount ELSE 0 END)';

CREATE INDEX IF NOT EXISTS idx_meta_metric_name_trgm
  ON semantic.meta_metric USING gin (name gin_trgm_ops);

-- ------------------------------------------------------------
-- 7. 已验证问答（few-shot 召回，提升准确率最快的手段）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic.meta_verified_query (
  id            BIGSERIAL PRIMARY KEY,
  datasource_id BIGINT      NOT NULL REFERENCES semantic.meta_datasource(id) ON DELETE CASCADE,
  question      TEXT        NOT NULL,
  sql           TEXT        NOT NULL,
  result_summary TEXT       NOT NULL DEFAULT '',
  verified_by   TEXT        NOT NULL DEFAULT '',
  embedding     vector(512),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE semantic.meta_verified_query IS '已验证问答对：按问题语义召回做 few-shot，仅人工确认后入库';

CREATE INDEX IF NOT EXISTS idx_meta_vq_question_trgm
  ON semantic.meta_verified_query USING gin (question gin_trgm_ops);
