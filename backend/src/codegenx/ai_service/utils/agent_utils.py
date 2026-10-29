from pathlib import Path

from shared.constants import get_code_dir
from shared.utils.log_util import log


async def collect_csv_info(user_id:str,app_id:str,show_limit:int=20) -> str:
    """扫描当前用户项目 code_dir 下前xx csv
    """
    return await collect_info(user_id,app_id,show_limit=show_limit,suffix="csv")



async def collect_info(user_id:str,app_id:str,suffix:str=None,show_limit:int=20) -> str:
    """扫描当前用户项目 code_dir 下前xx 文件
    suffix 为空时不做后缀过滤，直接列出目录下所有文件
    """
    code_dir = get_code_dir(user_id, app_id)
    if not code_dir.exists():
        return "（当前项目 code 目录不存在）"

    # 没有指定后缀时匹配全部，指定后缀时按后缀过滤
    pattern = f"*.{suffix}" if suffix else "*"
    files = [fp for fp in sorted(code_dir.glob(pattern)) if fp.is_file()][:show_limit]
    if not files:
        return f"（{code_dir} 下暂无文件）"

    parts: list[str] = [f"用户目录下前{show_limit}个文件："]
    for fp in files:
        try:
            size = fp.stat().st_size
            size_mb = size / 1024 / 1024
            parts.append(
                f"### 文件: {fp.name} ({size_mb:.2f}MB)\n"
            )
        except Exception as exc:
            log.warning("文件元信息失败 {}: {}", fp.name, exc)
            parts.append(f"### 文件: {fp.name}（大小获取失败: {exc}）")
    return "\n\n".join(parts)



def build_directory_skeleton(root: Path, *, max_entries: int = 40, max_depth: int = 2) -> str:
    """当前项目目录结构"""
    if not root.exists():
        return ""

    _IGNORED_DIRS = {
        ".git", "node_modules", "__pycache__", ".data",
        "dist", "build", ".next", ".nuxt",
        "venv", ".venv", "env", ".env", ".tox",
        ".eggs", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    }

    lines: list[str] = []
    prefix_map = {0: "", 1: "  ", 2: "    "}

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth or len(lines) >= max_entries:
            return
        try:
            children = sorted(
                current.iterdir(),
                key=lambda item: (item.is_file(), item.name.lower()),
            )
        except OSError as exc:
            log.warning("Failed to read project skeleton for {}: {}", current, exc)
            return

        indent = prefix_map.get(depth, "    " * depth)
        for child in children:
            if child.is_dir() and child.name in _IGNORED_DIRS:
                continue
            prefix = indent + ("- " if depth > 0 else "")
            lines.append(prefix + child.name + ("/" if child.is_dir() else ""))
            if child.is_dir() and depth < max_depth:
                walk(child, depth + 1)
            if len(lines) >= max_entries:
                break

    walk(root, 0)
    return "\n".join(lines)