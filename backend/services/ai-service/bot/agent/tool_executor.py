import os
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.runtime_schema import ActivateTurn, RuntimeSessionState
from bot.utils.log_utils import log
from bot.agent.tool_handler import ToolRegistry
from shared.constants import get_code_dir

MEMORY_TOOL_NAMES = {
    "memory_search",
    "memory_get",
    "write_short_term",
    "write_long_term",
    "write_identity_memory",
}

DATA_ANALYSIS_TOOL_NAMES = {
    "list_tables",
    "describe_table",
    "sample_rows",
    "describe_table_stats",
    "describe_csv",
    "sample_csv_rows",
    "describe_csv_stats",
    "guess_analysis_task",
    "get_table_relationships",
}


TASK_TOOL_NAMES = {"task_create", "task_update", "task_get", "task_list"}

class ToolExecutor:
    def __init__(self, tools_registry: ToolRegistry):
        self.tools_registry = tools_registry

    @staticmethod
    def _get_session_state(context: Any, session: Any | None) -> Any | None:
        if session is not None:
            return session
        return getattr(context, "session_state", None)

    async def execute(self, tool_call: Dict[str, Any], turn_state: ActivateTurn,
                      session_state: RuntimeSessionState | None = None,safe_paths: Optional[List[str]] = None) -> Any:
        """
        统一执行工具的逻辑。包含安全检查。
        """
        tool_name = tool_call.get("name")
        tool_input = dict(tool_call.get("arguments", {}) or {})
        app_id = getattr(session_state, "app_id", "main") if session_state is not None else "main"
        user_id = getattr(session_state, "user_id", "") if session_state is not None else ""
        session_id = getattr(session_state, "session_id", "") if session_state is not None else ""
        turn_id = getattr(session_state, "request_id", "") if session_state is not None else ""
        trace_id = getattr(session_state, "trace_id", "")
        stop_signal = getattr(session_state, "stop_signal", None) if session_state is not None else None
        safe_paths = self._resolve_safe_paths(turn_state, session_state,safe_paths)

        if tool_name in MEMORY_TOOL_NAMES:
            tool_input.setdefault("app_id", app_id)
            tool_input.setdefault(
                "user_id",
                user_id or session_id or "anonymous",
            )

        if tool_name in {"read_file", "write_file", "edit_file"}:
            tool_input.setdefault("app_id", app_id)

        if tool_name in {"write_short_term", "write_long_term"}:
            tool_input.setdefault("session_id", session_id)
            tool_input.setdefault("turn_id", turn_id)
            tool_input.setdefault(
                "user_id",
                user_id or session_id or "anonymous",
            )

        if tool_name == "task":
            tool_input.setdefault("app_id", app_id)
            tool_input.setdefault("trace_id", trace_id)
            tool_input.setdefault("plan_summary", "")
            tool_input.setdefault("parent_session_id", session_id)
            tool_input.setdefault("parent_turn_id", turn_id)

        if tool_name in TASK_TOOL_NAMES:
            # inject TaskManager (s12) — stored on session state per app_id
            task_manager = getattr(session_state, "task_manager", None) if session_state is not None else None
            tool_input.setdefault("task_manager", task_manager)

        if tool_name in DATA_ANALYSIS_TOOL_NAMES:
            db_name = getattr(session_state, "db_name", None) if session_state is not None else None
            if db_name:
                tool_input.setdefault("db_name", db_name)
            tool_input.setdefault("app_id", app_id)

        # 1. 查找工具并拉取验证
        tool = next((t for t in self.tools_registry.tools if t.name == tool_name), None)
        if not tool:
            log.error(f"Unknown tool called: {tool_name}")
            return {"error": f"未知工具：{tool_name}"}

        # 2. 安全检查 (安全边界前置)
        error_msg = self._perform_safety_checks(tool_name, tool_input, safe_paths)
        if error_msg:
            log.warning(f"Safety check failed for tool '{tool_name}': {error_msg}")
            return {"error": f"拒绝执行: {error_msg}"}

        # 3. 工具执行 在工具执行内部已经做了异常处理了
        # try:
        func = tool.executor
        call_kwargs = {"params": tool_input}
        if "signal" in inspect.signature(func).parameters:
            call_kwargs["signal"] = stop_signal
        if inspect.iscoroutinefunction(func):
            result = await func(**call_kwargs)
        else:
            result = func(**call_kwargs)

        # 执行成功则大的输出落盘
        if result.success:
            result = await session_state.context_manager.persist_large_output(tool_call=tool_call,output=result)

        if hasattr(result, "model_dump"):
            result = result.model_dump()
        elif hasattr(result, "dict"):
            result = result.dict()

        return result



    def _resolve_safe_paths(self, context: Any, session_state: Any | None,safe_paths: Optional[List[str]] = None) -> List[Path]:
        for candidate in (
            getattr(context, "safe_paths", None),
            getattr(session_state, "safe_paths", None) if session_state is not None else None,
            safe_paths,
        ):
            if not candidate:
                continue
            resolved = [Path(path).resolve() for path in candidate if str(path).strip()]
            if resolved:
                return resolved
        return list(safe_paths) if safe_paths else []

    def _perform_safety_checks(self, tool_name: str, tool_input: Dict[str, Any], safe_paths: List[Path]) -> Optional[str]:
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
            if key in ["path", "filename", "src", "dest", "file_path"]:
                if not isinstance(value, str):
                    continue
                try:
                    target_path = self._resolve_candidate_path(value, tool_input.get("app_id", "main"))
                    if not self._is_safe_path(target_path, safe_paths):
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
        
    def _is_safe_path(self, target: Path, safe_paths: List[Path]) -> bool:
        for sp in safe_paths:
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
            candidate = get_code_dir(app_id) / candidate
        return candidate.resolve()