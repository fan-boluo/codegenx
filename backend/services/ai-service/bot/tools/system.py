import asyncio
import subprocess
from typing import Any
from pathlib import Path

from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log

# 去掉继承BaseTool，危险工具，不加载
class BashTool:
    @property
    def name(self) -> str:
        return "bash"

    @property
    def label(self) -> str:
        return "system"

    @property
    def description(self) -> str:
        return "Run a shell command."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute"
                }
            },
            "required": ["command"]
        }

    async def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
    ) -> ToolResult:
        command = params["command"]
        
        if signal and signal.is_set():
            raise asyncio.CancelledError("Operation aborted")
            
        try:
            # For simplicity, running async process
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.cwd())  # Defaults to current directory
            )
            
            # Wait for execution or cancellation
            if signal:
                # Polling for signal or process finish
                while process.returncode is None:
                    if signal.is_set():
                        process.terminate()
                        raise asyncio.CancelledError("Operation aborted")
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
            else:
                await process.wait()
                
            stdout, stderr = await process.communicate()
            
            out = (stdout + stderr).decode('utf-8', errors='replace').strip()
            # Trim output if too long
            if len(out) > 50000:
                out = out[:50000] + "\n... (output truncated)"
                
            if not out:
                out = "(no output)"
                
            return ToolResult(
                success=process.returncode == 0,
                data=[{"type": "text", "text": out}],
            )
            
        except Exception as e:
            log.error(f"Bash command failed: {e}")
            return ToolResult(
                success=False,
                data=[{"type": "text", "text": f"Error: {str(e)}"}],
            )
