"""临时：清理 eval-* trace 及关联数据（重跑前清理用）。"""
import asyncio
import sys

import asyncpg

DSN = "postgresql://postgres:boluo123@127.0.0.1:5432/postgres"


async def main() -> None:
    keep_prefix = sys.argv[1] if len(sys.argv) > 1 else ""  # 传 case_id 列表则只删这些
    conn = await asyncpg.connect(dsn=DSN)
    cond = "trace_id LIKE 'eval-%'"
    args: list = []
    if keep_prefix:
        ids = [int(x) for x in keep_prefix.split(",")]
        cond += " AND substring(trace_id from 'eval-(\\d+)-')::int = ANY($1)"
        args.append(ids)
    tids = [r["trace_id"] for r in await conn.fetch(
        f"SELECT trace_id FROM telemetry.traces WHERE {cond}", *args)]
    if tids:
        await conn.execute("DELETE FROM telemetry.trace_spans WHERE trace_id = ANY($1)", tids)
        await conn.execute("DELETE FROM telemetry.tool_semantics WHERE trace_id = ANY($1)", tids)
        await conn.execute("DELETE FROM telemetry.trace_scores WHERE trace_id = ANY($1)", tids)
        await conn.execute("DELETE FROM telemetry.traces WHERE trace_id = ANY($1)", tids)
    print(f"[OK] 清理 {len(tids)} 条 eval trace")
    await conn.close()


asyncio.run(main())
