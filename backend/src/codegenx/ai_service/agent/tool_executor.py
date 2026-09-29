import inspect
from pathlib import Path
from typing import Any, Dict, List

from codegenx.ai_service.agent.runtime_schema import ActivateTurn, RuntimeSessionState
from codegenx.ai_service.agent.tool_handler import ToolRegistry
from codegenx.ai_service.hook import HookContext, HookDecision, HookEvent, on
from shared import log
from shared.constants import get_code_dir

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


# ── 路径安全守卫（无状态函数 + 模块级监听器，docs/Hook设计.md §4.2）──────────
# 注意：@on 装饰时注册裸函数对象（分发器直接 callback(ctx)），
# 监听器必须是无 self 的模块级函数，否则每次分发都 TypeError。


def _resolve_safe_paths(session_state: Any | None) -> List[Path]:
    """从会话状态解析 safe_paths（RuntimeSessionState.safe_paths 经 context_manager 推导）。"""
    candidate = getattr(session_state, "safe_paths", None) if session_state is not None else None
    if not candidate:
        return []
    return [Path(path).resolve() for path in candidate if str(path).strip()]


def _is_safe_path(target: Path, safe_paths: List[Path]) -> bool:
    for sp in safe_paths:
        try:
            # 能够计算 relatively 说明 target 在 sp 之内
            target.relative_to(sp)
            return True
        except ValueError:
            pass
    return False


def _resolve_candidate_path(value: str, app_id: str | int, user_id: str | int = "") -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = get_code_dir(user_id or "main", app_id) / candidate
    return candidate.resolve()


@on(HookEvent.BEFORE_TOOL_CALL, name="safe_path_guard", priority=30)
async def safe_path_guard(ctx: "HookContext") -> "HookDecision | None":
    """路径安全守卫（迁自 ToolExecutor._perform_safety_checks，安全边界前置）。

    检查路径类参数是否在 safe_path 内，越界返回 HookDecision.block 短路拒绝；
    并对 Bash 类命令做基本危险命令拦截。
    与 tools/base.py 的 file_tool_param_guard（参数空值校验）分工不同，故命名区分。
    """
    tool_call = ctx.data.get("tool_call") or {}
    tool_name = str(tool_call.get("name") or "")
    tool_input = dict(tool_call.get("arguments") or {})
    if not tool_name or not tool_input:
        return None

    session_state = ctx.session
    safe_paths = _resolve_safe_paths(session_state)
    user_id = str(getattr(session_state, "user_id", "") or "") if session_state is not None else ""
    request = getattr(session_state, "request", None) if session_state is not None else None
    app_id = str(getattr(request, "app_id", "") or "") if request is not None else ""
    app_id = app_id or str(tool_input.get("app_id", "main"))

    for key, value in tool_input.items():
        # 路径类参数严格校验（文件操作工具的参数可能叫 path/filename/src/dest 等）
        if key in ("path", "filename", "src", "dest", "file_path"):
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                target_path = _resolve_candidate_path(value, app_id, user_id)
                if not _is_safe_path(target_path, safe_paths):
                    return HookDecision.block(
                        f"{tool_name}：越界访问：目标路径 '{target_path}' 不在 safe_path 允许范围内"
                    )
            except Exception as e:
                return HookDecision.block(f"{tool_name}：路径解析错误：{e}")

    # 对于 bash 类工具，进一步做危险命令黑名单拦截（示例策略）
    if tool_name in ("run_bash", "bash", "execute_command"):
        cmd = str(tool_input.get("command", ""))
        # 此处可以做命令黑名单或敏感命令策略...
        forbidden_cmds = ("rm -rf /", "mkfs", "chown")
        if any(forbidden in cmd for forbidden in forbidden_cmds):
            return HookDecision.block(
                f"{tool_name}：命中了危险命令策略：不能执行可能破坏系统的命令"
            )

    return None


class ToolExecutor:
    def __init__(self, tools_registry: ToolRegistry):
        self.tools_registry = tools_registry

    async def execute(self, tool_call: Dict[str, Any], turn_state: ActivateTurn,
                      session_state: RuntimeSessionState | None = None) -> Any:
        """
        统一执行工具的逻辑。

        安全检查已前置为 BEFORE_TOOL_CALL 监听器（safe_path_guard /
        file_tool_param_guard 等），由 runtime 在调用本方法之前分发；
        blocked 的调用根本不会走到这里。
        """
        tool_name = tool_call.get("name")
        tool_input = dict(tool_call.get("arguments", {}) or {})
        app_id = str(session_state.request.app_id) if session_state is not None and session_state.request is not None else "main"
        user_id = getattr(session_state, "user_id", "") if session_state is not None else ""
        session_id = getattr(session_state, "session_id", "") if session_state is not None else ""
        turn_id = getattr(session_state, "request_id", "") if session_state is not None else ""
        trace_id = getattr(session_state, "trace_id", "")
        stop_signal = getattr(session_state, "stop_signal", None) if session_state is not None else None

        if tool_name in {"read_file", "write_file", "edit_file", "delete_file", "list_directory", "code_check", "find", "grep"}:
            tool_input.setdefault("app_id", app_id)
            tool_input.setdefault("user_id", user_id)

        if tool_name == "subagent":
            tool_input.setdefault("app_id", app_id)
            tool_input.setdefault("user_id", user_id)
            tool_input.setdefault("trace_id", trace_id)
            tool_input.setdefault("plan_summary", "")
            tool_input.setdefault("parent_session_id", session_id)
            tool_input.setdefault("parent_turn_id", turn_id)

        if tool_name in TASK_TOOL_NAMES:
            # P2 服务化：不再注入 TaskManager 实例，改为注入 ids（工具内部走 app.tasks）
            tool_input.setdefault("user_id", user_id)
            tool_input.setdefault("app_id", app_id)
            tool_input.setdefault("session_id", session_id)

        if tool_name in DATA_ANALYSIS_TOOL_NAMES:
            db_name = getattr(session_state, "db_name", None) if session_state is not None else None
            if db_name:
                tool_input.setdefault("db_name", db_name)
            tool_input.setdefault("app_id", app_id)

        # 1. 查找工具
        tool = next((t for t in self.tools_registry.tools if t.name == tool_name), None)
        if not tool:
            log.error(f"Unknown tool called: {tool_name}")
            return {"error": f"未知工具：{tool_name}"}

        # 2. 工具执行 在工具执行内部已经做了异常处理了
        func = tool.executor
        call_kwargs = {"params": tool_input}
        if "signal" in inspect.signature(func).parameters:
            call_kwargs["signal"] = stop_signal
        if inspect.iscoroutinefunction(func):
            result = await func(**call_kwargs)
        else:
            result = func(**call_kwargs)

        return result
