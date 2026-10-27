"""评测报告生成器：从 eval JSON 生成 5 板块 Markdown 报告。

板块：
  1. 总览摘要 — EX / Valid Rate / VES / 耗时 + baseline delta
  2. 维度分解 — 按 difficulty / grain / ops / multi_hop 分组 EX
  3. 失败模式归类 — result_mismatch 的自动根因检测
  4. 回归对比 — 逐题 vs baseline 的 match 变化（新增通过 / 新增失败 / 持续失败）
  5. 轨迹摘要 — 工具调用统计

用法：
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\analyze_report.py
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\analyze_report.py --last   # 最近一次
  .venv\\Scripts\\python.exe ..\\data\\evaluate_dvdrental\\analyze_report.py --file eval_xxx.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
BASELINE_PATH = EVAL_DIR / "baseline.json"
BACKEND_DIR = EVAL_DIR.parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR / "src"))
os.chdir(BACKEND_DIR)


def _load_eval(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_latest() -> Path:
    files = sorted(RESULTS_DIR.glob("eval_*.json"))
    if not files:
        raise SystemExit(f"results/ 目录下没有找到 eval_*.json")
    return files[-1]


def _section(title: str) -> str:
    return f"\n## {title}\n"


def _fmt_val(v, fmt, unit="") -> str:
    """格式化数值，None 返回 —。"""
    if v is None:
        return "—"
    return f"{fmt % v}{unit}"


def _delta(val, base, fmt="%.2f", unit="") -> str:
    if base is None:
        return f"— (baseline 无)"
    d = val - base
    sign = "+" if d >= 0 else ""
    emoji = "🟢" if d > 0 else ("🔴" if d < 0 else "⚪")
    return f"{emoji} {fmt % val}{unit} ({sign}{fmt % d}{unit} vs baseline)"


def build_report(eval_path: Path, baseline: dict | None) -> str:
    data = _load_eval(eval_path)
    s = data["summary"]
    results = data["results"]
    lines: list[str] = []

    # ── Header ──
    lines.append(f"# 评测报告")
    lines.append(f"\n> 文件: `{eval_path.name}` | 生成于: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"> Agent: **{s.get('agent', 'unknown')}** | DB: **{s.get('db', '?')}.{s.get('schema', '?')}**")
    if baseline is None:
        lines.append("> ⚠️ 无 baseline，本次为首次评测")
    else:
        lines.append(f"> Baseline: `{Path(baseline['_eval_path']).name}` ({baseline['summary']['agent']})")

    # ═══════════════════════════════════════════════════════════════
    # 1. 总览摘要
    # ═══════════════════════════════════════════════════════════════
    lines.append(_section("1. 总览摘要"))
    bs = baseline.get("summary") if baseline else None

    lines.append("| 指标 | 本次 | Baseline | Delta |")
    lines.append("|---|---|---|---|")
    ex_cur = s["accuracy_pct"]
    ex_base = bs["accuracy_pct"] if bs else None
    lines.append(f"| **Execution Accuracy (EX)** | **{ex_cur:.2f}%** | {_fmt_val(ex_base, '%.2f', '%')} | {_delta(ex_cur, ex_base, '%.2f', '%')} |")

    vr_cur = s.get("valid_sql_rate_pct", 0)
    vr_base = bs.get("valid_sql_rate_pct") if bs else None
    lines.append(f"| Valid SQL Rate | {vr_cur:.2f}% | {_fmt_val(vr_base, '%.2f', '%')} | {_delta(vr_cur, vr_base, '%.2f', '%')} |")

    ves_cur = s.get("avg_ves", 0)
    ves_base = bs.get("avg_ves") if bs else None
    lines.append(f"| Avg VES（效率比） | {ves_cur} | {_fmt_val(ves_base, '%.3f')} | {_delta(ves_cur, ves_base, '%.3f')} |")

    t_cur = s["avg_elapsed_s"]
    t_base = bs.get("avg_elapsed_s") if bs else None
    lines.append(f"| 平均耗时 | {t_cur}s | {_fmt_val(t_base, '%.1f', 's')} | {_delta(t_cur, t_base, '%.1f', 's')} |")

    tc_cur = s.get("avg_tool_calls", 0)
    tc_base = bs.get("avg_tool_calls") if bs else None
    lines.append(f"| 平均工具调用数 | {tc_cur} | {_fmt_val(tc_base, '%.1f')} | {_delta(tc_cur, tc_base, '%.1f')} |")

    lines.append(f"\n**题量**: {s['total']}（有效 {s['scored']} / 通过 {s['passed']}）")

    # 失败类型分布速览
    lines.append("\n**失败类型分布**:")
    lines.append("```")
    lines.append(f"  match:           {sum(1 for r in results if r['judge']=='match'):>3}  ✅")
    lines.append(f"  result_mismatch: {s.get('result_mismatch',0):>3}")
    lines.append(f"  no_sql:          {s.get('no_sql',0):>3}")
    lines.append(f"  pred_sql_failed: {s.get('pred_sql_failed',0):>3}")
    lines.append(f"  gold_sql_failed: {s.get('gold_sql_failed',0):>3}")
    lines.append("```")

    # ═══════════════════════════════════════════════════════════════
    # 2. 维度分解
    # ═══════════════════════════════════════════════════════════════
    lines.append(_section("2. 维度分解"))

    for dim, summary_key, label in [
        ("difficulty", "accuracy_by_difficulty", "难度"),
        ("grain", "accuracy_by_grain", "输出粒度"),
        ("multi_hop", "accuracy_by_multi_hop", "JOIN 跳数"),
    ]:
        groups = s.get(summary_key, {})
        if not groups:
            continue
        lines.append(f"\n### 按{label}分组 EX")
        lines.append("| " + label + " | EX | 通过/总数 |")
        lines.append("|---|---|---|")
        for k, v in sorted(groups.items(), key=lambda x: str(x[0])):
            lines.append(f"| {k} | {v['ex']:.1f}% | {v['passed']}/{v['total']} |")

    # ops 是多标签，单独处理
    ops_groups = s.get("accuracy_by_ops", {})
    if ops_groups:
        lines.append(f"\n### 按 SQL 操作类型 (ops) 分组 EX")
        lines.append("| ops | EX | 通过/总数 |")
        lines.append("|---|---|---|")
        for op, v in sorted(ops_groups.items(), key=lambda x: -x[1]["total"]):
            lines.append(f"| `{op}` | {v['ex']:.1f}% | {v['passed']}/{v['total']} |")

    # ═══════════════════════════════════════════════════════════════
    # 3. 失败模式归类
    # ═══════════════════════════════════════════════════════════════
    lines.append(_section("3. 失败模式归类"))

    failure_dist = s.get("failure_distribution", {})
    if failure_dist:
        lines.append("\n**失败根因分布**:")
        lines.append("```")
        for ft, cnt in sorted(failure_dist.items(), key=lambda x: -x[1]):
            lines.append(f"  {ft:<35s} {cnt:>3}")
        lines.append("```")

    # 逐题失败详情
    fail_results = [r for r in results if r["judge"] != "match"]
    if fail_results:
        lines.append("\n**逐题失败详情**:")
        lines.append("| ID | 问题 | 失败类型 | 耗时 | 工具数 | agent_error |")
        lines.append("|---|---|---|---|---|---|")
        for r in fail_results:
            q = r["question"][:40] + ("…" if len(r["question"]) > 40 else "")
            fe = r.get("failure_type") or r["judge"]
            ae = "⚠️" if r.get("agent_error") else ""
            lines.append(f"| {r.get('id','?')} | {q} | `{fe}` | {r['elapsed_s']}s | {r.get('total_tool_calls','?')} {ae} |")

    # ═══════════════════════════════════════════════════════════════
    # 4. 回归对比（逐题 vs baseline）
    # ═══════════════════════════════════════════════════════════════
    lines.append(_section("4. 回归对比（逐题）"))

    if baseline is None:
        lines.append("⚠️ 无 baseline，跳过回归对比。先用 `run_eval.py --baseline` 固化基线。")
    else:
        br = {r.get("id"): r for r in baseline["results"]}

        improved, regressed, still_fail = [], [], []
        for r in results:
            rid = r.get("id")
            b = br.get(rid)
            if b is None:
                continue
            cur_match = r["judge"] == "match"
            base_match = b["judge"] == "match"
            if cur_match and not base_match:
                improved.append((r, b))
            elif not cur_match and base_match:
                regressed.append((r, b))
            elif not cur_match and not base_match:
                still_fail.append((r, b))

        lines.append(f"\n| 类别 | 数量 |")
        lines.append("|---|---|")
        lines.append(f"| 🟢 新增通过 | **{len(improved)}** |")
        lines.append(f"| 🔴 新增失败（回归） | **{len(regressed)}** |")
        lines.append(f"| ⚪ 持续失败 | {len(still_fail)} |")

        if regressed:
            lines.append("\n**⚠️ 回归题（务必检查）**:")
            for r, b in regressed:
                lines.append(f"  - **{r.get('id')}** {r['question'][:50]} | baseline=`{b['judge']}` → 当前=`{r['judge']}` ({r.get('failure_type','')})")

        if improved:
            lines.append(f"\n**✅ 改进题**:")
            for r, b in improved:
                lines.append(f"  - **{r.get('id')}** {r['question'][:50]} | baseline=`{b['judge']}` → current=`{r['judge']}`")

    # ═══════════════════════════════════════════════════════════════
    # 5. 轨迹摘要
    # ═══════════════════════════════════════════════════════════════
    lines.append(_section("5. 轨迹摘要"))

    # 最慢 5 题
    sorted_by_time = sorted(results, key=lambda r: r["elapsed_s"], reverse=True)
    lines.append("\n**最慢 5 题**:")
    for r in sorted_by_time[:5]:
        lines.append(f"  - {r.get('id','?')} {r['elapsed_s']}s | tools={r.get('total_tool_calls','?')} | sql_calls={r.get('execute_sql_calls','?')} | {r['question'][:40]}")

    # 最多工具调用 5 题（老数据没有 total_tool_calls 时跳过）
    if results and "total_tool_calls" in results[0]:
        sorted_by_tools = sorted(results, key=lambda r: r["total_tool_calls"], reverse=True)
        lines.append("\n**工具调用最多 5 题**:")
        for r in sorted_by_tools[:5]:
            lines.append(f"  - {r.get('id','?')} tools={r['total_tool_calls']} | sql_calls={r.get('execute_sql_calls','?')} | explore={r.get('explore_calls','?')} | {r['question'][:40]}")

    # 使用了 information_schema 探查的题
    explore_used = [r for r in results if r.get("explore_calls", 0) > 0]
    if explore_used:
        lines.append(f"\n**使用 information_schema 探查的题** ({len(explore_used)} 题，这些题 agent 没有走语义层):")
        for r in explore_used:
            lines.append(f"  - {r.get('id','?')} explore_calls={r['explore_calls']} | judge={r['judge']} | {r['question'][:40]}")

    # 使用了语义层工具的题
    if "semantic_calls" in results[0] if results else False:
        semantic_used = [r for r in results if r.get("semantic_calls", 0) > 0]
        lines.append(f"\n**使用语义层工具的题**: {len(semantic_used)}/{len(results)}")

    lines.append("\n---")
    lines.append(f"*报告由 analyze_report.py 自动生成*")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="", help="指定 eval JSON 文件名（results/ 下）")
    parser.add_argument("--last", action="store_true", help="使用最近一次 eval")
    parser.add_argument("--baseline", default=str(BASELINE_PATH), help="baseline.json 路径")
    parser.add_argument("--output", default="", help="报告输出路径（默认同目录 report_<ts>.md）")
    args = parser.parse_args()

    if args.file:
        eval_path = RESULTS_DIR / args.file
    elif args.last:
        eval_path = _find_latest()
    else:
        eval_path = _find_latest()

    if not eval_path.exists():
        raise SystemExit(f"文件不存在: {eval_path}")

    baseline = None
    if args.baseline and Path(args.baseline).exists():
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        baseline["_eval_path"] = str(Path(args.baseline))

    report = build_report(eval_path, baseline)

    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = eval_path.parent / f"report_{ts}.md"

    out_path.write_text(report, encoding="utf-8")
    print(f"✅ 报告已生成: {out_path}")
    print(report[:2000] + ("\n...(截断)" if len(report) > 2000 else ""))


if __name__ == "__main__":
    main()
