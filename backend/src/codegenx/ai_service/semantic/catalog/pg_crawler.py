"""
PG 元数据采集器：把目标库的 catalog 落到 semantic schema。

采集内容（S0）：
  - schema / 表 / 列 / 主键 / 外键关系，表与列注释作为业务描述的初始值
  - 表卡向量（可选，--no-embed 关闭）

状态策略（bootstrap）：
  - 有表注释 -> verified（数仓里 COMMENT 通常由人工维护，可直接召回）
  - 无表注释 -> draft（等人工/离线 Agent 补全后再核验）
  LLM 生成或推断的描述一律先 draft，不得自动置 verified。

用法：
  python -m codegenx.ai_service.semantic.catalog.pg_crawler \
      --name pagila --dsn "postgresql://user:pass@host:5432/pagila"
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os

import asyncpg

from shared import log
from codegenx.ai_service.semantic.store import pg_store

_TABLES_SQL = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       COALESCE(obj_description(c.oid, 'pg_class'), '') AS comment,
       GREATEST(c.reltuples, 0)::bigint AS row_estimate
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'semantic')
  AND n.nspname NOT LIKE 'pg_%'
ORDER BY 1, 2
"""

_COLUMNS_SQL = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       a.attname AS column_name,
       a.attnum  AS ordinal,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       COALESCE(col_description(c.oid, a.attnum), '') AS comment
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE a.attnum > 0
  AND NOT a.attisdropped
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'semantic')
  AND n.nspname NOT LIKE 'pg_%'
ORDER BY 1, 2, a.attnum
"""

_PK_SQL = """
SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS column_name
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE i.indisprimary
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'semantic')
  AND n.nspname NOT LIKE 'pg_%'
ORDER BY 1, 2, a.attnum
"""

_FK_SQL = """
SELECT sn.nspname AS schema_name,
       sc.relname  AS table_name,
       sa.attname  AS column_name,
       tn.nspname  AS ref_schema,
       tc.relname  AS ref_table,
       ta.attname  AS ref_column,
       con.conname AS constraint_name,
       gs.i        AS ordinal
FROM pg_constraint con
JOIN pg_class sc ON sc.oid = con.conrelid
JOIN pg_namespace sn ON sn.oid = sc.relnamespace
JOIN pg_class tc ON tc.oid = con.confrelid
JOIN pg_namespace tn ON tn.oid = tc.relnamespace
JOIN LATERAL generate_subscripts(con.conkey, 1) AS gs(i) ON TRUE
JOIN pg_attribute sa ON sa.attrelid = sc.oid AND sa.attnum = con.conkey[gs.i]
JOIN pg_attribute ta ON ta.attrelid = tc.oid AND ta.attnum = con.confkey[gs.i]
WHERE con.contype = 'f'
  AND sn.nspname NOT IN ('pg_catalog', 'information_schema', 'semantic')
  AND tn.nspname NOT IN ('pg_catalog', 'information_schema', 'semantic')
ORDER BY 1, 2, 7, gs.i
"""


def _source_hash(comment: str, columns: list[dict]) -> str:
    """表结构指纹：注释 + 列名/类型/可空，用于后续增量同步判重。"""
    raw = "|".join(
        [comment]
        + [f"{c['column_name']}:{c['data_type']}:{c['nullable']}" for c in columns]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _read_catalog(dsn: str) -> tuple[list, list, list, list]:
    """读取目标库 catalog（asyncpg 单连接不支持并发，按序执行）。"""
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        tables = await conn.fetch(_TABLES_SQL)
        columns = await conn.fetch(_COLUMNS_SQL)
        pks = await conn.fetch(_PK_SQL)
        fks = await conn.fetch(_FK_SQL)
    finally:
        await conn.close()
    return tables, columns, pks, fks


async def crawl(
    name: str,
    dsn: str,
    *,
    description: str = "",
    domain: str = "",
    dsn_secret_ref: str = "SEMANTIC_TARGET_DSN",
    embed: bool = True,
) -> dict:
    """采集一个 PG 数据源，返回统计摘要。"""
    tables, columns, pks, fks = await _read_catalog(dsn)

    columns_by_table: dict[tuple[str, str], list[dict]] = {}
    for row in columns:
        columns_by_table.setdefault((row["schema_name"], row["table_name"]), []).append(dict(row))

    pk_by_table: dict[tuple[str, str], list[str]] = {}
    for row in pks:
        pk_by_table.setdefault((row["schema_name"], row["table_name"]), []).append(row["column_name"])

    # FK 按约束聚合（复合外键要成对写入 from/to 列）
    fk_edges: dict[tuple, dict] = {}
    for row in fks:
        key = (
            row["schema_name"], row["table_name"],
            row["ref_schema"], row["ref_table"], row["constraint_name"],
        )
        edge = fk_edges.setdefault(key, {"from": [], "to": []})
        edge["from"].append(row["column_name"])
        edge["to"].append(row["ref_column"])

    ds_id = await pg_store.upsert_datasource(
        name, dialect="postgres", dsn_secret_ref=dsn_secret_ref, description=description,
    )

    tables_by_schema: dict[str, list[dict]] = {}
    for row in tables:
        tables_by_schema.setdefault(row["schema_name"], []).append(dict(row))

    table_ids: dict[tuple[str, str], int] = {}
    embed_targets: list[tuple[int, str]] = []

    for schema_name, schema_tables in tables_by_schema.items():
        schema_id = await pg_store.upsert_schema(ds_id, schema_name, domain)
        # 增量同步：读取现有表的 hash，结构未变则跳过 upsert/向量化
        existing = await pg_store.list_table_hashes(schema_id)
        for table in schema_tables:
            key = (schema_name, table["table_name"])
            cols = columns_by_table.get(key, [])
            comment = (table["comment"] or "").strip()
            new_hash = _source_hash(comment, cols)

            old = existing.get(table["table_name"])
            if old is not None and old[1] == new_hash:
                # 表结构未变：复用现有 table_id，跳过列 upsert 与向量化
                table_ids[key] = old[0]
                continue

            status = "verified" if comment else "draft"
            table_id = await pg_store.upsert_table(
                schema_id,
                name=table["table_name"],
                comment=comment,
                primary_key=pk_by_table.get(key, []),
                row_estimate=int(table["row_estimate"] or 0),
                status=status,
                source_hash=new_hash,
            )
            table_ids[key] = table_id

            for col in cols:
                await pg_store.upsert_column(
                    table_id,
                    name=col["column_name"],
                    ordinal=int(col["ordinal"]),
                    data_type=col["data_type"],
                    nullable=bool(col["nullable"]),
                    comment=(col["comment"] or "").strip(),
                )
            await pg_store.prune_columns(table_id, [c["column_name"] for c in cols])

            if embed:
                text = " ".join(filter(None, [
                    table["table_name"], comment, schema_name,
                ]))
                embed_targets.append((table_id, text))

        await pg_store.prune_tables(schema_id, [t["table_name"] for t in schema_tables])

    edges: list[dict] = []
    for (schema_name, table_name, ref_schema, ref_table, _con), pair in fk_edges.items():
        from_id = table_ids.get((schema_name, table_name))
        to_id = table_ids.get((ref_schema, ref_table))
        if from_id is None or to_id is None:
            continue
        edges.append({
            "from_table_id": from_id,
            "to_table_id": to_id,
            "from_columns": pair["from"],
            "to_columns": pair["to"],
        })
    relation_count = await pg_store.replace_fk_relations(ds_id, edges)

    embedded = 0
    if embed and embed_targets:
        embedded = await _embed_tables(embed_targets)

    summary = {
        "datasource": name,
        "schemas": len(tables_by_schema),
        "tables": len(table_ids),
        "columns": len(columns),
        "fk_relations": relation_count,
        "embedded_tables": embedded,
    }
    log.info("语义层采集完成: {}", summary)
    return summary


async def _embed_tables(targets: list[tuple[int, str]]) -> int:
    """表卡向量化；失败不影响采集结果（关键词通道仍可用）。"""
    try:
        from codegenx.ai_service.llm.embedding import get_embedding_client

        client = get_embedding_client()
        vectors = await client.embed_texts([text for _id, text in targets])
    except Exception as exc:
        log.warning("表卡向量化失败，本次仅采集结构化元数据: {}", exc)
        return 0

    count = 0
    for (table_id, _text), vec in zip(targets, vectors):
        await pg_store.update_table_embedding(table_id, vec)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="采集 PG 元数据到 semantic schema")
    parser.add_argument("--name", required=True, help="数据源名称（工具参数 datasource 的取值）")
    parser.add_argument("--dsn", default=os.getenv("SEMANTIC_TARGET_DSN", ""),
                        help="目标库只读 DSN，默认取环境变量 SEMANTIC_TARGET_DSN")
    parser.add_argument("--description", default="", help="数据源说明")
    parser.add_argument("--domain", default="", help="主题域（如 销售/会员）")
    parser.add_argument("--no-embed", action="store_true", help="跳过表卡向量化")
    args = parser.parse_args()

    if not args.dsn:
        parser.error("缺少 --dsn 或环境变量 SEMANTIC_TARGET_DSN")

    summary = asyncio.run(crawl(
        args.name, args.dsn,
        description=args.description, domain=args.domain, embed=not args.no_embed,
    ))
    print(summary)


if __name__ == "__main__":
    main()
