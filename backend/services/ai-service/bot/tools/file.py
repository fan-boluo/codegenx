import asyncio
import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
import aiofiles
from pydantic import BaseModel, Field

from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log
from shared.constants import get_code_dir

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB
GREP_MAX_LINE_LENGTH = 500  # Max chars per grep match line

class TextContent(BaseModel):
    """Text content block"""
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """Image content block"""
    type: Literal["image"] = "image"
    data: str  # Base64 or URL
    mime_type: str | None = Field(default=None, alias="mimeType")

@dataclass
class TruncationResult:
    """Result of truncation operation"""

    content: str
    """The truncated content"""

    truncated: bool
    """Whether truncation occurred"""

    truncated_by: Literal["lines", "bytes"] | None
    """What caused truncation (lines or bytes limit)"""

    total_lines: int
    """Total number of lines in original content"""

    total_bytes: int
    """Total bytes in original content"""

    output_lines: int
    """Number of lines in output"""

    output_bytes: int
    """Number of bytes in output"""

    last_line_partial: bool
    """Whether last line was partially truncated (tail only)"""

    first_line_exceeds_limit: bool
    """Whether first line alone exceeds byte limit (head only)"""

    max_lines: int
    """Maximum lines limit used"""

    max_bytes: int
    """Maximum bytes limit used"""


def format_size(num_bytes: int) -> str:
    """
    Format byte count as human-readable size.

    Args:
        num_bytes: Number of bytes

    Returns:
        Formatted string (e.g., "1.5KB", "2.3MB")
    """
    if num_bytes < 1024:
        return f"{num_bytes}B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    else:
        return f"{num_bytes / (1024 * 1024):.1f}MB"
def resolve_read_path(path: str, app_id: str | int = "main"):
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = get_code_dir(app_id) / expanded
    resolved = expanded.resolve()

    return resolved


def check_path(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    if not os.access(path, os.R_OK):
        raise PermissionError(f"File not readable: {path}")


def detect_image_mime_type(path: Path):
    suffix = path.suffix.lower()

    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp',
        '.svg': 'image/svg+xml',
    }

    return mime_types.get(suffix)


def truncate_with_max_limit(content:str):
    lines = content.split('\n')
    total_lines =len(lines)
    total_bytes =len(content.encode('utf-8'))

    max_bytes = DEFAULT_MAX_BYTES
    max_lines = DEFAULT_MAX_LINES
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            last_line_partial=False,
            first_line_exceeds_limit=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    first_line_bytes = len(lines[0].encode('utf-8'))
    if first_line_bytes > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=0,
            output_bytes=0,
            last_line_partial=False,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    output_lines_arr: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"

    # 累计行数，达到最大size截断
    for i, line in enumerate(lines):
        if i >= max_lines:
            truncated_by = "lines"
            break

        # +1 for newline (except first line)
        line_bytes = len(line.encode('utf-8')) + (1 if i > 0 else 0)

        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break

        output_lines_arr.append(line)
        output_bytes_count += line_bytes

    # If we exited due to line limit
    if len(output_lines_arr) >= max_lines and output_bytes_count <= max_bytes:
        truncated_by = "lines"

    output_content = '\n'.join(output_lines_arr)
    final_output_bytes = len(output_content.encode('utf-8'))

    return TruncationResult(
        content=output_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output_lines_arr),
        output_bytes=final_output_bytes,
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


class ReadFileTool(BaseTool):

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def label(self) -> str:
        return "file"

    @property
    def description(self) -> str:
        return (
            f"Read the contents of a file. Supports text files and images " 
                  f"(jpg, png, gif, webp). Images are sent as attachments. "
                  f"For text files, output is truncated to {DEFAULT_MAX_LINES} lines "
                  f"or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
                  f"Use offset/limit for large files. When you need the full file, "
                  f"continue with offset until complete."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path to read"
            },
            "offset": {
                "type": "integer",
                "description": "Starting line number (1-indexed, optional)"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read (optional)"
            },
        },
        "required": ["path"]
    }

    async def read_file(self, path: str) -> bytes:
        """Read file contents"""
        async with aiofiles.open(path, 'rb') as f:
            return await f.read()

    async def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
            # on_update: Callable[[AgentToolResult[TDetails]], None] | None = None,
    ) -> ToolResult:
        path = params["path"]
        offset = params.get("offset")
        limit = params.get("limit")

        # Resolve path with macOS compatibility
        absolute_path = resolve_read_path(path, params.get("app_id", "main"))

        # Enforce fs.workspaceOnly if configured
        check_path(absolute_path)

        # Check if already cancelled
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        # Check if aborted after access check
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        # Detect if image
        mime_type = detect_image_mime_type(absolute_path)

        if mime_type:
            # Read as image (binary)
            buffer = await self.read_file(str(absolute_path))
            base64_data = base64.b64encode(buffer).decode('utf-8')

            # Check if aborted after reading
            if signal and signal.is_set():
                raise asyncio.CancelledError("Operation aborted")

            # TODO: Implement image resizing if auto_resize_images is True
            # For now, just return the image as-is
            text_note = f"Read image file [{mime_type}]"

            return ToolResult(
                success=True,
                data=
                    TextContent(text=text_note).model_dump() +
                    ImageContent(data=base64_data, mime_type=mime_type).model_dump()

            )
        else:
            # Read as text file
            buffer = await self.read_file(str(absolute_path))
            text_content = buffer.decode('utf-8')
            all_lines = text_content.split('\n')
            total_file_lines = len(all_lines)

            # Check if aborted after reading
            if signal and signal.is_set():
                raise asyncio.CancelledError("Operation aborted")

            # Apply offset (1-indexed to 0-indexed)
            start_line = max(0, (offset - 1)) if offset else 0
            start_line_display = start_line + 1  # For display (1-indexed)

            # Check if offset is out of bounds
            if start_line >= total_file_lines:
                raise ValueError(
                    f"Offset {offset} is beyond end of file "
                    f"({total_file_lines} lines total)"
                )

            # Apply limit if specified
            user_limited_lines: int | None = None
            if limit is not None:
                end_line = min(start_line + limit, total_file_lines)
                selected_content = '\n'.join(all_lines[start_line:end_line])
                user_limited_lines = end_line - start_line
            else:
                selected_content = '\n'.join(all_lines[start_line:])

            # Apply truncation (respects both line and byte limits)
            truncation = truncate_with_max_limit(selected_content)

            output_text: str
            details: dict[str, Any] | None = None

            if truncation.first_line_exceeds_limit:
                # 首行超出，告诉llm部分读取
                first_line_size = format_size(
                    len(all_lines[start_line].encode('utf-8'))
                )
                output_text = (
                    f"[Line {start_line_display} is {first_line_size}, exceeds "
                    f"{format_size(DEFAULT_MAX_BYTES)} limit. Use bash: "
                    f"sed -n '{start_line_display}p' {path} | "
                    f"head -c {DEFAULT_MAX_BYTES}]"
                )
                details = {"truncation": truncation.__dict__}
            elif truncation.truncated:
                # Truncation occurred - build actionable notice
                end_line_display = start_line_display + truncation.output_lines - 1
                next_offset = end_line_display + 1

                output_text = truncation.content

                if truncation.truncated_by == "lines":
                    output_text += (
                        f"\n\n[Showing lines {start_line_display}-{end_line_display} "
                        f"of {total_file_lines}. Use offset={next_offset} to continue.]"
                    )
                else:
                    output_text += (
                        f"\n\n[Showing lines {start_line_display}-{end_line_display} "
                        f"of {total_file_lines} ({format_size(DEFAULT_MAX_BYTES)} limit). "
                        f"Use offset={next_offset} to continue.]"
                    )

                details = {"truncation": truncation.__dict__}
            elif user_limited_lines is not None and start_line + user_limited_lines < total_file_lines:
                # User specified limit, there's more content, but no truncation
                remaining = total_file_lines - (start_line + user_limited_lines)
                next_offset = start_line + user_limited_lines + 1

                output_text = truncation.content
                output_text += (
                    f"\n\n[{remaining} more lines in file. "
                    f"Use offset={next_offset} to continue.]"
                )
            else:
                # No truncation, no user limit exceeded
                output_text = truncation.content

            return ToolResult(
                success=True,
                data=output_text + "\n 以下是执行的details:"+str(details)
            )


class WriteFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def label(self) -> str:
        return "file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does. Automatically creates parent directories."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                },
            },
            "required": ["path", "content"]
        }
    async def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
            # on_update: Callable[[AgentToolResult[TDetails]], None] | None = None,
    ) -> ToolResult:
        path = params["path"]
        content = params["content"]
        # 解析
        absolute_path = resolve_read_path(path, params.get("app_id", "main"))
        log.debug(f"解析后的完整写入路径为:{absolute_path}")
        # 创建父目录
        parant_path = absolute_path.parent
        parant_path.mkdir(parents=True, exist_ok=True)

        # Check if already cancelled
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        # Check if aborted before writing
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        # Write the file
        async with aiofiles.open(absolute_path, 'w', encoding='utf-8') as f:
            await f.write(content)

        # Check if aborted after writing
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")

        return ToolResult(
            success=True,
            data=f"Successfully wrote {len(content)} bytes to {path}"
        )

class EditFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def label(self) -> str:
        return "file"

    @property
    def description(self) -> str:
        return (
            "Replace exact text in a file. Very strict matching."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to edit"
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find and replace"
                },
                "new_text": {
                    "type": "string",
                    "description": "New text to insert"
                },
            },
            "required": ["path", "old_text", "new_text"]
        }
    
    async def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path = params["path"]
        old_text = params["old_text"]
        new_text = params["new_text"]
        
        absolute_path = resolve_read_path(path, params.get("app_id", "main"))
        check_path(absolute_path)
        
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")
            
        async with aiofiles.open(absolute_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            
        if old_text not in content:
            return ToolResult(
                success=False,
                message=f"Error: Exact text not found in {path}"
            )
            
        new_content = content.replace(old_text, new_text, 1)
        
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")
            
        async with aiofiles.open(absolute_path, 'w', encoding='utf-8') as f:
            await f.write(new_content)
            
        return ToolResult(
            success=True,
            data=f"Successfully edited {path}",
        )