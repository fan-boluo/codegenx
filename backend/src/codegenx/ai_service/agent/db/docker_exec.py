"""
docker_exec —— 容器内命令执行

职责：
- 提供通用的 docker exec 非交互执行能力（供 SQL / Python 代码共用）
- 封装 mysql 客户端调用及结果解析的便捷方法
- 非 dev 环境使用；dev 环境由上层直接走 MysqlManager / 本地 python

注意：使用 asyncio.create_subprocess_exec，不分配 TTY，避免交互式会话问题。
"""

import asyncio
import base64
from dataclasses import dataclass
from typing import Any

from shared import log

# docker exec 默认超时（秒）
DEFAULT_DOCKER_TIMEOUT = 60
# mysql 查询默认超时
DEFAULT_SQL_TIMEOUT = 30


@dataclass
class ExecResult:
    """容器内命令执行的统一结果。"""
    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


async def docker_exec(
    container: str,
    *args: str,
    timeout: int = DEFAULT_DOCKER_TIMEOUT,
    env: dict[str, str] | None = None,
) -> ExecResult:
    """非交互模式执行容器内命令。

    Args:
        container: docker 容器名
        *args:     容器内要执行的命令及参数（直接传给 docker exec，不经过 shell）
        timeout:   超时秒数
        env:       额外注入的环境变量

    Returns:
        ExecResult（exit_code / stdout / stderr）
    """
    cmd = ["docker", "exec"]
    if env:
        for k, v in env.items():
            cmd.extend(["-e", f"{k}={v}"])
    cmd.append(container)
    cmd.extend(args)

    log.info("docker_exec: {}", " ".join(cmd))

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.error("docker_exec 超时 ({}s)：容器={}, args={}", timeout, container, args)
        return ExecResult(exit_code=-1, stdout="", stderr=f"docker exec 超时 ({timeout}s)")
    except FileNotFoundError:
        log.error("docker 命令不存在，请确认 docker 已安装并在 PATH 中")
        return ExecResult(exit_code=-1, stdout="", stderr="docker 命令不可用")
    except Exception as exc:
        log.error("docker_exec 异常：{}", exc)
        return ExecResult(exit_code=-1, stdout="", stderr=str(exc))

    return ExecResult(
        exit_code=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def _parse_mysql_table_output(stdout: str) -> list[dict[str, Any]]:
    """解析 mysql -N -B (或默认表格模式) 输出为 list[dict]。

    容器内 mysql 使用 tab 分隔 + \\N 表示 NULL（默认 -B -N 模式）。
    """
    rows: list[dict[str, Any]] = []
    if not stdout.strip():
        return rows

    lines = stdout.rstrip("\n").split("\n")
    # 有表头（默认模式）或无表头（-N 模式），这里按无表头处理，后续可扩展
    # 当前约定容器内执行时使用 -N -B，故直接把每一行当一条结果
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("\t")
        # -B 模式下 tab 分隔，暂用 _col_0, _col_1 占位
        # 更稳妥的方案：第一次执行时先 SHOW COLUMNS，或者约定上层用 WITH 子查询
        # 当前阶段保持简单：把整行当单条记录，方便 AI 后续解析
        rows.append({"_raw": line, "_cols": parts})
    return rows


async def execute_sql_in_docker(
    container: str,
    db_name: str,
    sql: str,
    *,
    mysql_user: str = "root",
    mysql_password: str = "",
    mysql_host: str = "127.0.0.1",
    mysql_port: int = 3306,
    timeout: int = DEFAULT_SQL_TIMEOUT,
) -> list[dict[str, Any]]:
    """在容器内通过 mysql 客户端执行只读 SQL，返回解析后的 rows。

    容器内命令形如：
        mysql -h{host} -P{port} -u{user} -p{password} -N -B -D {db} -e "{sql}"
    """
    args = [
        "mysql",
        f"-h{mysql_host}",
        f"-P{mysql_port}",
        f"-u{mysql_user}",
        f"-p{mysql_password}",
        "-N", "-B",            # tab 分隔、无表头
        "-D", db_name,
        "-e", sql,
    ]
    result = await docker_exec(container, *args, timeout=timeout)
    if not result.success:
        raise RuntimeError(
            f"容器内 SQL 执行失败 (exit={result.exit_code}): {result.stderr or result.stdout}"
        )
    return _parse_mysql_table_output(result.stdout)


async def execute_python_in_docker(
    container: str,
    code: str,
    *,
    python_bin: str = "python",
    timeout: int = DEFAULT_DOCKER_TIMEOUT,
) -> ExecResult:
    """在容器内执行一段 Python 代码，返回统一 ExecResult。

    为避免代码中引号/换行在 docker exec 命令行被 shell 吃掉，采用 base64 传参：
    - 容器内运行固定命令 `python -c "import base64,sys;exec(base64.b64decode(sys.argv[1]).decode('utf-8'))" <base64>`
    - 调用方需保证 `code` 中访问的文件路径已经是容器内路径（由上层做路径映射）
    """
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    args = [
        python_bin,
        "-c",
        "import base64,sys;exec(base64.b64decode(sys.argv[1]).decode('utf-8'))",
        encoded,
    ]
    return await docker_exec(container, *args, timeout=timeout)
