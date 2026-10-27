"""数据分析评测框架（Layer 1）：自然语言问题 → agent → 多维度评测。

评测维度：
  1. EX（Execution Accuracy）：结果集匹配率，沿用宽松口径（多重集 + 列数容错）
  2. Valid SQL Rate：agent 产出的 SQL 中可执行比例
  3. VES（Valid Efficiency Score）：预测 SQL vs 金标准 SQL 的执行时长比（pred/gold 越大越好）
  4. Tool Path Quality：每题主工具调用数、SQL 执行次数、是否用了信息_schema 探查
  5. 失败模式自动归类：result_mismatch 的根因自动检测

用法（在 backend 目录执行）：
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py --limit 5
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py --only 0,12,20
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py --agent data_explore
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\run_eval.py --baseline  # 固化为 baseline

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
BASELINE_PATH = EVAL_DIR / "baseline.json"

sys.path.insert(0, str(BACKEND_DIR / "src"))
os.chdir(BACKEND_DIR)  # .env / config.json 相对路径解析依赖 cwd

from codegenx.ai_service.hook import HookEvent, on  # noqa: E402


# ═══════════════════════════════════════════════════════════════════
# Hook 捕获：AFTER_TOOL_CALL 记录所有工具调用（执行耗时 + 结果）
# ═══════════════════════════════════════════════════════════════════
_tool_trace: list[dict[str, Any]] = []
_tool_start_time: dict[str, float] = {}


def _tool_args(ctx) -> dict:
    tc = ctx.data.get("tool_call") or {}
    args = tc.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    return args if isinstance(args, dict) else {}


@on(HookEvent.BEFORE_TOOL_CALL, name="eval_trace_start", priority=10)
async def _trace_start(ctx) -> None:
    tc = ctx.data.get("tool_call") or {}
    call_id = tc.get("call_id") or uuid.uuid4().hex
    _tool_start_time[call_id] = time.time()
    return None


@on(HookEvent.AFTER_TOOL_CALL, name="eval_trace_end", priority=10)
async def _trace_end(ctx) -> None:
    tc = ctx.data.get("tool_call") or {}
    name = tc.get("name", "")
    call_id = tc.get("call_id") or name
    result = ctx.data.get("result")
    start = _tool_start_time.pop(call_id, time.time())
    elapsed = time.time() - start
    success = getattr(result, "success", True) if result is not None else True
    sql_text = ""
    if name == "execute_sql":
        sql_text = str(_tool_args(ctx).get("sql") or "").strip()
    _tool_trace.append({
        "tool": name,
        "success": bool(success),
        "elapsed_s": round(elapsed, 3),
        "sql": sql_text[:500],  # 截断避免 trace 膨胀
        "message": str(getattr(result, "message", "") or "")[:200] if result else "",
    })
    return None


# ═══════════════════════════════════════════════════════════════════
# 结果集比较（执行 EX 的宽松口径）
# ═══════════════════════════════════════════════════════════════════
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
    """结果集等价判定：允许多选列（附带 id）和行顺序差异。"""
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
    stripped = re.sub(
        r"(?is)\s+(?:LIMIT\s+\d+\s+OFFSET\s+\d+|LIMIT\s+\d+|OFFSET\s+\d+)\s*;?\s*$",
        "", sql,
    ).strip()
    return stripped if stripped != sql.strip() else ""


# ═══════════════════════════════════════════════════════════════════
# 失败模式自动归类（result_mismatch 的根因检测）
# ═══════════════════════════════════════════════════════════════════
def _classify_failure(
    gold_sql: str, pred_sql: str | None,
    gold_rows: list[Any], pred_rows: list[Any],
    gold_result_rows: int, pred_result_rows: int,
    question_tag: dict | None = None,
) -> str:
    """自动检测 result_mismatch 的根因类型。"""
    if pred_sql is None:
        return "no_sql"

    pred_lower = pred_sql.lower()
    gold_lower = gold_sql.lower()

    # 1. 空结果 vs 非空结果 — 粒度偏离（过度聚合）是最常见
    if gold_result_rows > 0 and pred_result_rows == 0:
        # pred 返回空，gold 返回有数据
        if "group by" in pred_lower and "group by" not in gold_lower:
            return "over_aggregation"  # 不该聚合但聚合了
        return "result_set_empty_mismatch"

    if gold_result_rows == 0 and pred_result_rows > 0:
        return "result_set_nonempty_mismatch"

    # 2. WHERE 条件检查
    gold_tables = set(re.findall(r"\bfrom\s+([\w\.]+)", gold_lower)) | set(
        re.findall(r"\bjoin\s+([\w\.]+)", gold_lower)
    )
    pred_tables = set(re.findall(r"\bfrom\s+([\w\.]+)", pred_lower)) | set(
        re.findall(r"\bjoin\s+([\w\.]+)", pred_lower)
    )
    missing_tables = gold_tables - pred_tables - {t.split(".")[-1] for t in pred_tables}
    if missing_tables:
        return f"missing_table:{','.join(sorted(missing_tables))}"

    # 3. GROUP BY 差异
    if "group by" in gold_lower and "group by" not in pred_lower:
        return "missing_group_by"
    if "group by" not in gold_lower and "group by" in pred_lower:
        return "unexpected_group_by"

    # 4. 行数差异很大但不是空集
    if pred_result_rows > 0 and gold_result_rows > 0:
        ratio = min(pred_result_rows, gold_result_rows) / max(pred_result_rows, gold_result_rows)
        if ratio < 0.1:
            return f"row_count_mismatch:{gold_result_rows}vs{pred_result_rows}"

    return "unclassified"


# ═══════════════════════════════════════════════════════════════════
# 业务库连接 + 执行
# ═══════════════════════════════════════════════════════════════════
async def _connect_business(db_name: str, schema: str):
    import asyncpg
    from codegenx.ai_service.agent.db.postgres_manager import _get_pool

    pool = await _get_pool(db_name)

    async def fetch(sql: str, timeout: int = 120) -> tuple[list[Any], float]:
        start = time.time()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                if schema:
                    await conn.execute(f'SET LOCAL search_path TO "{schema}"')
                rows = await asyncio.wait_for(conn.fetch(sql), timeout=timeout)
        elapsed = time.time() - start
        return rows, elapsed

    return fetch


# ═══════════════════════════════════════════════════════════════════
# 单题执行
# ═══════════════════════════════════════════════════════════════════
async def _run_one(
    agent_cls, question: str, db_name: str,
) -> dict[str, Any]:
    from codegenx.ai_service.agent.agents.agent import AgentMessage
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
    from codegenx.ai_service.schema.ai_schema import UserAIRequest

    run_id = uuid.uuid4().hex[:10]
    request = UserAIRequest(
        appId="eval_app", userId="eval_user", message=question,
        sessionId=f"eval-{run_id}", traceId=run_id, requestId=run_id,
        dbName=db_name,
    )
    state = RuntimeSessionState(session_id=f"eval-{run_id}", request=request)
    msg = AgentMessage(
        sender="user", recipient="data_explore",
        request_id=run_id, session_id=state.session_id, task=question,
    )

    _tool_trace.clear()
    started = time.time()
    error = ""
    answer = ""
    try:
        agent = agent_cls()
        reply = await asyncio.wait_for(agent.run(msg, state), timeout=300)
        answer = str(getattr(reply, "result", "") or "")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.time() - started, 1)

    executed_sqls = [t for t in _tool_trace if t["tool"] == "execute_sql"]
    ok_sqls = []
    for c in executed_sqls:
        if c["success"] and c["sql"] and c["sql"] not in ok_sqls:
            ok_sqls.append(c["sql"])

    # 工具路径质量指标
    total_tool_calls = len(_tool_trace)
    explore_calls = sum(1 for t in _tool_trace if "information_schema" in t.get("sql", "") or "information_schema" in t.get("message", ""))
    semantic_calls = sum(1 for t in _tool_trace if t["tool"] in ("search_tables", "describe_table_card", "find_join_path", "search_metrics"))

    return {
        "question": question,
        "executed_sqls": executed_sqls,
        "candidate_sqls": ok_sqls,
        "answer": answer,
        "agent_error": error,
        "elapsed_s": elapsed,
        # 工具路径质量
        "total_tool_calls": total_tool_calls,
        "execute_sql_calls": len(executed_sqls),
        "execute_sql_success": sum(1 for s in executed_sqls if s["success"]),
        "explore_calls": explore_calls,
        "semantic_calls": semantic_calls,
        # VES 计算时需要
        "pred_exec_times": [t["elapsed_s"] for t in executed_sqls if t["success"]],
    }


# ═══════════════════════════════════════════════════════════════════
# 候选匹配 + 金标准执行
# ═══════════════════════════════════════════════════════════════════
async def _match_candidates(
    fetch, gold_rows: list[Any], gold_exec_time: float,
    candidates: list[str],
) -> tuple[str, list[Any], str, float]:
    """逐个执行候选 SQL，返回首个与金标准一致的结果 + 其执行时长 + VES。"""
    first_error = ""
    pred_time = 0.0
    for sql in candidates:
        variants = [sql]
        depaged = _depaginate(sql)
        if depaged:
            variants.append(depaged)
        for variant in variants:
            try:
                rows, exec_time = await fetch(variant)
            except Exception as exc:
                first_error = first_error or f"{type(exc).__name__}: {exc}"
                continue
            pred_time = max(pred_time, exec_time)
            if _same_result(gold_rows, rows):
                ves = round(gold_exec_time / pred_time, 3) if pred_time > 0 else 0.0
                return variant, rows, "", ves
    return "", [], first_error, 0.0


# ═══════════════════════════════════════════════════════════════════
# Agent 类解析
# ═══════════════════════════════════════════════════════════════════
def _resolve_agent(agent_name: str):
    mapping = {
        "data_explore": ("codegenx.ai_service.agent.agents.data_explore_agent", "DateExploreAgent"),
        "plan": ("codegenx.ai_service.agent.agents.plan_agent", "PlanAgent"),
        "leader": ("codegenx.ai_service.agent.agents.leader_agent", "LeaderAgent"),
        "csv_explore": ("codegenx.ai_service.agent.agents.csv_explore_agent", "CSVExploreAgent"),
    }
    if agent_name not in mapping:
        raise SystemExit(f"未知 agent: {agent_name}，可用: {list(mapping.keys())}")
    mod_name, cls_name = mapping[agent_name]
    import importlib
    mod = importlib.import_module(mod_name)
    return getattr(mod, cls_name)


# ═══════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════
async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--only", default="", help="只跑指定下标，如 0,12,20")
    parser.add_argument("--db", default="business", help="业务库 dbName（config.json databases 键）")
    parser.add_argument("--agent", default="data_explore", help="agent 名称（data_explore/plan/leader/csv_explore）")
    parser.add_argument("--baseline", action="store_true", help="将本次结果固化为 baseline.json")
    args = parser.parse_args()

    questions = json.loads((EVAL_DIR / "question.json").read_text(encoding="utf-8"))
    if args.only:
        idx = [int(x) for x in args.only.split(",") if x.strip()]
        questions = [questions[i] for i in idx]
    elif args.limit:
        questions = questions[: args.limit]

    from codegenx.ai_service.system_app import init_app, start_app, stop_app
    from codegenx.ai_service.agent.db import postgres_manager
    from shared.constants import PUBLIC_SCHEMAS

    # schema 取 constants.PUBLIC_SCHEMAS 里 datasource 匹配的项（格式 "datasource.schema"）
    schema_name = ""
    for pub in PUBLIC_SCHEMAS:
        ds, _, sch = pub.partition(".")
        if ds == args.db:
            schema_name = sch
            break
    agent_cls = _resolve_agent(args.agent)

    app = init_app()
    await start_app(app)
    fetch = await _connect_business(args.db, schema_name)

    results: list[dict[str, Any]] = []
    passed = 0
    try:
        for i, item in enumerate(questions, 1):
            question, gold_sql = item["question"], item["sql"]
            print(f"\n[{i}/{len(questions)}] {question}", flush=True)

            # 金标准执行（带耗时）
            gold_rows: list[Any] = []
            gold_exec_time = 0.0
            gold_error = ""
            try:
                gold_rows, gold_exec_time = await fetch(gold_sql)
            except Exception as exc:
                gold_error = f"{type(exc).__name__}: {exc}"

            record = await _run_one(agent_cls, question, args.db)
            candidates = record["candidate_sqls"]

            matched_sql, matched_rows = "", []
            pred_error = ""
            ves = 0.0
            failure_type = ""

            if gold_error:
                match, judge = None, "gold_sql_failed"
            elif not candidates:
                match, judge = False, "no_sql"
                failure_type = "no_sql"
            else:
                matched_sql, matched_rows, pred_error, ves = await _match_candidates(
                    fetch, gold_rows, gold_exec_time, candidates,
                )
                if matched_sql:
                    match, judge = True, "match"
                elif pred_error:
                    match, judge = False, "pred_sql_failed"
                    failure_type = "pred_sql_failed"
                else:
                    match, judge = False, "result_mismatch"
                    failure_type = _classify_failure(
                        gold_sql, matched_sql or candidates[-1] if candidates else None,
                        gold_rows, matched_rows,
                        len(gold_rows), len(matched_rows),
                        item,
                    )

            if match:
                passed += 1

            record.update({
                "id": item.get("id", f"q_{i-1}"),
                "gold_sql": gold_sql,
                "difficulty": item.get("difficulty", ""),
                "ops": item.get("ops", []),
                "grain": item.get("grain", ""),
                "domain": item.get("domain", ""),
                "multi_hop": item.get("multi_hop", 0),
                "gold_exec_time_s": round(gold_exec_time, 3),
                "gold_error": gold_error,
                "gold_result_rows": len(gold_rows),
                "matched_sql": matched_sql,
                "pred_result_rows": len(matched_rows),
                "pred_error": pred_error,
                "ves": ves,
                "match": match,
                "judge": judge,
                "failure_type": failure_type,
            })
            results.append(record)

            # 进度输出
            print(
                f"  -> {judge}{' ✅' if match else ' ❌'} | agent={record['elapsed_s']}s | "
                f"tool_calls={record['total_tool_calls']} | sql_ok={record['execute_sql_success']}/{record['execute_sql_calls']}",
                flush=True,
            )
            if record["agent_error"]:
                print(f"  agent_error: {record['agent_error']}", flush=True)
            if pred_error:
                print(f"  pred_error: {pred_error}", flush=True)

        # ── 汇总计算 ──
        scored = [r for r in results if r["judge"] != "gold_sql_failed"]
        accuracy = (passed / len(scored) * 100) if scored else 0.0

        # Valid SQL Rate（agent 执行过的所有 SQL 中成功执行的比例）
        total_attempted_sql = sum(r["execute_sql_calls"] for r in results)
        total_valid_sql = sum(r["execute_sql_success"] for r in results)
        valid_sql_rate = (total_valid_sql / total_attempted_sql * 100) if total_attempted_sql else 0.0

        # VES 汇总（只看 matched 的 case）
        ves_list = [r["ves"] for r in results if r["judge"] == "match" and r["ves"] > 0]
        avg_ves = round(sum(ves_list) / len(ves_list), 3) if ves_list else 0.0

        # 失败模式分布
        failure_dist = Counter(
            r["failure_type"] for r in results
            if r["judge"] in ("result_mismatch", "no_sql", "pred_sql_failed")
        )

        # 工具路径质量汇总
        avg_tool_calls = round(
            sum(r["total_tool_calls"] for r in results) / len(results), 1
        ) if results else 0
        avg_explore_calls = round(
            sum(r["explore_calls"] for r in results) / len(results), 2
        ) if results else 0

        summary = {
            "db": args.db,
            "schema": schema_name,
            "agent": args.agent,
            "total": len(results),
            "scored": len(scored),
            "passed": passed,
            "accuracy_pct": round(accuracy, 2),
            # 新增指标
            "valid_sql_rate_pct": round(valid_sql_rate, 2),
            "avg_ves": avg_ves,
            "avg_elapsed_s": round(
                sum(r["elapsed_s"] for r in results) / len(results), 1
            ) if results else 0,
            "avg_tool_calls": avg_tool_calls,
            "avg_explore_calls": avg_explore_calls,
            # 失败分布
            "gold_sql_failed": sum(1 for r in results if r["judge"] == "gold_sql_failed"),
            "no_sql": sum(1 for r in results if r["judge"] == "no_sql"),
            "pred_sql_failed": sum(1 for r in results if r["judge"] == "pred_sql_failed"),
            "result_mismatch": sum(1 for r in results if r["judge"] == "result_mismatch"),
            "failure_distribution": dict(failure_dist),
            # 维度分组 EX
            "accuracy_by_difficulty": {},
            "accuracy_by_grain": {},
            "accuracy_by_ops": {},
            "accuracy_by_multi_hop": {},
        }

        # 各维度分组 EX
        for dim_key, dim_name in [
            ("difficulty", "accuracy_by_difficulty"),
            ("grain", "accuracy_by_grain"),
            ("multi_hop", "accuracy_by_multi_hop"),
        ]:
            groups: dict[Any, tuple[int, int]] = {}  # key -> (passed, total)
            for r in scored:
                if r["judge"] == "gold_sql_failed":
                    continue
                key = r.get(dim_key, "unknown")
                p, t = groups.get(key, (0, 0))
                t += 1
                if r["match"]:
                    p += 1
                groups[key] = (p, t)
            summary[dim_name] = {
                str(k): {"passed": v[0], "total": v[1], "ex": round(v[0] / v[1] * 100, 2)}
                for k, v in groups.items()
            }

        # ops 是 list，单独处理
        ops_groups: dict[str, tuple[int, int]] = {}
        for r in scored:
            if r["judge"] == "gold_sql_failed":
                continue
            for op in r.get("ops", []):
                p, t = ops_groups.get(op, (0, 0))
                t += 1
                if r["match"]:
                    p += 1
                ops_groups[op] = (p, t)
        summary["accuracy_by_ops"] = {
            k: {"passed": v[0], "total": v[1], "ex": round(v[0] / v[1] * 100, 2)}
            for k, v in sorted(ops_groups.items(), key=lambda x: -x[1][1])
        }

        payload = {"summary": summary, "results": results}
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 固化 baseline
        if args.baseline:
            BASELINE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n✅ 已固化 baseline: {BASELINE_PATH}")

        print("\n===== 评测结果 =====")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"明细: {out}")
    finally:
        await postgres_manager.close_pools()
        await stop_app(app)


if __name__ == "__main__":
    asyncio.run(main())
