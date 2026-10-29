"""T1 评测闭环脚本（读库模式）：金标准题集 → 重放产生 trace → 读库评分写回 → 聚合报告。

子命令：
  import-cases  题集 JSON 导入 eval_cases（按 dataset+question upsert）
  replay        逐题调用 POST /ai/chat/gen 产生 trace（trace_id 编码 case_id）
  score         读 telemetry trace/spans 取 SQL，连业务库对比 gold_sql 算 EX/VES/ValidRate → 写 trace_scores
  report        聚合 trace_scores 输出 Markdown 报告

题集 JSON 格式：
  {"dataset": "pg_test", "description": "...", "cases": [
     {"question": "...", "gold_sql": "SELECT ...", "db_name": "business",
      "difficulty": "simple", "ops": "agg", "grain": "city", "multi_hop": false}]}

运行（cwd 任意）：
  backend/.venv/Scripts/python.exe data/evaluate/eval.py <subcommand> ...

 d:/Project/agent/CodeGenX/backend/.venv/Scripts/python.exe data/evaluate/eval.py replay --account boluo --password boluo123 --app-id app_0001 --ids 25,26,27,35,36,37,47,48,50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import asyncpg
import httpx

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "backend" / "src"))

from db.postgres.postgres_client import postgres_business, postgres_dsn  # noqa: E402
from shared.config.config import get_settings  # noqa: E402
from shared.constants import PUBLIC_SCHEMAS  # noqa: E402

EVAL_PREFIX = "eval"          # 重放 trace_id 前缀：eval-{case_id}-{随机}，score 据此关联
SQL_TIMEOUT_S = 30            # 业务库单条 SQL 执行超时
TRACE_WAIT_S = 15             # 重放后等 trace 落库超时


# ============================================================ 结果对比（EX 核心）

def _norm_value(v) -> str:
    """值规范化：数值统一浮点格式（消除 100 vs 100.0），interval 统一为天数，
    其余字符串去空白小写。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, timedelta):
        return f"{v.total_seconds() / 86400:.6f}".rstrip("0").rstrip(".")
    if isinstance(v, (int, float)):
        return f"{float(v):.6f}".rstrip("0").rstrip(".")
    return re.sub(r"\s+", " ", str(v)).strip().lower()


def _depaginate(sql: str) -> str:
    """去掉结尾 LIMIT/OFFSET（top-N 并列时比较全序更公平），无变化返回空串。"""
    stripped = re.sub(
        r"(?is)\s+(?:LIMIT\s+\d+\s*(?:OFFSET\s+\d+)?|OFFSET\s+\d+)\s*;?\s*$", "", sql,
    ).strip()
    return stripped if stripped != sql.strip() else ""


def _sig(rows, cols: tuple) -> list:
    """行集在指定列投影下的规范化签名（多重集，忽略行序）。"""
    return sorted(tuple(_norm_value(r[c]) for c in cols) for r in rows)


def rows_match(pred_rows, gold_rows) -> bool:
    """EX 判定（列名优先、列位兜底，双向投影，多重集比较忽略行序）：

    1. gold 列名 ⊆ pred 列名：agent 多选列（附带上名列/中文别名场景按列位兜底）；
    2. pred 列名 ⊆ gold 列名：agent 少选列（如 gold SELECT * 含 last_update 这类
       不影响答案的列）；
    3. 列名无交集时退回列位组合投影（兼容全别名场景，组合数封顶防爆）。
    """
    if not gold_rows or not pred_rows:
        return not gold_rows and not pred_rows
    gkeys = [str(k) for k in gold_rows[0].keys()]
    pkeys = [str(k) for k in pred_rows[0].keys()]
    gset, pset = set(gkeys), set(pkeys)
    # 1. 列名：gold ⊆ pred
    if gset <= pset:
        return _sig(gold_rows, tuple(gkeys)) == _sig(pred_rows, tuple(gkeys))
    # 2. 列名：pred ⊆ gold
    if pset <= gset:
        return _sig(gold_rows, tuple(pkeys)) == _sig(pred_rows, tuple(pkeys))
    # 3. 列位：gold 列数 == pred 列数直接比；pred 更宽时按组合投影
    gcols, pcols = len(gkeys), len(pkeys)
    if gcols > pcols:
        return False
    if gcols == pcols:
        return _sig(gold_rows, tuple(gkeys)) == _sig(pred_rows, tuple(pkeys))
    from itertools import combinations, islice
    projections = islice(combinations(pkeys, gcols), 30)
    gold_sig = _sig(gold_rows, tuple(gkeys))
    return any(gold_sig == _sig(pred_rows, proj) for proj in projections)


# ============================================================ 业务库执行

async def fetch_rows(dsn: str, sql: str) -> tuple[list, str | None]:
    """执行 SQL 返回 (行, 错误)。错误时行空、错误非空。"""
    conn = await asyncpg.connect(dsn=dsn, timeout=SQL_TIMEOUT_S)
    try:
        rows = await asyncio.wait_for(conn.fetch(sql), SQL_TIMEOUT_S)
        return [tuple(r) for r in rows], None
    except Exception as exc:
        return [], str(exc)
    finally:
        await conn.close()


# ============================================================ 元库访问

async def meta_conn() -> asyncpg.Connection:
    return await asyncpg.connect(dsn=postgres_dsn())


def _since(s: str) -> datetime:
    """--since 参数 → tz-aware UTC（asyncpg timestamptz 不接受 naive datetime）。"""
    dt = datetime.fromisoformat(s or "1970-01-01")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ============================================================ 子命令：import-cases

async def cmd_import(args) -> None:
    """题集 JSON → eval_datasets / eval_cases（dataset+question 唯一，upsert）。

    支持两种格式：
      1. {"dataset","description","cases":[...]}（标准格式）
      2. 扁平数组 [{question, sql, difficulty, ops, ...}]（question.json 格式，
         sql 映射为 gold_sql；dataset 名取 --name 或文件名，db_name 取 --db）
    """
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        data = {
            "dataset": args.name or Path(args.file).stem,
            "description": args.description,
            "cases": [{**c, "gold_sql": str(c.get("sql") or "")} for c in raw],
        }
    else:
        data = raw
        data["dataset"] = data.get("dataset") or args.name or Path(args.file).stem
    name = data["dataset"]
    conn = await meta_conn()
    try:
        ds_id = await conn.fetchval(
            """
            INSERT INTO telemetry.eval_datasets (name, description)
            VALUES ($1, $2)
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id
            """, name, data.get("description") or "")
        n_new = n_upd = 0
        for case in data.get("cases", []):
            if isinstance(case.get("ops"), list):
                case["ops"] = ",".join(str(o) for o in case["ops"])
            question = str(case.get("question") or "").strip()
            if not question:
                continue
            row = await conn.fetchrow(
                """
                INSERT INTO telemetry.eval_cases
                  (dataset_id, question, gold_sql, db_name, difficulty, ops, grain, multi_hop, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (dataset_id, question) DO UPDATE SET
                  gold_sql = EXCLUDED.gold_sql, db_name = EXCLUDED.db_name,
                  difficulty = EXCLUDED.difficulty, ops = EXCLUDED.ops, grain = EXCLUDED.grain,
                  multi_hop = EXCLUDED.multi_hop, status = EXCLUDED.status, updated_at = now()
                RETURNING (xmax = 0) AS inserted
                """,
                ds_id, question, str(case.get("gold_sql") or ""), str(args.db or case.get("db_name") or ""),
                str(case.get("difficulty") or ""), str(case.get("ops") or ""),
                str(case.get("grain") or ""), bool(case.get("multi_hop")),
                str(args.status or case.get("status") or "draft"),
            )
            n_new += 1 if row["inserted"] else 0
            n_upd += 0 if row["inserted"] else 1
        total = await conn.fetchval(
            "SELECT count(*) FROM telemetry.eval_cases WHERE dataset_id=$1", ds_id)
        print(f"[OK] 题集 {name}(id={ds_id}) 导入：新增 {n_new}，更新 {n_upd}，共 {total} 题")
    finally:
        await conn.close()


# ============================================================ 子命令：replay

async def _login(base_url: str, account: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:
        resp = await client.post("/api/user/login", json={
            "userAccount": account, "userPassword": password})
        resp.raise_for_status()
        body = resp.json()
        token = (body.get("data") or "")
        if not token:
            raise RuntimeError(f"登录失败: {body}")
        return token


async def _wait_trace(conn: asyncpg.Connection, trace_id: str) -> bool:
    """轮询等 turn_end flush 落库。"""
    deadline = time.time() + TRACE_WAIT_S
    while time.time() < deadline:
        if await conn.fetchval("SELECT 1 FROM telemetry.traces WHERE trace_id=$1", trace_id):
            return True
        await asyncio.sleep(0.5)
    return False


async def cmd_replay(args) -> None:
    """逐题重放：每题独立会话，trace_id = eval-{case_id}-{rand}；SSE 读到流结束。"""
    token = await _login(args.base_url, args.account, args.password)
    conn = await meta_conn()
    try:
        cond = "status='verified'" if not args.all else "TRUE"
        params: list = []
        if args.ids:
            cond += " AND id = ANY($1)"
            params.append([int(x) for x in args.ids.split(",") if x.strip()])
        params.append(args.limit)
        cases = await conn.fetch(
            f"SELECT * FROM telemetry.eval_cases WHERE {cond} ORDER BY id LIMIT ${len(params)}", *params)
        print(f"[OK] 待重放 {len(cases)} 题 → {args.base_url}")
        ok = fail = 0
        async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout,
                                     trust_env=False) as client:
            headers = {"Authorization": f"Bearer {token}"}
            for case in cases:
                trace_id = f"{EVAL_PREFIX}-{case['id']}-{uuid.uuid4().hex[:8]}"
                payload = {
                    "appId": args.app_id, "message": case["question"],
                    "sessionId": f"evalsess-{trace_id}",
                    "traceId": trace_id, "requestId": f"evalreq-{trace_id}",
                    "clientVersion": "eval-script",
                }
                if case["db_name"]:
                    payload["dbName"] = case["db_name"]
                t0 = time.time()
                try:
                    # SSE：读到流结束即 turn 结束（内容不需解析）
                    async with client.stream("POST", "/api/ai/chat/gen", json=payload, headers=headers) as resp:
                        if resp.status_code != 200:
                            raise RuntimeError(f"HTTP {resp.status_code}: {(await resp.aread())[:200]}")
                        async for _ in resp.aiter_text():
                            pass
                except Exception as exc:
                    print(f"[FAIL] case={case['id']} {exc}")
                    fail += 1
                    continue
                landed = await _wait_trace(conn, trace_id)
                mark = "OK" if landed else "NO-TRACE"
                ok += 1 if landed else 0
                fail += 0 if landed else 1
                print(f"[{mark}] case={case['id']} trace={trace_id} {time.time()-t0:.1f}s")
                await asyncio.sleep(args.interval)
        print(f"[DONE] 重放完成：落库 {ok}，未落库/失败 {fail}")
    finally:
        await conn.close()


# ============================================================ 子命令：score

async def _load_sqls(conn: asyncpg.Connection, trace_id: str) -> list[dict]:
    """取一轮 trace 的全部 execute_postgresql 尝试（时序），含 db_name/sql/status。

    语义数据自 tool_semantics 独立表读取（骨架/语义分离，docs/链路追踪设计.md）。
    """
    rows = await conn.fetch(
        """
        SELECT attributes, success
        FROM telemetry.tool_semantics
        WHERE trace_id=$1 AND tool_name='execute_postgresql'
        ORDER BY created_at
        """, trace_id)
    out = []
    for row in rows:
        attrs = row["attributes"] if isinstance(row["attributes"], dict) else json.loads(row["attributes"] or "{}")
        sql = str(attrs.get("sql") or "").strip()
        if sql:
            out.append({
                "sql": sql,
                "db_name": str(attrs.get("datasource") or attrs.get("db_name") or ""),
                "status": "ok" if row["success"] else "error",
            })
    return out


async def cmd_score(args) -> None:
    """读库评分：pred 取 trace 最后一次 SQL 尝试，gold 执行对比；写回 trace_scores。"""
    conn = await meta_conn()
    try:
        if args.trace_id:
            traces = await conn.fetch("SELECT trace_id FROM telemetry.traces WHERE trace_id=$1", args.trace_id)
        else:
            # 默认评全部重放 trace；--since 只评某时刻之后（避免重复）
            since = _since(args.since)
            traces = await conn.fetch(
                "SELECT trace_id FROM telemetry.traces WHERE trace_id LIKE $1 AND started_at > $2",
                f"{EVAL_PREFIX}-%", since)
        print(f"[OK] 待评分 trace {len(traces)} 条")
        cache: dict[str, asyncpg.Connection] = {}
        n_scored = n_skip = 0
        try:
            for row in traces:
                trace_id = row["trace_id"]
                m = re.match(rf"{EVAL_PREFIX}-(\d+)-", trace_id)
                if not m:
                    n_skip += 1
                    continue
                case = await conn.fetchrow("SELECT * FROM telemetry.eval_cases WHERE id=$1", int(m.group(1)))
                if not case or not case["gold_sql"]:
                    n_skip += 1
                    continue
                result = await _score_one(conn, cache, trace_id, case)
                await _write_scores(conn, trace_id, case["id"], result)
                n_scored += 1
                print(f"[{result['category'].upper()}] trace={trace_id} EX={result['ex']} VES={result['ves']} {result['category']}")
        finally:
            for c in cache.values():
                await c.close()
        print(f"[DONE] 评分 {n_scored} 条，跳过 {n_skip} 条")
    finally:
        await conn.close()


async def _score_one(conn, cache: dict, trace_id: str, case) -> dict:
    """单条评分：全部成功 SQL 逐条重执行作候选（时序），首个与 gold 匹配者判 EX/VES。

    候选制理由：agent 常在给出最终 SQL 后再做分页取全量/聚合复核等后续执行，
    只看最后一条会把正确答案误判为 mismatch。
    """
    sqls = await _load_sqls(conn, trace_id)
    if not sqls:
        return {"ex": 0.0, "ves": 0.0, "valid": 0.0, "category": "no_sql", "detail": "无 execute_postgresql span"}
    ok_sqls: list[dict] = []
    for s in sqls:  # 去重保持时序
        if s["status"] == "ok" and s["sql"] and s["sql"] not in [c["sql"] for c in ok_sqls]:
            ok_sqls.append(s)
    if not ok_sqls:
        return {"ex": 0.0, "ves": 0.0, "valid": 0.0, "category": "sql_error",
                "detail": f"全部 {len(sqls)} 次 SQL 执行失败"}
    db_name = ok_sqls[-1]["db_name"] or case["db_name"]
    if not db_name:
        return {"ex": 0.0, "ves": 0.0, "valid": 1.0, "category": "db_unknown", "detail": "无法确定目标库"}
    dsn = postgres_business(db_name)
    if db_name not in cache:
        conn_biz = await asyncpg.connect(dsn=dsn, timeout=SQL_TIMEOUT_S)
        # 业务表可能挂在 datasource.schema（如 business.dvdrental），评分连接补 search_path
        schema = next(
            (s.split(".", 1)[1] for s in PUBLIC_SCHEMAS
             if "." in s and s.split(".", 1)[0] == db_name),
            "",
        )
        if schema:
            await conn_biz.execute(f'SET search_path TO "{schema}", public')
        cache[db_name] = conn_biz
    conn_biz = cache[db_name]
    # gold：金标准执行（金标准报错记为 gold_error，不入失败归类但需人工修题）
    t0 = time.perf_counter()
    try:
        gold_rows = [dict(r) for r in await asyncio.wait_for(conn_biz.fetch(case["gold_sql"]), SQL_TIMEOUT_S)]
        gold_time = time.perf_counter() - t0
        gold_err = None
    except Exception as exc:
        gold_rows, gold_err, gold_time = [], str(exc), time.perf_counter() - t0
    if gold_err:
        return {"ex": 0.0, "ves": 0.0, "valid": 1.0, "category": "gold_error", "detail": f"金标准 SQL 执行失败: {gold_err[:300]}"}
    gold_dep = _depaginate(case["gold_sql"])
    # 候选逐条重执行，首个匹配即成功
    n_valid = len(ok_sqls)
    last_err = ""
    last_rows = 0
    for cand in ok_sqls:
        # information_schema 探查类 SQL 不会匹配 gold，跳过重执行省时
        low = cand["sql"].lower()
        if "information_schema" in low or low.lstrip().startswith("select column_name"):
            continue
        t0 = time.perf_counter()
        try:
            pred_rows = [dict(r) for r in await asyncio.wait_for(conn_biz.fetch(cand["sql"]), SQL_TIMEOUT_S)]
            pred_time = time.perf_counter() - t0
        except Exception as exc:
            last_err = str(exc)
            continue
        last_rows = len(pred_rows)
        if rows_match(pred_rows, gold_rows):
            ves = min(1.0, gold_time / pred_time) if pred_time > 0 else 0.0
            return {"ex": 1.0, "ves": ves, "valid": 1.0, "category": "ok", "detail": ""}
        # top-N 并列：两侧去 LIMIT 后比全序（gold 带 LIMIT 且并列边界不稳定时才可能误判）
        if gold_dep:
            cand_dep = _depaginate(cand["sql"]) or cand["sql"]
            try:
                pred2 = [dict(r) for r in await asyncio.wait_for(conn_biz.fetch(cand_dep), SQL_TIMEOUT_S)]
                gold2 = [dict(r) for r in await asyncio.wait_for(conn_biz.fetch(gold_dep), SQL_TIMEOUT_S)]
                if rows_match(pred2, gold2):
                    return {"ex": 1.0, "ves": min(1.0, gold_time / pred_time) if pred_time > 0 else 0.0,
                            "valid": 1.0, "category": "ok", "detail": "top-n tie（去 LIMIT 全序匹配）"}
            except Exception as exc:
                last_err = str(exc)
    return {"ex": 0.0, "ves": 0.0, "valid": 1.0 if n_valid else 0.0,
            "category": "result_mismatch",
            "detail": f"候选 {n_valid} 条无一匹配；最后执行 {last_rows} 行 vs gold {len(gold_rows)} 行{('; ' + last_err[:200]) if last_err else ''}"}


async def _write_scores(conn: asyncpg.Connection, trace_id: str, case_id: int, result: dict) -> None:
    """写回三类评分（同一 trace 重评时先清旧，保证一份最新结果）。"""
    await conn.execute("DELETE FROM telemetry.trace_scores WHERE trace_id=$1 AND evaluator='eval_script'", trace_id)
    rows = [
        (trace_id, "EX", result["ex"], result["category"], "eval_script", case_id),
        (trace_id, "VES", result["ves"], "", "eval_script", case_id),
        (trace_id, "valid_sql", result["valid"], "", "eval_script", case_id),
    ]
    if result["detail"]:
        rows.append((trace_id, "detail", None, result["detail"][:1000], "eval_script", case_id))
    await conn.executemany(
        """
        INSERT INTO telemetry.trace_scores (trace_id, name, value, comment, evaluator, case_id)
        VALUES ($1,$2,$3,$4,$5,$6)
        """, rows)


# ============================================================ 子命令：report

async def cmd_report(args) -> None:
    """聚合 trace_scores → Markdown 报告（总览 + 失败归类 + 维度分组）。"""
    conn = await meta_conn()
    try:
        since = _since(args.since)
        ex = await conn.fetchval(
            """
            SELECT avg(s.value) FROM telemetry.trace_scores s
            WHERE s.name='EX' AND s.evaluator='eval_script' AND s.created_at > $1
            """, since)
        valid = await conn.fetchval(
            """
            SELECT avg(s.value) FROM telemetry.trace_scores s
            WHERE s.name='valid_sql' AND s.evaluator='eval_script' AND s.created_at > $1
            """, since)
        ves = await conn.fetchval(
            """
            SELECT avg(s.value) FROM telemetry.trace_scores s
            WHERE s.name='VES' AND s.evaluator='eval_script' AND s.created_at > $1
            """, since)
        n = await conn.fetchval(
            """
            SELECT count(DISTINCT trace_id) FROM telemetry.trace_scores
            WHERE name='EX' AND evaluator='eval_script' AND created_at > $1
            """, since)
        cats = await conn.fetch(
            """
            SELECT comment AS category, count(*) AS cnt FROM telemetry.trace_scores
            WHERE name='EX' AND evaluator='eval_script' AND created_at > $1 AND value = 0
            GROUP BY comment ORDER BY cnt DESC
            """, since)
        dims = await conn.fetch(
            """
            SELECT c.difficulty, c.ops, c.multi_hop, avg(s.value) AS ex, count(*) AS cnt
            FROM telemetry.trace_scores s
            JOIN telemetry.eval_cases c ON c.id = s.case_id
            WHERE s.name='EX' AND s.evaluator='eval_script' AND s.created_at > $1
            GROUP BY c.difficulty, c.ops, c.multi_hop ORDER BY ex
            """, since)

        lines = [
            "# 评测报告", "",
            f"- 评测范围：{since} 之后，共 {n} 轮",
            f"- **EX**：{ex:.2%}" if ex is not None else "- EX：无数据",
            f"- **Valid SQL Rate**：{valid:.2%}" if valid is not None else "- Valid SQL Rate：无数据",
            f"- **VES**：{ves:.3f}" if ves is not None else "- VES：无数据",
            "", "## 失败归类（EX=0）", "",
            "| 归类 | 数量 |", "|---|---|",
        ]
        lines += [f"| {c['category'] or '-'} | {c['cnt']} |" for c in cats] or ["| 无 | 0 |"]
        lines += ["", "## 维度分组（EX）", "", "| difficulty | ops | multi_hop | EX | 数量 |", "|---|---|---|---|---|"]
        lines += [f"| {d['difficulty']} | {d['ops']} | {d['multi_hop']} | {d['ex']:.2%} | {d['cnt']} |" for d in dims]
        report = "\n".join(lines) + "\n"
        out = Path(args.out)
        out.write_text(report, encoding="utf-8")
        print(report)
        print(f"[OK] 报告已写 {out}")
    finally:
        await conn.close()


# ============================================================ CLI

def main() -> None:
    parser = argparse.ArgumentParser(description="T1 评测闭环（读库模式）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import-cases", help="题集 JSON 导入 eval_cases")
    p.add_argument("--file", required=True, help="题集 JSON 路径")
    p.add_argument("--name", default="", help="dataset 名（默认取 JSON dataset 字段或文件名）")
    p.add_argument("--description", default="", help="dataset 描述（扁平格式时使用）")
    p.add_argument("--db", default="", help="覆盖业务库 db_name（扁平格式时使用，如 business）")
    p.add_argument("--status", default="", help="覆盖题目 status（verified/draft，默认 draft）")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("replay", help="逐题调用 chat API 产生 trace")
    p.add_argument("--base-url", default=f"http://127.0.0.1:{get_settings().app_port}")
    p.add_argument("--account", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--app-id", default="main")
    p.add_argument("--ids", default="", help="只重放指定 case id，如 1,2,3")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true", help="含 draft 题目（默认只重放 verified）")
    p.add_argument("--timeout", type=int, default=300, help="单题 SSE 读取超时秒")
    p.add_argument("--interval", type=float, default=1.0, help="题间间隔秒")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("score", help="读库评分写回 trace_scores")
    p.add_argument("--trace-id", default="", help="只评指定 trace")
    p.add_argument("--since", default="", help="只评该 ISO 时刻之后（避免重复评分）")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("report", help="聚合输出 Markdown 报告")
    p.add_argument("--since", default="", help="聚合该 ISO 时刻之后")
    p.add_argument("--out", default=str(_REPO / "data" / "evaluate" / "report.md"))
    p.set_defaults(func=cmd_report)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
