import re


# 禁止的 SQL 关键字（写操作/DDL/DCL）
_FORBIDDEN_SQL = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|RENAME|'
    r'REPLACE|LOAD|GRANT|REVOKE|SET\s+@|LOCK|UNLOCK|FLUSH|'
    r'CALL|EXECUTE|EXEC|PREPARE)\b',
    re.IGNORECASE,
)
# 白名单 SQL 前缀
_ALLOWED_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH")


def check_sql_safety(sql: str) -> None:
    """检查 SQL 是否安全（只读），不安全则抛出 ValueError。"""
    stripped = sql.strip().rstrip(";")
    upper = stripped.upper()

    # 检查是否以白名单前缀开头
    if not any(upper.startswith(p) for p in _ALLOWED_PREFIXES):
        raise ValueError(f"SQL 不允许执行，仅支持: {', '.join(_ALLOWED_PREFIXES)}")

    # 检查是否包含禁止关键字
    if _FORBIDDEN_SQL.search(stripped):
        raise ValueError("SQL 包含禁止的写操作/DDL 关键字")


# ── 格式化辅助 ────────────────────────────────────────────

def format_rows(rows: list[dict], max_cell_len: int = 200) -> str:
    """将查询结果格式化为对齐文本表格。"""
    if not rows:
        return "(无数据)"
    columns = list(rows[0].keys())
    # 计算列宽
    widths = [len(c) for c in columns]
    for row in rows:
        for i, col in enumerate(columns):
            val = str(row.get(col, ""))
            widths[i] = max(widths[i], min(len(val), max_cell_len))
    # 表头
    header = " | ".join(c.ljust(w) for c, w in zip(columns, widths))
    sep = "-+-".join("-" * w for w in widths)
    lines = [header, sep]
    # 数据行
    for row in rows[:50]:  # 最多 50 行
        cells = []
        for i, col in enumerate(columns):
            val = str(row.get(col, ""))
            if len(val) > max_cell_len:
                val = val[:max_cell_len] + "..."
            cells.append(val.ljust(widths[i]))
        lines.append(" | ".join(cells))
    if len(rows) > 50:
        lines.append(f"... 还有 {len(rows) - 50} 行")
    return "\n".join(lines)