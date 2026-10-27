"""
语义层卡片渲染：把 store 返回的行（dict）格式化成给 LLM 的紧凑文本。

渲染原则：
  - 一屏可读，不铺全量字段；draft 明确标注，避免模型把未审核信息当事实；
  - 行数、置信度等“可信度信号”必须随卡片返回。
"""
from __future__ import annotations

from typing import Any


def _tags(value: Any) -> str:
    return ",".join(value or [])


def _pk(value: Any) -> str:
    return ",".join(value or [])


def fmt_table_card(row: dict, score: float | None = None) -> str:
    """表卡（检索结果行）。"""
    name = f"{row.get('datasource', '')}.{row.get('schema_name', '')}.{row.get('name', '')}"
    head = f"[{row.get('status', 'draft')}] {name}"
    if score is not None:
        head += f"  score={score:.2f}"
    lines = [head]
    desc = (row.get("business_desc") or row.get("comment") or "").strip()
    if desc:
        lines.append(f"  含义: {desc}")
    if row.get("grain"):
        lines.append(f"  粒度: {row['grain']}")
    if row.get("primary_key"):
        lines.append(f"  主键: {_pk(row['primary_key'])}")
    if row.get("row_estimate"):
        lines.append(f"  行数估算: {row['row_estimate']}")
    if row.get("tags"):
        lines.append(f"  标签: {_tags(row['tags'])}")
    return "\n".join(lines)


def fmt_table_detail(table: dict, columns: list[dict]) -> str:
    """表详情：表卡 + 列清单（describe_table 输出）。"""
    lines = [fmt_table_card(table)]
    if columns:
        lines.append("  列:")
        for col in columns:
            parts = [f"    - {col['name']} {col.get('data_type', '')}"]
            if not col.get("nullable", True):
                parts.append("NOT NULL")
            if col.get("comment"):
                parts.append(f"// {col['comment']}")
            if col.get("business_desc"):
                parts.append(f"[{col['business_desc']}]")
            if col.get("synonyms"):
                parts.append(f"同义词: {_tags(col['synonyms'])}")
            if col.get("is_pii"):
                parts.append("PII")
            if col.get("cardinality"):
                parts.append(f"基数≈{col['cardinality']}")
            if col.get("sample_values"):
                parts.append(f"样例: {col['sample_values']}")
            lines.append(" ".join(parts))
    else:
        lines.append("  （列卡未采集）")
    return "\n".join(lines)


def fmt_metric_card(row: dict, score: float | None = None) -> str:
    head = f"[{row.get('status', 'draft')}] {row.get('name', '')}"
    if score is not None:
        head += f"  score={score:.2f}"
    lines = [head]
    if row.get("synonyms"):
        lines.append(f"  同义词: {_tags(row['synonyms'])}")
    if row.get("definition"):
        lines.append(f"  口径: {row['definition']}")
    if row.get("expression"):
        lines.append(f"  表达式: {row['expression']}")
    if row.get("default_filters"):
        lines.append(f"  默认过滤: {row['default_filters']}")
    if row.get("grain"):
        lines.append(f"  粒度: {row['grain']}")
    if row.get("dimensions"):
        lines.append(f"  可下钻维度: {_tags(row['dimensions'])}")
    return "\n".join(lines)


def fmt_verified_query(row: dict, score: float | None = None) -> str:
    lines = []
    head = "已验证示例"
    if score is not None:
        head += f"  score={score:.2f}"
    lines.append(head)
    lines.append(f"  Q: {row.get('question', '')}")
    lines.append(f"  SQL: {row.get('sql', '')}")
    if row.get("result_summary"):
        lines.append(f"  结果: {row['result_summary']}")
    return "\n".join(lines)


def fmt_join_path(rows: list[dict]) -> str:
    """JOIN 路径：steps 是 jsonb 数组，每步含两侧表名/列/关系类型/置信度。"""
    if not rows:
        return "（未找到已登记的表间关系；请先用 FK 或人工声明补充 meta_relation）"
    lines = []
    for idx, row in enumerate(rows, 1):
        lines.append(f"路径{idx}（{row.get('hops', '?')} 跳）:")
        for step in row.get("steps") or []:
            lines.append(
                "  {fs}.{ft}.{fc} -> {ts}.{tt}.{tc}  [{rel}, 置信度 {conf:.2f}{verified}]".format(
                    fs=step.get("from_schema", ""),
                    ft=step.get("from_table", ""),
                    fc=",".join(step.get("from_columns") or []),
                    ts=step.get("to_schema", ""),
                    tt=step.get("to_table", ""),
                    tc=",".join(step.get("to_columns") or []),
                    rel=step.get("rel_type", ""),
                    conf=float(step.get("confidence") or 0.0),
                    verified=", 已确认" if step.get("verified") else "",
                )
            )
    return "\n".join(lines)
