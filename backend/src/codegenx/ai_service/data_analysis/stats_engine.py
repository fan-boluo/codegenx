"""
StatsEngine — 统计计算公共逻辑

为 describe_table_stats / describe_csv_stats 提供：
- 数值列统计（均值/方差/分位数）
- 类别列频率统计
- 相关性矩阵
- 统一采样策略
- 格式化输出

纯计算，不涉及 I/O。
"""

from typing import Any

import numpy as np


# 采样阈值：(max_rows, sample_pct)
_SAMPLE_THRESHOLDS = [
    (100_000, 100),   # < 10万 → 全量
    (500_000, 10),    # 10万-50万 → 10%
    (2_000_000, 5),   # 50万-200万 → 5%
    (float("inf"), 1),  # > 200万 → 1%
]

# 输出截断
MAX_CATEGORY_TOP = 10
MAX_CORRELATION_PAIRS = 15
MAX_DISTINCT_VALUES = 10000


def _resolve_sample_pct(total_rows: int, requested_pct: int) -> tuple[int, bool]:
    """根据行数自动决定采样率。返回 (effective_pct, is_sampled)。"""
    if total_rows <= 0:
        return 100, False
    effective = requested_pct
    for threshold, pct in _SAMPLE_THRESHOLDS:
        if total_rows <= threshold:
            effective = min(effective, pct)
            break
    return effective, effective < 100


def _sample_indices(total_rows: int, sample_pct: int) -> np.ndarray:
    """生成等距采样索引。"""
    if sample_pct >= 100:
        return np.arange(total_rows)
    sample_size = max(int(total_rows * sample_pct / 100), 1000)
    step = total_rows / sample_size
    return np.unique(np.floor(np.arange(0, total_rows, step)).astype(int))


# ── 数值统计 ──────────────────────────────────────────────

def compute_numeric_stats(values: np.ndarray, sample_pct: int = 100) -> dict[str, Any]:
    """对一维数值数组计算描述性统计。
    values 中 NaN 会被自动跳过。
    sample_pct < 100 时标注估算值。
    """
    vals = values[~np.isnan(values)]
    if len(vals) == 0:
        return {"count": 0, "missing": len(values), "note": "无有效数值"}

    is_sample = sample_pct < 100
    prefix = "~" if is_sample else ""
    return {
        f"{prefix}count": int(len(vals)),
        f"{prefix}missing": int(len(values) - len(vals)),
        f"{prefix}missing_pct": round((len(values) - len(vals)) / max(len(values), 1) * 100, 1),
        f"{prefix}mean": round(float(np.mean(vals)), 4),
        f"{prefix}std": round(float(np.std(vals, ddof=1)), 4) if len(vals) > 1 else 0,
        f"{prefix}min": round(float(np.min(vals)), 4),
        f"{prefix}p25": round(float(np.percentile(vals, 25)), 4),
        f"{prefix}p50": round(float(np.percentile(vals, 50)), 4),
        f"{prefix}p75": round(float(np.percentile(vals, 75)), 4),
        f"{prefix}max": round(float(np.max(vals)), 4),
        "estimated": is_sample,
    }


# ── 类别统计 ──────────────────────────────────────────────

def compute_categorical_stats(values: np.ndarray, max_distinct: int = MAX_DISTINCT_VALUES) -> dict[str, Any]:
    """对一维类别数组做频率统计。
    去重值超过 max_distinct 时截断并标注。
    """
    total = len(values)
    is_missing = _safe_isna(values)
    missing = int(np.sum(is_missing))
    valid = values[~is_missing] if missing > 0 else values

    unique_vals, counts = np.unique(valid, return_counts=True)
    distinct = len(unique_vals)

    truncated = distinct > max_distinct
    if truncated:
        # 只保留 frequency 最高的前 max_distinct
        top_idx = np.argsort(counts)[::-1][:max_distinct]
        unique_vals = unique_vals[top_idx]
        counts = counts[top_idx]

    # 排序：频率降序
    sorted_idx = np.argsort(counts)[::-1]
    top_n = min(MAX_CATEGORY_TOP, len(unique_vals))
    top_items = [
        {"value": str(unique_vals[i]), "count": int(counts[i]),
         "pct": round(float(counts[i]) / max(total, 1) * 100, 1)}
        for i in sorted_idx[:top_n]
    ]

    return {
        "distinct": int(distinct),
        "missing": missing,
        "missing_pct": round(missing / max(total, 1) * 100, 1),
        "top_values": top_items,
        "truncated": truncated,
    }


# ── 相关性 ────────────────────────────────────────────────

def compute_correlation(matrix: np.ndarray, col_names: list[str], max_pairs: int = MAX_CORRELATION_PAIRS) -> dict[str, Any]:
    """计算数值列之间的 Pearson 相关系数矩阵。
    返回绝对值最大的 N 对。
    """
    n_cols = matrix.shape[1]
    if n_cols < 2:
        return {"pairs": [], "note": "数值列不足 2 列，无法计算相关性"}

    corr = np.corrcoef(matrix, rowvar=False)
    pairs = []
    for i in range(n_cols):
        for j in range(i + 1, n_cols):
            pairs.append({
                "col_a": col_names[i],
                "col_b": col_names[j],
                "correlation": round(float(corr[i, j]), 4),
            })

    pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)
    return {"pairs": pairs[:max_pairs]}


# ── 列类型推断 ────────────────────────────────────────────

def _safe_isna(values: np.ndarray):
    """跨平台的缺失值检查（不依赖 pandas）。"""
    if values.dtype.kind in ('f', 'c'):  # float/complex
        return np.isnan(values)
    if values.dtype.kind in ('U', 'S', 'O'):  # string/object
        # 检查 None 和空字符串
        is_none = np.array([v is None for v in values])
        is_empty = np.array([str(v).strip() == '' for v in values])
        return is_none | is_empty
    return np.zeros(len(values), dtype=bool)


def _try_to_datetime(values: np.ndarray) -> bool:
    """判断 values 是否可被解析为时间。不依赖 pandas。"""
    try:
        from datetime import datetime
        # 采样 5 个值尝试解析
        sample = values[~_safe_isna(values)][:5]
        for v in sample:
            s = str(v).strip()
            # 尝试常见的日期时间格式
            formats = [
                "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d",
                "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y",
            ]
            parsed = False
            for fmt in formats:
                try:
                    datetime.strptime(s, fmt)
                    parsed = True
                    break
                except ValueError:
                    continue
            if not parsed:
                return False
        return len(sample) > 0
    except Exception:
        return False


def _infer_dtype(values: np.ndarray) -> str:
    """推断单列的数据类型（不依赖 pandas）。"""
    vals = values[~_safe_isna(values)]
    if len(vals) == 0:
        return "unknown"
    # 尝试转数值
    try:
        converted = np.asarray(vals, dtype=float)
        if np.all(converted == np.floor(converted)):
            return "integer"
        return "numeric"
    except (ValueError, TypeError):
        pass
    # 检查是否像时间
    if _try_to_datetime(vals):
        return "datetime"
    return "categorical"


def infer_column_types(col_names: list[str], columns: list[np.ndarray]) -> dict[str, str]:
    """批量推断所有列的类型。"""
    return {name: _infer_dtype(col) for name, col in zip(col_names, columns)}


# ── 格式化输出 ────────────────────────────────────────────

def format_numeric_stats(col_name: str, stats: dict) -> str:
    """单列数值统计 → 字符串。"""
    if stats.get("count", 0) == 0:
        return f"  {col_name}: 无有效数据"
    est = " ≈" if stats.get("estimated") else ""
    lines = [
        f"  {col_name}:",
        f"    count={stats.get('~count', stats.get('count'))}, "
        f"missing={stats.get('~missing', stats.get('missing'))}"
        f" ({stats.get('~missing_pct', stats.get('missing_pct'))}%)",
        f"    mean{est}={stats.get('~mean', stats.get('mean'))}, "
        f"std{est}={stats.get('~std', stats.get('std'))}",
        f"    min{est}={stats.get('~min', stats.get('min'))}, "
        f"p25{est}={stats.get('~p25', stats.get('p25'))}, "
        f"p50{est}={stats.get('~p50', stats.get('p50'))}, "
        f"p75{est}={stats.get('~p75', stats.get('p75'))}, "
        f"max{est}={stats.get('~max', stats.get('max'))}",
    ]
    return "\n".join(lines)


def format_categorical_stats(col_name: str, stats: dict) -> str:
    """单列类别统计 → 字符串。"""
    truncated = " [去重统计已截断]" if stats.get("truncated") else ""
    lines = [
        f"  {col_name}: distinct={stats['distinct']}{truncated}, "
        f"missing={stats['missing']} ({stats['missing_pct']}%)",
    ]
    for item in stats.get("top_values", []):
        lines.append(f"    {item['value']}: {item['count']} ({item['pct']}%)")
    return "\n".join(lines)


def format_correlation(corr: dict) -> str:
    """相关性结果 → 字符串。"""
    if corr.get("note"):
        return f"  {corr['note']}"
    lines = ["  列间相关性 (Pearson, |r| 降序):"]
    for p in corr["pairs"]:
        lines.append(f"    {p['col_a']} × {p['col_b']}: r={p['correlation']}")
    return "\n".join(lines)


# 尝试 import pandas（可选依赖，CSV 工具需要）
try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    pd = None  # type: ignore
    _HAS_PANDAS = False
