"""
Grep tool using ripgrep with JSON parsing for structured output and async streaming.
Mirrors packages/coding-agent/src/core/tools/grep.ts
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from typing import Any

from bot.tools.base import BaseTool, ToolResult
from bot.tools.truncate_utils import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
)
from shared.config.log_config import log

DEFAULT_LIMIT = 100


def _find_rg() -> str | None:
    return shutil.which("rg")


class GrepTool(BaseTool):

    @property
    def name(self) -> str:
        return "grep"

    @property
    def label(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return (
            f"A powerful search tool built on ripgrep. Search file contents for "
            f"a pattern. Returns matching lines with context blocks (surrounding lines). "
            f"Output is limited to {DEFAULT_LIMIT} matches or "
            f"{format_size(DEFAULT_MAX_BYTES)}. Long lines are truncated to "
            f"{GREP_MAX_LINE_LENGTH} chars.\n\n"
            f"## When to use\n"
            f"- Finding where a function/class/variable is defined or referenced\n"
            f"- Searching for error messages, log patterns, or configuration keys\n"
            f"- Strategy: grep first, then read_file only matching files "
            f"(with offset/limit to see relevant lines)\n\n"
            f"## Usage\n"
            f"- Supports full regex syntax (e.g., 'log.*Error', 'function\\s+\\w+')\n"
            f"- Filter files with glob parameter (e.g., '*.js', '**/*.tsx')\n"
            f"- Use context to see surrounding lines of each match\n"
            f"- Pattern syntax: Uses ripgrep - literal braces need escaping "
            f"(use `interface\\{{\\}}` to find `interface{{}}` in Go code)"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex or literal with literal: true)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search (default: current directory)",
                },
                "glob": {
                    "type": "string",
                    "description": "Filter files by glob pattern, e.g. '*.ts'",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default: false)",
                },
                "literal": {
                    "type": "boolean",
                    "description": "Treat pattern as literal string (default: false)",
                },
                "context": {
                    "type": "number",
                    "description": "Lines to show before and after each match (default: 0)",
                },
                "limit": {
                    "type": "number",
                    "description": f"Maximum number of matches (default: {DEFAULT_LIMIT})",
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
            log.warning("grep 执行异常: {}", exc)
            return ToolResult(success=False, message=f"搜索失败: {exc}", render=f"搜索失败: {params.get('pattern', '')}")

    async def _do_execute(
        self,
        params: dict,
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        pattern: str = params["pattern"]
        search_dir: str | None = params.get("path")
        glob_filter: str | None = params.get("glob")
        ignore_case: bool = params.get("ignore_case", False)
        literal: bool = params.get("literal", False)
        context: int = params.get("context", 0)
        limit: int = params.get("limit", DEFAULT_LIMIT)

        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        rg_path = _find_rg()
        if not rg_path:
            raise RuntimeError(
                "ripgrep (rg) is not available. Install it: https://github.com/BurntSushi/ripgrep"
            )

        cwd = params.get("cwd", os.getcwd())
        search_path = search_dir if (search_dir and os.path.isabs(search_dir)) else os.path.join(cwd, search_dir or ".")
        search_path = os.path.normpath(search_path)

        if not os.path.exists(search_path):
            raise FileNotFoundError(f"路径不存在: {search_path}")

        is_directory = os.path.isdir(search_path)
        effective_limit = max(1, limit)
        context_value = max(0, context)

        args = ["--json", "--line-number", "--color=never", "--hidden"]
        if ignore_case:
            args.append("--ignore-case")
        if literal:
            args.append("--fixed-strings")
        if glob_filter:
            args.extend(["--glob", glob_filter])
        args.extend([pattern, search_path])

        # File cache for context lines
        file_cache: dict[str, list[str]] = {}

        def get_file_lines(file_path: str) -> list[str]:
            if file_path not in file_cache:
                try:
                    with open(file_path, encoding="utf-8", errors="replace") as f:
                        content = f.read().replace("\r\n", "\n").replace("\r", "\n")
                    file_cache[file_path] = content.split("\n")
                except Exception:
                    file_cache[file_path] = []
            return file_cache[file_path]

        def format_path(file_path: str) -> str:
            if is_directory:
                rel = os.path.relpath(file_path, search_path)
                if rel and not rel.startswith(".."):
                    return rel.replace("\\", "/")
            return os.path.basename(file_path)

        def format_block(file_path: str, line_number: int) -> list[str]:
            relative_path = format_path(file_path)
            lines = get_file_lines(file_path)
            if not lines:
                return [f"{relative_path}:{line_number}: (unable to read file)"]

            block: list[str] = []
            c = context_value
            start = max(1, line_number - c) if c > 0 else line_number
            end = min(len(lines), line_number + c) if c > 0 else line_number

            for current in range(start, end + 1):
                if current > len(lines):
                    break
                line_text = lines[current - 1]
                sanitized = line_text.replace("\r", "")
                is_match_line = current == line_number
                truncated_text, was_truncated = truncate_line(sanitized)
                if was_truncated:
                    nonlocal lines_truncated
                    lines_truncated = True
                if is_match_line:
                    block.append(f"{relative_path}:{current}: {truncated_text}")
                else:
                    block.append(f"{relative_path}-{current}- {truncated_text}")
            return block

        lines_truncated = False
        match_count = 0
        match_limit_reached = False
        matches: list[dict[str, Any]] = []

        proc = await asyncio.create_subprocess_exec(
            rg_path, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_data = b""

        async def read_stderr():
            nonlocal stderr_data
            if proc.stderr:
                stderr_data = await proc.stderr.read()

        stderr_task = asyncio.create_task(read_stderr())

        if proc.stdout:
            async for raw_line in proc.stdout:
                if signal and signal.is_set():
                    proc.kill()
                    raise asyncio.CancelledError("Operation aborted")

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or match_count >= effective_limit:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "match":
                    match_count += 1
                    file_path = event.get("data", {}).get("path", {}).get("text")
                    line_number = event.get("data", {}).get("line_number")
                    if file_path and isinstance(line_number, int):
                        matches.append({"file_path": file_path, "line_number": line_number})

                    if match_count >= effective_limit:
                        match_limit_reached = True
                        proc.kill()
                        break

        await stderr_task
        await proc.wait()

        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        if match_count == 0:
            return ToolResult(success=True, data="No matches found", render=f"搜索: {pattern} - 无匹配")

        output_lines: list[str] = []
        for match in matches:
            block = format_block(match["file_path"], match["line_number"])
            output_lines.extend(block)

        raw_output = "\n".join(output_lines)
        truncation = truncate_head(raw_output, max_lines=sys.maxsize)

        output = truncation.content
        notices: list[str] = []

        if match_limit_reached:
            notices.append(
                f"{effective_limit} matches limit reached. "
                f"Use limit={effective_limit * 2} for more, or refine pattern"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        if lines_truncated:
            notices.append(
                f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. "
                "Use read tool to see full lines"
            )

        if notices:
            output += f"\n\n[{'. '.join(notices)}]"

        return ToolResult(success=True, data=output, render=f"搜索: {pattern} - {match_count} 条匹配")
