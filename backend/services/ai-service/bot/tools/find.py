"""
Find files by glob pattern tool.
Uses fd (fdfind) when available, falls back to os.walk.
Mirrors packages/coding-agent/src/core/tools/find.ts
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from bot.tools.base import BaseTool, ToolResult
from bot.tools.truncate_utils import (
    DEFAULT_MAX_BYTES,
    TruncationResult,
    format_size,
    truncate_head,
)
from bot.utils.log_utils import log

DEFAULT_LIMIT = 1000


def _find_fd() -> str | None:
    return shutil.which("fd") or shutil.which("fdfind")


def _load_gitignore_patterns(search_path: str) -> list[str]:
    patterns: list[str] = []
    gitignore = os.path.join(search_path, ".gitignore")
    if os.path.isfile(gitignore):
        try:
            with open(gitignore, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except Exception:
            pass
    return patterns


def _is_gitignored(rel_path: str, patterns: list[str]) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    for pattern in patterns:
        stripped = pattern.rstrip("/")
        if fnmatch.fnmatch(rel_path, stripped) or fnmatch.fnmatch(rel_path, stripped + "/*"):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, stripped):
                return True
    return False


def _glob_files(pattern: str, search_path: str, limit: int) -> list[str]:
    """Fallback glob using os.walk when fd not available."""
    results: list[str] = []
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv"}
    gitignore_patterns = _load_gitignore_patterns(search_path)

    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for filename in files:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, search_path)
            if gitignore_patterns and _is_gitignored(rel_path, gitignore_patterns):
                continue
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(rel_path, pattern):
                results.append(rel_path)
                if len(results) >= limit:
                    return results
    return results


class FindTool(BaseTool):

    @property
    def name(self) -> str:
        return "find"

    @property
    def label(self) -> str:
        return "file"

    @property
    def description(self) -> str:
        return (
            f"Search for files by glob pattern. Returns matching file paths "
            f"relative to the search directory. Respects .gitignore. "
            f"Output is truncated to {DEFAULT_LIMIT} results or "
            f"{format_size(DEFAULT_MAX_BYTES)}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files, e.g. '*.ts', '**/*.json'",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current directory)",
                },
                "limit": {
                    "type": "number",
                    "description": f"Maximum number of results (default: {DEFAULT_LIMIT})",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        params: dict,
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        try:
            return await self._do_execute(params, signal)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("find 执行异常: %s", exc)
            return ToolResult(success=False, message=f"查找文件失败: {exc}")

    async def _do_execute(
        self,
        params: dict,
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        pattern: str = params["pattern"]
        search_dir: str | None = params.get("path")
        limit: int = params.get("limit", DEFAULT_LIMIT)

        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        cwd = params.get("cwd", os.getcwd())
        search_path = search_dir if (search_dir and os.path.isabs(search_dir)) else os.path.join(cwd, search_dir or ".")
        search_path = os.path.normpath(search_path)
        effective_limit = limit

        if not os.path.exists(search_path):
            raise FileNotFoundError(f"路径不存在: {search_path}")

        fd_path = _find_fd()
        relativized: list[str] = []

        if fd_path:
            args = [
                fd_path,
                "--glob",
                "--color=never",
                "--hidden",
                "--max-results", str(effective_limit),
                pattern,
                search_path,
            ]
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout or "").strip()
            if output:
                for raw_line in output.split("\n"):
                    line = raw_line.strip().rstrip("/\\").rstrip("/")
                    if not line:
                        continue
                    if line.startswith(search_path):
                        rel = line[len(search_path):].lstrip(os.sep)
                    else:
                        rel = os.path.relpath(line, search_path)
                    relativized.append(rel)
        else:
            relativized = await asyncio.get_event_loop().run_in_executor(
                None, _glob_files, pattern, search_path, effective_limit
            )

        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        if not relativized:
            return ToolResult(
                success=True,
                data="No files found matching pattern"
            )

        result_limit_reached = len(relativized) >= effective_limit
        raw_output = "\n".join(relativized)
        truncation = truncate_head(raw_output, max_lines=sys.maxsize)

        result_output = truncation.content
        notices: list[str] = []

        if result_limit_reached:
            notices.append(
                f"{effective_limit} results limit reached. "
                f"Use limit={effective_limit * 2} for more, or refine pattern"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")

        if notices:
            result_output += f"\n\n[{'. '.join(notices)}]"

        return ToolResult(success=True, data=result_output)
