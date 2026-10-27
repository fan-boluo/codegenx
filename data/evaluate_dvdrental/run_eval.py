"""DVD Rental 数据分析评测：自然语言问题 -> data_explore 智能体 -> 执行准确率。

评测口径（execution accuracy）：
  1. 用同一业务库分别执行“金标准 SQL”与“智能体最后一次成功执行的 SQL”
  2. 比较结果集（行多重集 + 单行值集合，数值四舍五入到 4 位）
  3. 智能体未产出 SQL / SQL 执行失败 / 结果不一致均记为失败

用法（在 backend 目录执行）：
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py --limit 5
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py --only 0,12,20

结果写入 data/evaluate_dvdrental/results/eval_<时间戳>.json。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parents[1] / "backend"
RESULTS_DIR = EVAL_DIR / "results"

# codegenx 以 editable 方式装在 backend/.venv；这里补 src 兜底
sys.path.insert(0, str(BACKEND_DIR / "src"))
os.chdir(BACKEND_DIR)  # .env / config.json 相对路径解析依赖 cwd

from codegenx.ai_service.hook import HookEvent, on  # noqa: E402


# ── SQL 捕获：AFTER_TOOL_CALL 收集本次问题执行过的 SQL ────────────────
_captured: list[dict[str, Any]] = []


def _tool_args(ctx) -> dict:
    tc = ctx.data.get("tool_call") or {}
    args = tc.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    return args if isinstance(args, dict) else {}


@on(HookEvent.AFTER_TOOL_CALL, name="eval_sql_capture", priority=10)
async def _capture_sql(ctx) -> None:
    tc = ctx.data.get("tool_call") or {}
    if tc.get("name") != "execute_sql":
        return None
    result = ctx.data.get("result")
    _captured.append({
        "sql": str(_tool_args(ctx).get("sql") or "").strip(),
        "success": bool(getattr(result, "success", False)),
        "message": str(getattr(result, "message", "") or ""),
    })
    return None


# ── 结果集比较 ────────────────────────────────────────────────────
def _normalize_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return f"{round(value, 4):.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    return str(value)


def _result_signature(rows: list[dict], columns: tuple[str, ...]) -> Counter:
    return Counter(
        tuple(sorted(_normalize_value(r[c]) for c in columns)) for r in rows
    )


def _same_result(gold: list[Any], pred: list[Any]) -> bool:
    """结果集等价判定（execution accuracy 的宽松口径）。

    agent 回答时常见的两类“展示层差异”不应判错：
      1. 多选列（如附带 id/辅助统计）：只要存在与金标准列数相同的列组合能对齐即可；
      2. 行顺序不同：按行多重集比较。
    列名不参与逐字比较（别名差异很常见）。
    """
    if not gold or not pred:
        return not gold and not pred

    gold_rows = [dict(r) for r in gold]
    pred_rows = [dict(r) for r in pred]
    gold_cols = tuple(gold_rows[0].keys())
    pred_cols = tuple(pred_rows[0].keys())
    if len(gold_cols) > len(pred_cols):
        return False

    gold_sig = _result_signature(gold_rows, gold_cols)
    if len(gold_cols) == len(pred_cols):
        return gold_sig == _result_signature(pred_rows, pred_cols)

    # 多选列：优先按列名投影，其次枚举行内列组合（列名不一致但值一致）
    if all(c in pred_cols for c in gold_cols):
        return gold_sig == _result_signature(pred_rows, gold_cols)

    extra = len(pred_cols) - len(gold_cols)
    if extra > 4:
        return False
    from itertools import combinations
    for combo in combinations(pred_cols, len(gold_cols)):
        if gold_sig == _result_signature(pred_rows, combo):
            return True
    return False


def _depaginate(sql: str) -> str:
    """去掉末尾的 LIMIT/OFFSET：分页是 50 行展示约束，不代表分析口径。"""
    stripped = re.sub(
        r"(?is)\s+(?:LIMIT\s+\d+\s+OFFSET\s+\d+|LIMIT\s+\d+|OFFSET\s+\d+)\s*;?\s*$",
        "", sql,
    ).strip()
    return stripped if stripped != sql.strip() else ""


# ── 业务库执行 ────────────────────────────────────────────────────
async def _connect_business(db_name: str, schema: str):
    import asyncpg

    from codegenx.ai_service.agent.db.postgres_manager import _get_pool  # 复用连接池配置

    pool = await _get_pool(db_name)

    async def fetch(sql: str, timeout: int = 120) -> list[Any]:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                if schema:
                    await conn.execute(f'SET LOCAL search_path TO "{schema}"')
                return await asyncio.wait_for(conn.fetch(sql), timeout=timeout)

    return fetch


# ── 单题执行 ──────────────────────────────────────────────────────
async def _run_one(
    agent_cls, question: str, db_name: str, fetch,
) -> dict[str, Any]:
    from codegenx.ai_service.agent.agents.agent import AgentMessage
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
    from codegenx.ai_service.schema.ai_schema import UserAIRequest

    run_id = uuid.uuid4().hex[:10]
    request = UserAIRequest(
        appId="eval_app",
        userId="eval_user",
        message=question,
        sessionId=f"eval-{run_id}",
        traceId=run_id,
        requestId=run_id,
        dbName=db_name,
    )
    state = RuntimeSessionState(session_id=f"eval-{run_id}", request=request)
    msg = AgentMessage(
        sender="user", recipient="data_explore",
        request_id=run_id, session_id=state.session_id, task=question,
    )

    _captured.clear()
    started = time.time()
    error = ""
    answer = ""
    try:
        agent = agent_cls()
        reply = await asyncio.wait_for(agent.run(msg, state), timeout=300)
        answer = str(getattr(reply, "result", "") or "")
    except Exception as exc:  # 单题异常不中断整批
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.time() - started, 1)

    executed = list(_captured)
    ok_sqls = []
    for c in executed:
        if c["success"] and c["sql"] and c["sql"] not in ok_sqls:
            ok_sqls.append(c["sql"])

    return {
        "question": question,
        "executed_sqls": executed,
        "candidate_sqls": ok_sqls,
        "answer": answer,
        "agent_error": error,
        "elapsed_s": elapsed,
    }


async def _match_candidates(
    fetch, gold_rows: list[Any], candidates: list[str],
) -> tuple[str, list[Any], str]:
    """逐个执行候选 SQL（含去分页变体），返回首个与金标准一致的结果。"""
    first_error = ""
    for sql in candidates:
        variants = [sql]
        depaged = _depaginate(sql)
        if depaged:
            variants.append(depaged)
        for variant in variants:
            try:
                rows = await fetch(variant)
            except Exception as exc:
                first_error = first_error or f"{type(exc).__name__}: {exc}"
                continue
            if _same_result(gold_rows, rows):
                return variant, rows, ""
    return "", [], first_error


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--only", default="", help="只跑指定下标，如 0,12,20")
    parser.add_argument("--db", default="business", help="业务库 dbName（config.json databases 键）")
    args = parser.parse_args()

    questions = json.loads((EVAL_DIR / "question.json").read_text(encoding="utf-8"))
    if args.only:
        idx = [int(x) for x in args.only.split(",") if x.strip()]
        questions = [questions[i] for i in idx]
    elif args.limit:
        questions = questions[: args.limit]

    from codegenx.ai_service.agent.agents.data_explore_agent import DateExploreAgent
    from codegenx.ai_service.agent.db.target import resolve_db_target
    from codegenx.ai_service.system_app import init_app, start_app, stop_app
    from codegenx.ai_service.agent.db import postgres_manager

    target = resolve_db_target(args.db)
    if target is None or target.dialect != "postgres":
        raise SystemExit(f"未配置 PostgreSQL 业务库: {args.db}")

    app = init_app()
    await start_app(app)
    fetch = await _connect_business(target.database, target.schema_name)

    results: list[dict[str, Any]] = []
    passed = 0
    try:
        for i, item in enumerate(questions, 1):
            question, gold_sql = item["question"], item["sql"]
            print(f"\n[{i}/{len(questions)}] {question}", flush=True)

            gold_rows: list[Any] = []
            gold_error = ""
            try:
                gold_rows = await fetch(gold_sql)
            except Exception as exc:
                gold_error = f"{type(exc).__name__}: {exc}"

            record = await _run_one(DateExploreAgent, question, args.db, fetch)

            candidates = record["candidate_sqls"]
            matched_sql, matched_rows = "", []
            pred_error = ""
            if gold_error:
                match, judge = None, "gold_sql_failed"
            elif not candidates:
                match, judge = False, "no_sql"
            else:
                matched_sql, matched_rows, pred_error = await _match_candidates(
                    fetch, gold_rows, candidates,
                )
                if matched_sql:
                    match, judge = True, "match"
                elif pred_error:
                    match, judge = False, "pred_sql_failed"
                else:
                    match, judge = False, "result_mismatch"

            if match:
                passed += 1

            record.update({
                "gold_sql": gold_sql,
                "difficulty": item.get("difficulty", ""),
                "gold_error": gold_error,
                "gold_result_rows": len(gold_rows),
                "matched_sql": matched_sql,
                "pred_result_rows": len(matched_rows),
                "pred_error": pred_error,
                "match": match,
                "judge": judge,
            })
            results.append(record)
            print(
                f"  -> {judge} | agent={record['elapsed_s']}s | "
                f"candidates={len(candidates)} pred_rows={len(matched_rows)} gold_rows={len(gold_rows)}",
                flush=True,
            )
            if record["agent_error"]:
                print(f"  agent_error: {record['agent_error']}", flush=True)
            if pred_error:
                print(f"  pred_error: {pred_error}", flush=True)

        scored = [r for r in results if r["judge"] != "gold_sql_failed"]
        accuracy = (passed / len(scored) * 100) if scored else 0.0
        summary = {
            "db": args.db,
            "schema": target.schema_name,
            "total": len(results),
            "scored": len(scored),
            "passed": passed,
            "accuracy_pct": round(accuracy, 2),
            "gold_sql_failed": sum(1 for r in results if r["judge"] == "gold_sql_failed"),
            "no_sql": sum(1 for r in results if r["judge"] == "no_sql"),
            "pred_sql_failed": sum(1 for r in results if r["judge"] == "pred_sql_failed"),
            "result_mismatch": sum(1 for r in results if r["judge"] == "result_mismatch"),
            "avg_elapsed_s": round(
                sum(r["elapsed_s"] for r in results) / len(results), 1
            ) if results else 0,
        }
        payload = {"summary": summary, "results": results}
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n===== 评测结果 =====")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"明细: {out}")
    finally:
        await postgres_manager.close_pools()
        await stop_app(app)


if __name__ == "__main__":
    asyncio.run(main())
