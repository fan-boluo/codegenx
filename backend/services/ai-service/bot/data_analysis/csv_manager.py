"""
CsvManager — CSV 文件读取管理

职责：
- 安全路径校验
- 自动编码检测
- 采样读取
- 大文件保护
- 基本文件信息提取
"""

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from shared.config.log_config import log

# 可选依赖
try:
    import chardet
    _HAS_CHARDET = True
except ImportError:
    chardet = None  # type: ignore
    _HAS_CHARDET = False

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    pd = None  # type: ignore
    _HAS_PANDAS = False


# ── 配置 ──────────────────────────────────────────────────

MAX_FILE_SIZE_MB = 500
LARGE_CSV_ROWS = 500_000     # 50 万行 → 采样
VERY_LARGE_CSV_ROWS = 2_000_000  # 200 万行 → 更小采样

DEFAULT_ENCODINGS = ["utf-8", "gbk", "gb2312", "gb18030", "latin-1"]


@dataclass
class CsvConfig:
    data_dir: str = "/data/csv_files"
    max_file_size_mb: int = MAX_FILE_SIZE_MB


class CsvManager:
    """CSV 文件读取管理。"""

    def __init__(self, config: CsvConfig | None = None):
        self.config = config or CsvConfig()

    def _check_safety(self, file_path: str) -> Path:
        """路径安全性检查。返回 resolved Path。"""
        p = Path(file_path).resolve()
        data_dir = Path(self.config.data_dir).resolve()

        # 必须在 data_dir 下
        try:
            p.relative_to(data_dir)
        except ValueError:
            raise ValueError(f"文件不在允许的目录中: {data_dir}")

        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        if not p.is_file():
            raise ValueError(f"不是文件: {file_path}")

        # 大小检查
        size_mb = p.stat().st_size / (1024 * 1024)
        if size_mb > self.config.max_file_size_mb:
            raise ValueError(
                f"文件过大 ({size_mb:.1f}MB > {self.config.max_file_size_mb}MB)，"
                f"拒绝全量读取。请使用采样参数"
            )

        return p

    def _detect_encoding(self, file_path: Path) -> str:
        """自动检测编码，默认回退到 utf-8。"""
        if _HAS_CHARDET:
            with open(file_path, "rb") as f:
                raw = f.read(100_000)
            result = chardet.detect(raw)
            enc = result.get("encoding", "utf-8")
            confidence = result.get("confidence", 0)
            log.info("编码检测: %s (置信度 %.2f)", enc, confidence)
            return enc or "utf-8"
        return "utf-8"

    def _detect_separator(self, file_path: Path, encoding: str) -> str:
        """用 csv.Sniffer 自动检测分隔符，失败时回退到逗号。"""
        try:
            with open(file_path, "r", encoding=encoding, newline="") as f:
                sample = f.read(10_000)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except Exception:
            return ","

    def _estimate_rows(self, file_path: Path, encoding: str, sep: str) -> int:
        """快速估算行数（大文件不逐行计数）。"""
        file_size = file_path.stat().st_size
        with open(file_path, "r", encoding=encoding, newline="") as f:
            # 读前 1000 行算平均行长
            reader = csv.reader(f, delimiter=sep)
            total_bytes = 0
            lines = 0
            for i, row in enumerate(reader):
                if i >= 1000:
                    break
                total_bytes += len(sep.join(row).encode(encoding))
                lines = i + 1
        if lines == 0:
            return 0
        avg_bytes_per_line = total_bytes / lines
        return int(file_size / avg_bytes_per_line)

    def _resolve_sample_pct(self, estimated_rows: int) -> tuple[float, str | None]:
        """根据估算行数决定采样率。"""
        if estimated_rows <= 0:
            return 1.0, None
        if estimated_rows > VERY_LARGE_CSV_ROWS:
            return 0.10, f"采样率 10%，全量约 {estimated_rows // 10000} 万行"
        if estimated_rows > LARGE_CSV_ROWS:
            return 0.30, f"采样率 30%，全量约 {estimated_rows // 10000} 万行"
        return 1.0, None

    # ── 公共 API ──────────────────────────────────────────

    def read_info(self, file_path: str) -> dict[str, Any]:
        """读取 CSV 基本信息（不加载全量数据）。"""
        p = self._check_safety(file_path)
        encoding = self._detect_encoding(p)
        sep = self._detect_separator(p, encoding)
        estimated_rows = self._estimate_rows(p, encoding, sep)

        # 读 header
        with open(p, "r", encoding=encoding, newline="") as f:
            reader = csv.reader(f, delimiter=sep)
            header = next(reader, [])
            n_cols = len(header)

        file_size_mb = round(p.stat().st_size / (1024 * 1024), 2)

        return {
            "file_path": str(p),
            "file_name": p.name,
            "file_size_mb": file_size_mb,
            "encoding": encoding,
            "separator": sep,
            "estimated_rows": estimated_rows,
            "n_cols": n_cols,
            "columns": header,
        }

    def read_sample(self, file_path: str, limit: int = 10) -> dict[str, Any]:
        """采样读取前 N 行，返回列名 + 数据行。"""
        p = self._check_safety(file_path)
        encoding = self._detect_encoding(p)
        sep = self._detect_separator(p, encoding)

        with open(p, "r", encoding=encoding, newline="") as f:
            reader = csv.reader(f, delimiter=sep)
            header = next(reader, [])
            rows = []
            for i, row in enumerate(reader):
                if i >= limit:
                    break
                rows.append(row)

        return {"columns": header, "rows": rows, "n_rows_sampled": len(rows)}

    def read_dataframe(self, file_path: str, sample_pct: int = 100,
                       nrows: int | None = None) -> "pd.DataFrame":
        """用 pandas 读取数据（用于统计计算）。
        sample_pct < 100 时等距采样。
        """
        if not _HAS_PANDAS:
            raise RuntimeError("pandas 未安装，无法使用 CSV 统计功能")

        p = self._check_safety(file_path)
        encoding = self._detect_encoding(p)
        sep = self._detect_separator(p, encoding)

        if sample_pct >= 100 and nrows is None:
            return pd.read_csv(p, encoding=encoding, sep=sep)

        # 采样模式
        if nrows is not None:
            return pd.read_csv(p, encoding=encoding, sep=sep, nrows=nrows)

        # 百分比采样：先算总行数，再跳行读取
        estimated_rows = self._estimate_rows(p, encoding, sep)
        effective_pct, _note = self._resolve_sample_pct(estimated_rows)
        actual_pct = min(sample_pct / 100, effective_pct)
        if actual_pct >= 1.0:
            return pd.read_csv(p, encoding=encoding, sep=sep)

        skip_rows = max(1, int(1 / actual_pct)) - 1
        # 先读 header
        header_row = pd.read_csv(p, encoding=encoding, sep=sep, nrows=0)
        # 跳过读取
        df = pd.read_csv(
            p, encoding=encoding, sep=sep,
            skiprows=lambda i: i > 0 and (i - 1) % (skip_rows + 1) != 0,
        )
        # 对齐列名
        df.columns = header_row.columns.tolist()[:len(df.columns)]
        return df
