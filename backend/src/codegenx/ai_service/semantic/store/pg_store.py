"""
语义层存储：PostgreSQL 真源 + pgvector 混合检索。

设计要点：
  - 向量以文本参数传入再显式 ::vector 转换，避免 asyncpg 缺少 vector 类型编解码器；
  - 召回排除 deprecated，draft 可召回但打标并降权（verified +0.05）；
  - JOIN 路径在单个 datasource 内遍历，关系边按无向处理；
  - 本模块只做数据访问，不做 prompt 拼装（见 retrieval/hybrid.py）。
"""
from __future__ import annotations

import json
from typing import Iterable

from db.postgres.postgres_client import execute, fetch, fetchrow
from shared import log

SCHEMA = "semantic"


def vector_text(vec: Iterable[float] | None) -> str:
    """把向量转成 pgvector 文本格式；None/空返回空串（调用方按关键词模式处理）。"""
    if not vec:
        return ""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


async def list_datasources() -> list[dict]:
    """仅返回已启用的数据源（与 resolve_datasource 的 enabled 过滤口径一致，避免误提示）。"""
    rows = await fetch(
        f"SELECT id, name, dialect, description, enabled FROM {SCHEMA}.meta_datasource WHERE enabled ORDER BY id"
    )
    return [dict(r) for r in rows]


async def resolve_datasource(name: str | None) -> tuple[int, str]:
    """按名称解析数据源；name 为空时要求全局唯一。

    失败抛 LookupError（工具层转成可读提示），不做静默兜底。
    """
    if name:
        row = await fetchrow(
            f"SELECT id, name FROM {SCHEMA}.meta_datasource WHERE name = $1 AND enabled", name
        )
        if not row:
            known = ", ".join(d["name"] for d in await list_datasources()) or "无"
            raise LookupError(f"未知数据源 '{name}'（已注册：{known}）")
        return int(row["id"]), str(row["name"])

    rows = await fetch(
        f"SELECT id, name FROM {SCHEMA}.meta_datasource WHERE enabled ORDER BY id LIMIT 2"
    )
    if not rows:
        raise LookupError("语义层暂无已注册数据源，请先执行 catalog 采集")
    if len(rows) > 1:
        known = ", ".join(str(r["name"]) for r in rows)
        raise LookupError(f"存在多个数据源（{known}），请显式指定 datasource")
    return int(rows[0]["id"]), str(rows[0]["name"])


# ═══════════════════════════════════════════════════════════
# 检索
# ═══════════════════════════════════════════════════════════

_TABLE_SEARCH_SQL = f"""
WITH q AS (
  SELECT $1::text AS qt,
         CASE WHEN $2::text = '' THEN NULL ELSE $2::text::vector END AS qv,
         $3::bigint AS ds_id,
         $4::text AS domain,
         $5::float8 AS vec_w
)
SELECT r.id, r.datasource, r.schema_name, r.name, r.comment, r.business_desc,
       r.grain, r.primary_key, r.row_estimate, r.tags, r.status, r.score
FROM (
  SELECT t.id, d.name AS datasource, s.name AS schema_name, t.name, t.comment,
         t.business_desc, t.grain, t.primary_key, t.row_estimate, t.tags, t.status,
         CASE WHEN q.qv IS NULL
              THEN similarity(coalesce(t.name,'') || ' ' || coalesce(t.business_desc,''), q.qt)
              ELSE q.vec_w * (1 - (t.embedding <=> q.qv))
                   + (1 - q.vec_w) * similarity(coalesce(t.name,'') || ' ' || coalesce(t.business_desc,''), q.qt)
         END
         + CASE WHEN t.status = 'verified' THEN 0.05 ELSE 0 END AS score
  FROM {SCHEMA}.meta_table t
  JOIN {SCHEMA}.meta_schema s ON s.id = t.schema_id
  JOIN {SCHEMA}.meta_datasource d ON d.id = s.datasource_id
  CROSS JOIN q
  WHERE t.status <> 'deprecated'
    AND d.id = q.ds_id
    AND (q.domain IS NULL OR s.domain = q.domain)
    AND (q.qv IS NULL OR t.embedding IS NOT NULL)
) r
WHERE r.score >= $6::float8
ORDER BY r.score DESC
LIMIT $7::int
"""


async def search_tables(
    query: str,
    embedding: list[float] | None,
    *,
    datasource: str | None = None,
    domain: str | None = None,
    top_k: int = 8,
    min_score: float = 0.25,
    vector_weight: float = 0.7,
) -> list[dict]:
    ds_id, _ = await resolve_datasource(datasource)
    rows = await fetch(
        _TABLE_SEARCH_SQL, query, vector_text(embedding), ds_id, domain or None,
        float(vector_weight), float(min_score), int(top_k),
    )
    return [dict(r) for r in rows]


_METRIC_SEARCH_SQL = f"""
WITH q AS (
  SELECT $1::text AS qt,
         CASE WHEN $2::text = '' THEN NULL ELSE $2::text::vector END AS qv,
         $3::bigint AS ds_id,
         $4::float8 AS vec_w
)
SELECT r.id, r.name, r.synonyms, r.definition, r.expression, r.default_filters,
       r.grain, r.dimensions, r.status, r.score
FROM (
  SELECT m.id, m.name, m.synonyms, m.definition, m.expression, m.default_filters,
         m.grain, m.dimensions, m.status,
         CASE WHEN q.qv IS NULL
              THEN similarity(m.name || ' ' || array_to_string(m.synonyms,' ') || ' ' || coalesce(m.definition,''), q.qt)
              ELSE q.vec_w * (1 - (m.embedding <=> q.qv))
                   + (1 - q.vec_w) * similarity(m.name || ' ' || array_to_string(m.synonyms,' ') || ' ' || coalesce(m.definition,''), q.qt)
         END
         + CASE WHEN m.status = 'verified' THEN 0.05 ELSE 0 END AS score
  FROM {SCHEMA}.meta_metric m
  CROSS JOIN q
  WHERE m.status <> 'deprecated'
    AND m.datasource_id = q.ds_id
    AND (q.qv IS NULL OR m.embedding IS NOT NULL)
) r
WHERE r.score >= $5::float8
ORDER BY r.score DESC
LIMIT $6::int
"""


async def search_metrics(
    query: str,
    embedding: list[float] | None,
    *,
    datasource: str | None = None,
    top_k: int = 5,
    min_score: float = 0.25,
    vector_weight: float = 0.7,
) -> list[dict]:
    ds_id, _ = await resolve_datasource(datasource)
    rows = await fetch(
        _METRIC_SEARCH_SQL, query, vector_text(embedding), ds_id,
        float(vector_weight), float(min_score), int(top_k),
    )
    return [dict(r) for r in rows]


_VERIFIED_QUERY_SEARCH_SQL = f"""
WITH q AS (
  SELECT $1::text AS qt,
         CASE WHEN $2::text = '' THEN NULL ELSE $2::text::vector END AS qv,
         $3::bigint AS ds_id,
         $4::float8 AS vec_w
)
SELECT r.id, r.question, r.sql, r.result_summary, r.score
FROM (
  SELECT v.id, v.question, v.sql, v.result_summary,
         CASE WHEN q.qv IS NULL
              THEN similarity(v.question, q.qt)
              ELSE q.vec_w * (1 - (v.embedding <=> q.qv)) + (1 - q.vec_w) * similarity(v.question, q.qt)
         END AS score
  FROM {SCHEMA}.meta_verified_query v
  CROSS JOIN q
  WHERE v.datasource_id = q.ds_id
    AND (q.qv IS NULL OR v.embedding IS NOT NULL)
) r
WHERE r.score >= $5::float8
ORDER BY r.score DESC
LIMIT $6::int
"""


async def search_verified_queries(
    question: str,
    embedding: list[float] | None,
    *,
    datasource: str | None = None,
    top_k: int = 3,
    min_score: float = 0.25,
    vector_weight: float = 0.7,
) -> list[dict]:
    ds_id, _ = await resolve_datasource(datasource)
    rows = await fetch(
        _VERIFIED_QUERY_SEARCH_SQL, question, vector_text(embedding), ds_id,
        float(vector_weight), float(min_score), int(top_k),
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# 表详情 / 关系路径
# ═══════════════════════════════════════════════════════════

_TABLE_LOOKUP_SQL = f"""
SELECT t.id, d.name AS datasource, s.name AS schema_name, t.name, t.comment,
       t.business_desc, t.grain, t.primary_key, t.row_estimate, t.tags, t.status
FROM {SCHEMA}.meta_table t
JOIN {SCHEMA}.meta_schema s ON s.id = t.schema_id
JOIN {SCHEMA}.meta_datasource d ON d.id = s.datasource_id
WHERE d.id = $1 AND t.name = $2
  AND ($3::text IS NULL OR s.name = $3)
  AND t.status <> 'deprecated'
ORDER BY (t.status = 'verified') DESC, t.id
"""

_COLUMNS_SQL = f"""
SELECT name, ordinal, data_type, nullable, comment, business_desc, synonyms,
       is_pii, cardinality, sample_values
FROM {SCHEMA}.meta_column
WHERE table_id = $1
ORDER BY ordinal, name
"""


async def get_table_cards(
    datasource: str | None, table: str, schema: str | None = None,
) -> list[dict]:
    ds_id, _ = await resolve_datasource(datasource)
    rows = await fetch(_TABLE_LOOKUP_SQL, ds_id, table, schema or None)
    return [dict(r) for r in rows]


async def get_columns(table_id: int) -> list[dict]:
    rows = await fetch(_COLUMNS_SQL, table_id)
    return [dict(r) for r in rows]


_JOIN_PATH_SQL = f"""
WITH RECURSIVE walk AS (
  SELECT r.from_table_id AS src,
         r.to_table_id   AS dst,
         ARRAY[r.from_table_id, r.to_table_id] AS path,
         1 AS hops,
         jsonb_build_array(jsonb_build_object(
           'from_table', ft.name, 'from_schema', fs.name,
           'to_table', tt.name, 'to_schema', ts.name,
           'from_columns', r.from_columns, 'to_columns', r.to_columns,
           'rel_type', r.rel_type, 'confidence', r.confidence, 'verified', r.verified
         )) AS steps
  FROM {SCHEMA}.meta_relation r
  JOIN {SCHEMA}.meta_table ft ON ft.id = r.from_table_id
  JOIN {SCHEMA}.meta_schema fs ON fs.id = ft.schema_id
  JOIN {SCHEMA}.meta_table tt ON tt.id = r.to_table_id
  JOIN {SCHEMA}.meta_schema ts ON ts.id = tt.schema_id
  WHERE r.datasource_id = $1
    AND ft.name = $2
    AND ($3::text IS NULL OR fs.name = $3)
  UNION ALL
  SELECT w.src,
         CASE WHEN r.from_table_id = w.dst THEN r.to_table_id ELSE r.from_table_id END AS dst,
         w.path || CASE WHEN r.from_table_id = w.dst THEN r.to_table_id ELSE r.from_table_id END,
         w.hops + 1,
         w.steps || jsonb_build_array(jsonb_build_object(
           'from_table', ft.name, 'from_schema', fs.name,
           'to_table', tt.name, 'to_schema', ts.name,
           'from_columns', r.from_columns, 'to_columns', r.to_columns,
           'rel_type', r.rel_type, 'confidence', r.confidence, 'verified', r.verified
         ))
  FROM walk w
  JOIN {SCHEMA}.meta_relation r
    ON r.datasource_id = $1
   AND (r.from_table_id = w.dst OR r.to_table_id = w.dst)
  JOIN {SCHEMA}.meta_table ft ON ft.id = r.from_table_id
  JOIN {SCHEMA}.meta_schema fs ON fs.id = ft.schema_id
  JOIN {SCHEMA}.meta_table tt ON tt.id = r.to_table_id
  JOIN {SCHEMA}.meta_schema ts ON ts.id = tt.schema_id
  WHERE w.hops < $6::int
    AND NOT (CASE WHEN r.from_table_id = w.dst THEN r.to_table_id ELSE r.from_table_id END) = ANY(w.path)
)
SELECT w.steps, w.hops
FROM walk w
WHERE w.dst IN (
  SELECT t.id
  FROM {SCHEMA}.meta_table t
  JOIN {SCHEMA}.meta_schema s ON s.id = t.schema_id
  WHERE t.name = $4
    AND s.datasource_id = $1
    AND ($5::text IS NULL OR s.name = $5)
)
ORDER BY w.hops ASC
LIMIT 3
"""


def _split_qualified(ref: str) -> tuple[str | None, str]:
    """把 'schema.table' 拆成 (schema, table)；无 schema 时返回 (None, ref)。"""
    ref = (ref or "").strip()
    if "." in ref:
        schema, table = ref.rsplit(".", 1)
        return schema or None, table
    return None, ref


async def find_join_path(
    datasource: str | None, from_table: str, to_table: str, *, max_hops: int = 3,
) -> list[dict]:
    ds_id, _ = await resolve_datasource(datasource)
    from_schema, from_name = _split_qualified(from_table)
    to_schema, to_name = _split_qualified(to_table)
    rows = await fetch(
        _JOIN_PATH_SQL, ds_id, from_name, from_schema, to_name, to_schema, int(max_hops),
    )
    # asyncpg 默认把 jsonb 解码为 str，这里统一转成结构化 steps
    result = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("steps"), str):
            item["steps"] = json.loads(item["steps"])
        result.append(item)
    return result


# ═══════════════════════════════════════════════════════════
# 采集写入（供 catalog/pg_crawler.py 调用）
# ═══════════════════════════════════════════════════════════

async def upsert_datasource(
    name: str, *, dialect: str = "postgres", dsn_secret_ref: str = "", description: str = "",
) -> int:
    row = await fetchrow(
        f"""
        INSERT INTO {SCHEMA}.meta_datasource (name, dialect, dsn_secret_ref, description, updated_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (name) DO UPDATE
          SET dialect = EXCLUDED.dialect,
              dsn_secret_ref = EXCLUDED.dsn_secret_ref,
              description = EXCLUDED.description,
              updated_at = now()
        RETURNING id
        """,
        name, dialect, dsn_secret_ref, description,
    )
    return int(row["id"])


async def upsert_schema(datasource_id: int, name: str, domain: str = "") -> int:
    row = await fetchrow(
        f"""
        INSERT INTO {SCHEMA}.meta_schema (datasource_id, name, domain, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (datasource_id, name) DO UPDATE
          SET domain = CASE WHEN EXCLUDED.domain = '' THEN {SCHEMA}.meta_schema.domain
                            ELSE EXCLUDED.domain END,
              updated_at = now()
        RETURNING id
        """,
        datasource_id, name, domain,
    )
    return int(row["id"])


async def upsert_table(
    schema_id: int, *, name: str, comment: str, primary_key: list[str],
    row_estimate: int, status: str, source_hash: str,
) -> int:
    row = await fetchrow(
        f"""
        INSERT INTO {SCHEMA}.meta_table
          (schema_id, name, comment, business_desc, primary_key, row_estimate, status, source_hash, updated_at)
        VALUES ($1, $2, $3, $3, $4::text[], $5, $6, $7, now())
        ON CONFLICT (schema_id, name) DO UPDATE
          SET comment = EXCLUDED.comment,
              -- 人工补全的业务描述不被采集覆盖；为空时才用表注释初始化
              business_desc = CASE WHEN {SCHEMA}.meta_table.business_desc = ''
                                   THEN EXCLUDED.business_desc
                                   ELSE {SCHEMA}.meta_table.business_desc END,
              primary_key = EXCLUDED.primary_key,
              row_estimate = EXCLUDED.row_estimate,
              -- 已审核/已下架是人工状态，采集不得回退
              status = CASE WHEN {SCHEMA}.meta_table.status IN ('verified', 'deprecated')
                            THEN {SCHEMA}.meta_table.status
                            ELSE EXCLUDED.status END,
              source_hash = EXCLUDED.source_hash,
              updated_at = now()
        RETURNING id
        """,
        schema_id, name, comment, primary_key, int(row_estimate), status, source_hash,
    )
    return int(row["id"])


async def upsert_column(
    table_id: int, *, name: str, ordinal: int, data_type: str, nullable: bool,
    comment: str,
) -> None:
    await execute(
        f"""
        INSERT INTO {SCHEMA}.meta_column
          (table_id, name, ordinal, data_type, nullable, comment, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, now())
        ON CONFLICT (table_id, name) DO UPDATE
          SET ordinal = EXCLUDED.ordinal,
              data_type = EXCLUDED.data_type,
              nullable = EXCLUDED.nullable,
              comment = EXCLUDED.comment,
              updated_at = now()
        """,
        table_id, name, int(ordinal), data_type, bool(nullable), comment,
    )


async def prune_columns(table_id: int, keep_names: list[str]) -> None:
    await execute(
        f"DELETE FROM {SCHEMA}.meta_column WHERE table_id = $1 AND name <> ALL($2::text[])",
        table_id, keep_names,
    )


async def prune_tables(schema_id: int, keep_names: list[str]) -> None:
    await execute(
        f"DELETE FROM {SCHEMA}.meta_table WHERE schema_id = $1 AND name <> ALL($2::text[])",
        schema_id, keep_names,
    )


async def list_table_hashes(schema_id: int) -> dict[str, tuple[int, str]]:
    """返回 schema 下所有表的 {table_name: (table_id, source_hash)}，供增量同步判重。"""
    rows = await fetch(
        f"SELECT id, name, source_hash FROM {SCHEMA}.meta_table WHERE schema_id = $1",
        schema_id,
    )
    return {r["name"]: (int(r["id"]), r["source_hash"] or "") for r in rows}


async def replace_fk_relations(datasource_id: int, edges: list[dict]) -> int:
    """用最新 FK 覆盖采集来源的关系边；人工声明的 declared 边不动。"""
    await execute(
        f"DELETE FROM {SCHEMA}.meta_relation WHERE datasource_id = $1 AND provenance = 'pg_constraint'",
        datasource_id,
    )
    count = 0
    for e in edges:
        await execute(
            f"""
            INSERT INTO {SCHEMA}.meta_relation
              (datasource_id, from_table_id, to_table_id, from_columns, to_columns,
               rel_type, confidence, provenance, verified, updated_at)
            VALUES ($1, $2, $3, $4::text[], $5::text[], 'fk', 1.0, 'pg_constraint', TRUE, now())
            ON CONFLICT (from_table_id, to_table_id, rel_type, provenance) DO UPDATE
              SET from_columns = EXCLUDED.from_columns,
                  to_columns = EXCLUDED.to_columns,
                  confidence = EXCLUDED.confidence,
                  verified = EXCLUDED.verified,
                  updated_at = now()
            """,
            datasource_id, e["from_table_id"], e["to_table_id"],
            e["from_columns"], e["to_columns"],
        )
        count += 1
    return count


async def update_table_embedding(table_id: int, vec: list[float] | None) -> None:
    if not vec:
        return
    try:
        await execute(
            f"UPDATE {SCHEMA}.meta_table SET embedding = $2::text::vector, updated_at = now() WHERE id = $1",
            table_id, vector_text(vec),
        )
    except Exception as exc:  # 维度不一致/索引缺失不应中断采集
        log.warning("表 {} 向量写入失败（检查 embedding.dimensions 与 vector 维度）: {}", table_id, exc)


async def update_metric_embedding(metric_id: int, vec: list[float] | None) -> None:
    if not vec:
        return
    try:
        await execute(
            f"UPDATE {SCHEMA}.meta_metric SET embedding = $2::text::vector, updated_at = now() WHERE id = $1",
            metric_id, vector_text(vec),
        )
    except Exception as exc:
        log.warning("指标 {} 向量写入失败: {}", metric_id, exc)


async def insert_verified_query(
    datasource_id: int, *, question: str, sql: str, result_summary: str = "",
    verified_by: str = "", embedding: list[float] | None = None,
) -> int:
    row = await fetchrow(
        f"""
        INSERT INTO {SCHEMA}.meta_verified_query
          (datasource_id, question, sql, result_summary, verified_by, embedding)
        VALUES ($1, $2, $3, $4, $5, NULLIF($6::text,'')::vector)
        RETURNING id
        """,
        datasource_id, question, sql, result_summary, verified_by, vector_text(embedding),
    )
    return int(row["id"])


async def upsert_metric(
    datasource_id: int, *, name: str, synonyms: list[str], definition: str,
    expression: str, default_filters: str = "", grain: str = "",
    dimensions: list[str] | None = None, owner: str = "", status: str = "draft",
) -> int:
    row = await fetchrow(
        f"""
        INSERT INTO {SCHEMA}.meta_metric
          (datasource_id, name, synonyms, definition, expression, default_filters,
           grain, dimensions, owner, status, updated_at)
        VALUES ($1, $2, $3::text[], $4, $5, $6, $7, $8::text[], $9, $10, now())
        ON CONFLICT (datasource_id, name) DO UPDATE
          SET synonyms = EXCLUDED.synonyms,
              definition = EXCLUDED.definition,
              expression = EXCLUDED.expression,
              default_filters = EXCLUDED.default_filters,
              grain = EXCLUDED.grain,
              dimensions = EXCLUDED.dimensions,
              owner = EXCLUDED.owner,
              status = EXCLUDED.status,
              version = {SCHEMA}.meta_metric.version + 1,
              updated_at = now()
        RETURNING id
        """,
        datasource_id, name, synonyms, definition, expression, default_filters,
        grain, dimensions or [], owner, status,
    )
    return int(row["id"])
