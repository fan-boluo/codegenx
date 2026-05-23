#!/usr/bin/env python3
"""
GrepTool Python Version - Core search functionality based on ripgrep/grep
Supports multiple output modes: content, files_with_matches, count
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import asyncio

from bot.tools.base import BaseTool, ToolResult

# Version control system directories to exclude
VCS_DIRECTORIES = ['.git', '.svn', '.hg', '.bzr', '.jj', '.sl']
DEFAULT_HEAD_LIMIT = 250
DEFAULT_MAX_COLUMNS = 500


@dataclass
class GrepOutput():
    """Output structure for grep results"""
    mode: str  # 'content', 'files_with_matches', 'count'
    num_files: int
    filenames: List[str]
    content: Optional[str] = None
    num_lines: Optional[int] = None
    num_matches: Optional[int] = None
    applied_limit: Optional[int] = None
    applied_offset: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {
            'mode': self.mode,
            'numFiles': self.num_files,
            'filenames': self.filenames,
        }
        if self.content is not None:
            result['content'] = self.content
        if self.num_lines is not None:
            result['numLines'] = self.num_lines
        if self.num_matches is not None:
            result['numMatches'] = self.num_matches
        if self.applied_limit is not None:
            result['appliedLimit'] = self.applied_limit
        if self.applied_offset is not None:
            result['appliedOffset'] = self.applied_offset
        return result


def apply_head_limit(
        items: List[str],
        limit: Optional[int] = None,
        offset: int = 0
) -> Tuple[List[str], Optional[int]]:
    """
    Apply head limit and offset pagination to results.

    Args:
        items: List of items to limit
        limit: Maximum items to return (None/unset uses DEFAULT_HEAD_LIMIT, 0 = unlimited)
        offset: Skip first N items

    Returns:
        Tuple of (limited_items, applied_limit)
    """
    if limit == 0:
        # Explicit 0 = unlimited escape hatch
        return items[offset:], None

    effective_limit = limit if limit is not None else DEFAULT_HEAD_LIMIT
    sliced = items[offset:offset + effective_limit]

    # Only report applied_limit when truncation occurred
    was_truncated = len(items) - offset > effective_limit
    applied_limit = effective_limit if was_truncated else None

    return sliced, applied_limit


def to_relative_path(abs_path: str, cwd: Optional[str] = None) -> str:
    """Convert absolute path to relative path"""
    if cwd is None:
        cwd = os.getcwd()
    try:
        return os.path.relpath(abs_path, cwd)
    except ValueError:
        # On Windows, relpath fails if paths are on different drives
        return abs_path


def build_ripgrep_args(
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        type_filter: Optional[str] = None,
        output_mode: str = 'files_with_matches',
        context_before: Optional[int] = None,
        context_after: Optional[int] = None,
        context_both: Optional[int] = None,
        show_line_numbers: bool = True,
        case_insensitive: bool = False,
        multiline: bool = False,
) -> List[str]:
    """
    Build ripgrep command line arguments.

    Args:
        pattern: Regex pattern to search for
        path: Path to search in
        glob: Glob pattern to filter files
        type_filter: File type filter
        output_mode: 'content', 'files_with_matches', or 'count'
        context_before: Lines before match
        context_after: Lines after match
        context_both: Lines before and after match
        show_line_numbers: Show line numbers in output
        case_insensitive: Case insensitive search
        multiline: Enable multiline mode

    Returns:
        List of command arguments for ripgrep
    """
    args = ['rg', '--hidden']

    # Exclude VCS directories
    for vcs_dir in VCS_DIRECTORIES:
        args.extend(['--glob', f'!{vcs_dir}'])

    # Limit line length
    args.extend(['--max-columns', str(DEFAULT_MAX_COLUMNS)])

    # Multiline mode
    if multiline:
        args.extend(['-U', '--multiline-dotall'])

    # Case insensitive
    if case_insensitive:
        args.append('-i')

    # Output mode flags
    if output_mode == 'files_with_matches':
        args.append('-l')
    elif output_mode == 'count':
        args.append('-c')

    # Line numbers (only for content mode)
    if show_line_numbers and output_mode == 'content':
        args.append('-n')

    # Context flags
    if output_mode == 'content':
        if context_both is not None:
            args.extend(['-C', str(context_both)])
        else:
            if context_before is not None:
                args.extend(['-B', str(context_before)])
            if context_after is not None:
                args.extend(['-A', str(context_after)])

    # Pattern (use -e flag if pattern starts with dash)
    if pattern.startswith('-'):
        args.extend(['-e', pattern])
    else:
        args.append(pattern)

    # Type filter
    if type_filter:
        args.extend(['--type', type_filter])

    # Glob patterns
    if glob:
        glob_patterns = []
        raw_patterns = glob.split()

        for raw_pattern in raw_patterns:
            # If pattern has braces, keep it whole
            if '{' in raw_pattern and '}' in raw_pattern:
                glob_patterns.append(raw_pattern)
            else:
                # Split on commas
                glob_patterns.extend([p for p in raw_pattern.split(',') if p])

        for glob_pattern in glob_patterns:
            args.extend(['--glob', glob_pattern])

    # Search path
    if path:
        args.append(path)

    return args


def run_ripgrep(
        args: List[str],
        cwd: Optional[str] = None
) -> Tuple[List[str], int]:
    """
    Run ripgrep command and return results.

    Args:
        args: Command arguments
        cwd: Working directory

    Returns:
        Tuple of (output_lines, return_code)
    """
    if cwd is None:
        cwd = os.getcwd()

    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30
        )

        lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
        return lines, result.returncode

    except subprocess.TimeoutExpired:
        raise RuntimeError("Search timed out after 30 seconds")
    except FileNotFoundError:
        raise RuntimeError("ripgrep (rg) not found. Please install ripgrep.")


def grep_search(
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        type_filter: Optional[str] = None,
        output_mode: str = 'files_with_matches',
        context_before: Optional[int] = None,
        context_after: Optional[int] = None,
        context_both: Optional[int] = None,
        show_line_numbers: bool = True,
        case_insensitive: bool = False,
        head_limit: Optional[int] = None,
        offset: int = 0,
        multiline: bool = False,
) -> GrepOutput:
    """
    Main search function - core logic.

    Args:
        pattern: Regex pattern to search for
        path: Path to search in (defaults to cwd)
        glob: Glob pattern for files
        type_filter: File type to search
        output_mode: 'content', 'files_with_matches', or 'count'
        context_before: Lines before match
        context_after: Lines after match
        context_both: Lines before and after match
        show_line_numbers: Show line numbers
        case_insensitive: Case insensitive search
        head_limit: Limit output to N items
        offset: Skip first N items
        multiline: Enable multiline regex

    Returns:
        GrepOutput object with results
    """
    if path is None:
        path = os.getcwd()

    # Build and run ripgrep
    args = build_ripgrep_args(
        pattern=pattern,
        path=path,
        glob=glob,
        type_filter=type_filter,
        output_mode=output_mode,
        context_before=context_before,
        context_after=context_after,
        context_both=context_both,
        show_line_numbers=show_line_numbers,
        case_insensitive=case_insensitive,
        multiline=multiline,
    )

    results, return_code = run_ripgrep(args, cwd=os.getcwd())

    # Handle content mode
    if output_mode == 'content':
        limited_results, applied_limit = apply_head_limit(results, head_limit, offset)

        # Convert absolute paths to relative
        final_lines = []
        for line in limited_results:
            colon_idx = line.find(':')
            if colon_idx > 0:
                file_path = line[:colon_idx]
                rest = line[colon_idx:]
                final_lines.append(to_relative_path(file_path) + rest)
            else:
                final_lines.append(line)

        return GrepOutput(
            mode='content',
            num_files=0,
            filenames=[],
            content='\n'.join(final_lines),
            num_lines=len(final_lines),
            applied_limit=applied_limit,
            applied_offset=offset if offset > 0 else None,
        )


    # Handle count mode
    elif output_mode == 'count':
        limited_results, applied_limit = apply_head_limit(results, head_limit, offset)

        # Convert absolute paths to relative and count matches
        final_count_lines = []
        total_matches = 0
        file_count = 0

        for line in limited_results:
            colon_idx = line.rfind(':')
            if colon_idx > 0:
                file_path = line[:colon_idx]
                count_str = line[colon_idx + 1:]
                try:
                    count = int(count_str)
                    total_matches += count
                    file_count += 1
                    final_count_lines.append(to_relative_path(file_path) + ':' + count_str)
                except ValueError:
                    final_count_lines.append(line)
            else:
                final_count_lines.append(line)

        return GrepOutput(
            mode='count',
            num_files=file_count,
            filenames=[],
            content='\n'.join(final_count_lines),
            num_matches=total_matches,
            applied_limit=applied_limit,
            applied_offset=offset if offset > 0 else None,
        )

    # Default: files_with_matches mode
    else:
        # Sort by modification time (or name for determinism)
        file_stats = []
        for file_path in results:
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                mtime = 0
            file_stats.append((file_path, mtime))

        # Sort by mtime descending, then by name
        file_stats.sort(key=lambda x: (-x[1], x[0]))
        sorted_matches = [x[0] for x in file_stats]

        # Apply head limit
        limited_matches, applied_limit = apply_head_limit(sorted_matches, head_limit, offset)

        # Convert to relative paths
        relative_matches = [to_relative_path(f) for f in limited_matches]

        return GrepOutput(
            mode='files_with_matches',
            num_files=len(relative_matches),
            filenames=relative_matches,
            applied_limit=applied_limit,
            applied_offset=offset if offset > 0 else None,
        )


class GrepTool(BaseTool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def label(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "\n".join((
            "A powerful search tool built on ripgrep.",

            "Usage:",
            "- ALWAYS use 'grep' for search tasks. NEVER invoke `grep` or `rg` as a ${BASH_TOOL_NAME} command. The ${GREP_TOOL_NAME} tool has been optimized for correct permissions and access.",
            "- Supports full regex syntax (e.g., 'log.*Error', 'function\\s+\\w+')",
            "- Filter files with glob parameter (e.g., '*.js', '**/*.tsx') or type parameter (e.g., 'js', 'py', 'rust')",
            "- Output modes: 'content' shows matching lines, 'files_with_matches' shows only file paths (default), 'count' shows match counts",
            "- Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)",
            "- Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`",
        ))

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Path to search in (optional)"},
                "glob": {"type": "string", "description": "Glob pattern for files (optional)"},
                "type_filter": {"type": "string", "description": "File type filter (optional)"},
                "output_mode": {"type": "string", "enum": ["content","files_with_matches","count"], "description": "Output mode"},
                "context_before": {"type": "integer"},
                "context_after": {"type": "integer"},
                "context_both": {"type": "integer"},
                "case_insensitive": {"type": "boolean"},
                "head_limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "multiline": {"type": "boolean"},
            },
            "required": ["pattern"]
        }

    async def execute(self, params: dict, signal: asyncio.Event | None = None) -> ToolResult:
        pattern = params.get("pattern")
        if not pattern:
            return ToolResult(success=False, message="Missing required parameter: pattern")

        # Map params to grep_search signature
        search_kwargs = {
            "pattern": pattern,
            "path": params.get("path"),
            "glob": params.get("glob"),
            "type_filter": params.get("type_filter"),
            "output_mode": params.get("output_mode", "files_with_matches"),
            "context_before": params.get("context_before"),
            "context_after": params.get("context_after"),
            "context_both": params.get("context_both"),
            "show_line_numbers": True,
            "case_insensitive": params.get("case_insensitive", False),
            "head_limit": params.get("head_limit"),
            "offset": params.get("offset", 0),
            "multiline": params.get("multiline", False),
        }

        try:
            # run the (blocking) search in a thread to avoid blocking the event loop
            result: GrepOutput = await asyncio.to_thread(grep_search, **search_kwargs)

            return ToolResult(success=True, data=result.to_dict())

        except Exception as e:
            return ToolResult(success=False, message=str(e))


def main():
    """CLI interface for grep tool"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description='Python grep tool')
    parser.add_argument('pattern', help='Regex pattern to search for')
    parser.add_argument('-p', '--path', help='Path to search in')
    parser.add_argument('-g', '--glob', help='Glob pattern for files')
    parser.add_argument('-t', '--type', dest='type_filter', help='File type to search')
    parser.add_argument('-m', '--mode', default='files_with_matches',
                        choices=['content', 'files_with_matches', 'count'],
                        help='Output mode')
    parser.add_argument('-B', type=int, help='Lines before match')
    parser.add_argument('-A', type=int, help='Lines after match')
    parser.add_argument('-C', type=int, help='Lines before and after match')
    parser.add_argument('-n', '--no-line-numbers', action='store_true',
                        help='Disable line numbers')
    parser.add_argument('-i', '--case-insensitive', action='store_true',
                        help='Case insensitive search')
    parser.add_argument('--head-limit', type=int, help='Limit output')
    parser.add_argument('--offset', type=int, default=0, help='Skip first N items')
    parser.add_argument('--multiline', action='store_true', help='Enable multiline mode')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    try:
        result = grep_search(
            pattern=args.pattern,
            path=args.path,
            glob=args.glob,
            type_filter=args.type_filter,
            output_mode=args.mode,
            context_before=args.B,
            context_after=args.A,
            context_both=args.C,
            show_line_numbers=not args.no_line_numbers,
            case_insensitive=args.case_insensitive,
            head_limit=args.head_limit,
            offset=args.offset,
            multiline=args.multiline,
        )

        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            # Print human-readable format
            if result.content:
                print(result.content)
            else:
                for filename in result.filenames:
                    print(filename)

            if result.applied_limit:
                print(f"\n[Limit: {result.applied_limit}, Offset: {result.applied_offset or 0}]")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
