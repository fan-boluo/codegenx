import os
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.utils.log_utils import log
from bot.agent.tool_handler import ToolsHandler
from shared.constants import get_bot_code_dir

MEMORY_TOOL_NAMES = {
    "memory_search",
    "memory_get",
    "write_short_term",
    "write_long_term",
    "write_identity_memory",
}

class ToolExecutor:
    def __init__(self, tools_handler: ToolsHandler, safe_paths: Optional[List[str]] = None):
        self.tools_handler = tools_handler
        # Resolve all safe paths to absolute paths
        self.safe_paths = [Path(p).resolve() for p in (safe_paths or [])]
        if not self.safe_paths:
            # 如果没有配置 safe_path，可以将其限制为当前工作区或某个默认策略
            # 这里先假设如果没有配置则默认当前文件夹是 safe_path，保护不跑出去
            self.safe_paths = [Path(os.getcwd()).resolve()]
            log.warning(f"No safe_paths configured, defaulting to current working directory: {self.safe_paths}")

    async def execute(self, tool_call: Dict[str, Any], context: Any) -> Any:
        """
        统一执行工具的逻辑。包含安全检查。
        """
        tool_name = tool_call.get("name")
        tool_input = dict(tool_call.get("arguments", {}) or {})

        if tool_name in MEMORY_TOOL_NAMES:
            tool_input.setdefault("app_id", getattr(context, "app_id", "main"))

        if tool_name in {"read_file", "write_file", "edit_file"}:
            tool_input.setdefault("app_id", getattr(context, "app_id", "main"))

        if tool_name in {"write_short_term", "write_long_term", "write_identity_memory"}:
            tool_input.setdefault("session_id", getattr(context, "session_id", "default"))
            tool_input.setdefault("turn_id", getattr(context, "turn_id", ""))

        if tool_name == "todo":
            tool_input.setdefault("planner", getattr(context, "metadata", {}).get("planner"))

        if tool_name == "compact":
            tool_input.setdefault("context", context)
            tool_input.setdefault("context_compactor", getattr(context, "metadata", {}).get("context_compactor"))

        if tool_name == "task":
            tool_input.setdefault("app_id", getattr(context, "app_id", "main"))
            tool_input.setdefault("trace_id", getattr(context, "metadata", {}).get("trace_id", ""))
            tool_input.setdefault("plan_summary", getattr(context, "plan_state", "") or getattr(context, "metadata", {}).get("plan_state", ""))
            tool_input.setdefault("parent_session_id", getattr(context, "session_id", ""))
            tool_input.setdefault("parent_turn_id", getattr(context, "turn_id", ""))
        
        # 1. 查找工具并拉取验证
        tool = next((t for t in self.tools_handler.tools if t.name == tool_name), None)
        if not tool:
            log.error(f"Unknown tool called: {tool_name}")
            return {"error": f"未知工具：{tool_name}"}

        # 2. 安全检查 (安全边界前置)
        error_msg = self._perform_safety_checks(tool_name, tool_input)
        if error_msg:
            log.warning(f"Safety check failed for tool '{tool_name}': {error_msg}")
            return {"error": f"拒绝执行: {error_msg}"}

        # 3. 工具执行
        try:
            func = tool.executor
            if inspect.iscoroutinefunction(func):
                result = await func(params=tool_input)
            else:
                result = func(params=tool_input)

            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif hasattr(result, "dict"):
                result = result.dict()

            if tool_name == "todo":
                planner = tool_input.get("planner")
                if planner is not None:
                    try:
                        context.plan_state = planner.get_state()
                    except Exception as exc:
                        log.warning(f"Failed to refresh plan_state after todo tool: {exc}")

            return result
        except Exception as e:
            log.error(f"Tool '{tool_name}' crashed: {e}")
            return {"error": f"工具执行异常: {str(e)}"}

    def _perform_safety_checks(self, tool_name: str, tool_input: Dict[str, Any]) -> Optional[str]:
        """
        检查所有有关路径的参数，判断是否在 safe_path 内。
        并对 Bash 命令进行基本的敏感策略处理（示例）。
        """
        # 检查路径
        # 通常文件操作的 tool 参数可能叫 path, filename, src, dest, 等等
        for key, value in tool_input.items():
            # 尝试根据名称推测这是一个路径相关的参数
            # 或更严谨地，可以查 schema，但现在通过名称推测或显式硬编码都可以。
            # 为了简单健壮，我们检查值为字符串且看着像绝对或相对路径的操作
            
            # 若 key 匹配已知的路径参数名，我们严格校验它
            if key in ["path", "filename", "src", "dest"]:
                if not isinstance(value, str):
                    continue
                try:
                    target_path = self._resolve_candidate_path(value, tool_input.get("app_id", "main"))
                    if not self._is_safe_path(target_path):
                         return f"越界访问：目标路径 '{target_path}' 不在 safe_path 允许范围内"
                except Exception as e:
                     return f"路径解析错误：{e}"

        # 对于 bash 类工具，我们可以进一步限制敏感命令
        if tool_name in ["run_bash", "bash", "execute_command"]:
            cmd = tool_input.get("command", "")
            # 此处可以做命令黑名单或敏感命令策略...
            # 示例：
            forbidden_cmds = ["rm -rf /", "mkfs", "chown"]
            if any(forbidden in cmd for forbidden in forbidden_cmds):
                 return f"命中了危险命令策略：不能执行可能破坏系统的命令"

        return None
        
    def _is_safe_path(self, target: Path) -> bool:
        for sp in self.safe_paths:
            try:
                # 能够计算 relatively 说明 target 在 sp 之内
                target.relative_to(sp)
                return True
            except ValueError:
                pass
        return False

    def _resolve_candidate_path(self, value: str, app_id: str | int) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = get_bot_code_dir(app_id) / candidate
        return candidate.resolve()