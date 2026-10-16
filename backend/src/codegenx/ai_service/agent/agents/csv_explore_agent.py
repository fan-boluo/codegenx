"""CSV 表格分析智能体

用户在任务中提供 csv 文件名（如 "分析 sales.csv 的销售情况"），本 agent 负责：
1. 从当前用户项目 code_dir 下读取所有 csv 文件的元信息（列名 + 样本行）作为 prompt 上下文
2. LLM 生成一段 pandas 分析代码，代码中用 __CSV_PATH__ 占位符表示 csv 绝对路径
3. CsvAction 校验路径安全（限制在 code_dir 下）后，用 subprocess 调用 PYTHON_ENV 执行
4. 执行结果截断后返回给调用方（通常是 Supervisor）

参考 data_explore_agent 的 ReAct 模式实现，是一个纯执行器。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage, NextMessageModel
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from shared import log
from shared.constants import DATA_ROOT_DIR, PYTHON_ENV, get_code_dir

if TYPE_CHECKING:
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState


# 单个 csv 文件读入元信息的最大字节数（超过则只读头部）
MAX_CSV_BYTES_FOR_META = 1 * 1024 * 1024  # 1MB
# prompt 中每个 csv 展示的样本行数（不含表头）
CSV_SAMPLE_ROWS = 3
# prompt 中允许列出的 csv 文件数量上限
MAX_CSV_FILES_IN_PROMPT = 20
# 代码执行超时（秒）
EXEC_TIMEOUT_SECONDS = 60
# 返回给调用方的 stdout 最大字符数
MAX_RESULT_CHARS = 4000
# LLM 代码中表示 csv 绝对路径的占位符
CSV_PATH_PLACEHOLDER = "__CSV_PATH__"


class CsvActionInput(BaseModel):
    """CSV 分析动作的输入（LLM 返回内容的解析结果）。"""
    filename: str = ""          # 目标 csv 文件名（仅文件名，不含路径）
    python_code: str = ""       # pandas 分析代码，读取 csv 必须使用 __CSV_PATH__ 占位符
    explanation: str = ""       # 对分析目标的简要说明


class CsvAction(Action[CsvActionInput]):
    """CSV 分析动作：解析 LLM 代码 → 校验文件路径 → subprocess/docker 执行 → 返回 stdout。

    与 SQLAction 保持一致的环境分支策略：
    - dev    → 本地 subprocess 调用 PYTHON_ENV["model"] 执行
    - 非 dev → docker exec 在 sql_exec_docker_container 容器内执行（宿主机 .data/ 挂载到 docker_data_root/）
    """

    async def act(self, ai_message: ActOutput, session_state=None, **kwargs) -> ActOutput:
        parsed = self.parse_ai_message(ai_message, CsvActionInput)
        if parsed is None:
            return ActOutput(success=False, message="CSV 分析代码解析失败（非合法 JSON）")
        if not parsed.filename.strip():
            return ActOutput(success=False, message="缺少 filename，无法定位 csv 文件")
        if not parsed.python_code.strip():
            return ActOutput(success=False, message="缺少 python_code，无法执行分析")

        # ── 路径安全校验：基于宿主机 code_dir，防路径穿越 ──
        try:
            csv_path = self._resolve_csv_path(parsed.filename, session_state)
        except ValueError as exc:
            log.warning("CSV 路径校验未通过: {}", exc)
            return ActOutput(success=False, message=str(exc))

        # ── 按环境分支执行：占位符替换目标路径 + 选择执行通道 ──
        from shared import settings
        try:
            if settings.env == "dev":
                # 本地：占位符替换为宿主机绝对路径（windows 含反斜杠，用 raw 字符串）
                code = parsed.python_code.replace(CSV_PATH_PLACEHOLDER, f"r'{csv_path}'")
                stdout, stderr, rc = await self._run_python_local(code, cwd=csv_path.parent)
            else:
                # docker：占位符替换为容器内路径（宿主机 .data/ → docker_data_root/，统一正斜杠）
                container_csv = self._to_container_path(csv_path)
                code = parsed.python_code.replace(CSV_PATH_PLACEHOLDER, f"r'{container_csv}'")
                stdout, stderr, rc = await self._run_python_in_docker(code)
        except asyncio.TimeoutError:
            return ActOutput(success=False, message=f"代码执行超时（>{EXEC_TIMEOUT_SECONDS}s）")
        except Exception as exc:
            log.error("CSV 分析代码执行异常: {}", exc)
            return ActOutput(success=False, message=f"代码执行失败: {exc}")

        if rc != 0:
            # 非零退出：把 stderr 喂回给 LLM 修正
            return ActOutput(
                success=False,
                message=f"代码非零退出 rc={rc}\nstderr: {stderr[:1000]}",
            )

        log.info("CsvAction 执行成功 | env={} | file={} | stdout_len={}",
                 settings.env, csv_path.name, len(stdout))
        return ActOutput(success=True, output={
            "filename": csv_path.name,
            "explanation": parsed.explanation,
            "stdout": stdout,
            "stderr": stderr,
        })

    @staticmethod
    def _resolve_csv_path(filename: str, session_state) -> Path:
        """将用户提供的 csv 文件名解析为宿主机 code_dir 下的绝对路径，并做安全校验。

        校验规则：
        - 必须是纯文件名（不允许任何路径分隔符），防路径穿越
        - 必须以 .csv 结尾（不区分大小写）
        - resolve 后必须落在 code_dir 之内
        - 文件必须真实存在
        """
        if session_state is None:
            raise ValueError("缺少 session_state，无法定位用户项目目录")
        code_dir = get_code_dir(session_state.user_id, session_state.app_id).resolve()

        # 拒绝任何路径分隔符，仅允许纯文件名
        if os.sep in filename or (os.altsep and os.altsep in filename):
            raise ValueError(f"filename 不允许包含路径分隔符: {filename!r}")
        if not filename.lower().endswith(".csv"):
            raise ValueError(f"仅支持 .csv 文件: {filename!r}")

        target = (code_dir / filename).resolve()
        if not str(target).startswith(str(code_dir) + os.sep):
            raise ValueError(f"路径越界: {filename!r}")
        if not target.exists() or not target.is_file():
            raise ValueError(f"文件不存在: {filename}（code_dir={code_dir}）")
        return target

    @staticmethod
    def _to_container_path(host_path: Path) -> str:
        """把宿主机 .data/ 下的绝对路径映射为容器内路径（统一正斜杠）。

        约定：宿主机 DATA_ROOT_DIR 整个挂载到容器的 settings.docker_data_root。
        """
        from shared import settings
        rel = host_path.resolve().relative_to(DATA_ROOT_DIR.resolve())
        return f"{settings.docker_data_root.rstrip('/')}/{rel.as_posix()}"

    @staticmethod
    async def _run_python_local(code: str, cwd: Path) -> tuple[str, str, int]:
        """dev 环境：本地 subprocess 调用 PYTHON_ENV 执行，返回 (stdout, stderr, returncode)。

        用临时文件而不是 -c 传代码，避免命令行长度限制与引号转义问题。
        """
        python_exe = PYTHON_ENV.get("model") or sys.executable

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as fp:
                fp.write(code)
                tmp_path = fp.name

            proc = await asyncio.create_subprocess_exec(
                python_exe, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=EXEC_TIMEOUT_SECONDS
            )
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            return stdout, stderr, proc.returncode or 0
        finally:
            # 不管成功失败都清理临时文件
            if tmp_path:
                with contextlib.suppress(Exception):
                    os.unlink(tmp_path)

    @staticmethod
    async def _run_python_in_docker(code: str) -> tuple[str, str, int]:
        """非 dev 环境：docker exec 在容器内执行 python，返回 (stdout, stderr, returncode)。"""
        from codegenx.ai_service.agent.db.docker_exec import execute_python_in_docker
        from shared import settings
        result = await execute_python_in_docker(
            container=settings.sql_exec_docker_container,
            code=code,
            python_bin=settings.docker_python_bin,
            timeout=EXEC_TIMEOUT_SECONDS,
        )
        return result.stdout, result.stderr, result.exit_code


class CsvExploreAgent(BaseReActAgent):
    """ CSV 表格分析 """
    name = "csv_explore"

    profile: PromptConfig = PromptConfig(
        role="Data Analyst",
        name="csv_explore",  # 和 config.json 中的 name 对应
        goal="根据用户提供的 csv 文件名，编写 pandas 代码完成数据分析",
        desc="读取当前项目下的 csv 表格文件，使用 pandas 代码完成数据分析",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "每次分析**仅输出一段可独立运行的 Python 代码**，代码中读取 csv 必须使用占位符 __CSV_PATH__ 作为文件路径（例如：pd.read_csv(__CSV_PATH__)），执行框架会自动替换为真实绝对路径。",
            "代码必须通过 print() 将分析结果输出到 stdout，便于框架捕获；**禁止写文件、弹窗、绘图**。",
            "仅可使用 pandas / numpy / Python 标准库，**禁止引入 sklearn / matplotlib / scipy 等重型依赖**。",
            "禁止虚构列名或数据值，所有列名必须来自下方 csv 元信息；注意数值列的单位与缺失值。",
            "分析目标所需的统计（sum/mean/groupby/排序/筛选等）尽量在一次代码运行内完成，避免多次打印中间结果。",
        ],
        resource_prompt="当前项目下可用的 csv 文件及其结构（列名、样本行）如下:\n{{csv_info}}",
        expand_prompt="",
        examples="",
        out_schema="""请严格按如下 JSON 格式输出：
{
    "filename": "目标 csv 文件名（必须来自上方可用 csv 列表）",
    "python_code": "一段完整可运行的 Python 代码，读取 csv 时使用 __CSV_PATH__ 占位符作为路径",
    "explanation": "对分析目标的简要说明"
}
确保输出仅有 JSON，无多余文字，可被 python json.loads 加载。

版本说明：
Python 3.10.9
Pandas 2.2.3 
Numpy  1.26.4""",
    )

    def __init__(self):
        super().__init__()
        # 注意：这里是实例而非类，act 是实例方法
        self.actions = [CsvAction()]
        self._csv_info = ""  # _prepare_agent 中填充

    def _prepare_prompt_params(self) -> dict:
        return {"csv_info": self._csv_info}

    async def _prepare_agent(self, session_state):
        """ 先收集 csv 元信息，再 build prompt（顺序与 data_explore 一致） """
        self._csv_info = await self._collect_csv_info(session_state)
        await super()._prepare_agent(session_state)
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")

    @staticmethod
    async def _collect_csv_info(session_state: RuntimeSessionState) -> str:
        """扫描当前用户项目 code_dir 下所有 csv，读取列结构与样本行作为 prompt 上下文。

        - 单文件过大时只读头部，避免撑爆内存
        - 单个文件读取失败不阻塞主流程，仅记录 warning
        """
        code_dir = get_code_dir(session_state.user_id, session_state.app_id)
        if not code_dir.exists():
            return "（当前项目 code 目录不存在）"

        csv_files = sorted(code_dir.glob("*.csv"))[:MAX_CSV_FILES_IN_PROMPT]
        if not csv_files:
            return f"（{code_dir} 下暂无 csv 文件）"

        parts: list[str] = []
        for fp in csv_files:
            try:
                size = fp.stat().st_size
                read_bytes = min(size, MAX_CSV_BYTES_FOR_META)
                # 二进制读头部再按 utf-8 解码，避免大文件一次性读入
                with fp.open("rb") as f:
                    chunk = f.read(read_bytes)
                text = chunk.decode("utf-8", errors="replace")
                lines = [ln for ln in text.splitlines() if ln.strip()][: CSV_SAMPLE_ROWS + 1]
                header = lines[0] if lines else ""
                samples = lines[1:]
                size_mb = size / 1024 / 1024
                parts.append(
                    f"### 文件: {fp.name} ({size_mb:.2f}MB)\n"
                    f"列名: {header}\n"
                    f"样本前{len(samples)}行:\n" + ("\n".join(samples) if samples else "（无样本行）")
                )
            except Exception as exc:
                log.warning("读取 csv 元信息失败 {}: {}", fp.name, exc)
                parts.append(f"### 文件: {fp.name}（读取失败: {exc}）")
        return "\n\n".join(parts)

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """观察 CsvAction 结果：
        - 失败 → 把错误追加到 messages 让 LLM 修正代码（最多 max_steps 次）
        - 成功 → stdout 截断后返回给调用方（避免超长塞爆上下文）
        """
        msg.output = act_output.model_dump(mode="json")
        caller = msg.sender or "user"

        if not act_output.success:
            log.warning("CsvExploreAgent 执行失败，让 LLM 重试：{}", act_output.message)
            self._messages.append({
                "role": "user",
                "content": (
                    f"上一次生成的代码执行失败，请修正后重新输出 JSON。\n"
                    f"失败原因：{act_output.message}\n"
                    f"请重新生成正确的 Python 代码（记得用 __CSV_PATH__ 占位符读取 csv）。"
                ),
            })
            msg.success = False
            return msg

        # ── 执行成功：截断 stdout 后返回给调用方 ──
        parsed = act_output.output or {}
        stdout = parsed.get("stdout", "") or ""
        truncated = len(stdout) > MAX_RESULT_CHARS
        if truncated:
            stdout = stdout[:MAX_RESULT_CHARS]

        payload = {
            "filename": parsed.get("filename", ""),
            "explanation": parsed.get("explanation", ""),
            "stdout": stdout,
            "truncated": truncated,
        }
        log.info("CsvExploreAgent 执行成功 | 返回 {} | stdout_len={}{}",
                 caller, len(stdout), " (截断)" if truncated else "")
        # message 必须携带真实执行输出（stdout），供调用方（Supervisor 审查 / 历史
        # 记录 auto_plan_manager.add_agent_result）直接看到结果，metadata 保留结构化原文
        msg.next = NextMessageModel(
            next_agent=caller,
            message=(
                f"{self.agent_name} 已完成 csv 分析，请核查。\n"
                f"文件: {payload['filename']}\n"
                f"执行输出（stdout）：\n{stdout}"
            ),
            metadata=payload,
        )
        msg.success = True
        return msg


if __name__ == "__main__":
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState

    def _make_session_state() -> RuntimeSessionState:
        """构造一个最小可用的 RuntimeSessionState（只需 session_id + request）。"""
        from codegenx.ai_service.schema.ai_schema import UserAIRequest

        request = UserAIRequest(
            appId="test-app",
            userId="test-user",
            message="分析 sales.csv 的销售情况",
            sessionId="test-session",
            traceId="t1",
            requestId="r1",
        )
        return RuntimeSessionState(session_id="test-session", request=request)

    async def test():
        agent = CsvExploreAgent()
        agent.resolve_agent_config()
        await agent._prepare_agent(session_state=_make_session_state())

    asyncio.run(test())
