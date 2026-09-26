from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shared.constants import get_code_dir

# CJK 字符（汉字/谚文/全角符号等）：多数分词器一个 CJK 字符 ≈ 1 token
_CJK_RE = re.compile("[⺀-鿿가-힣！-｠]")


def _text_tokens(text: str) -> int:
    """单段文本估算（F-5 CJK 校准）：CJK 按 1 字符/token，其余按 4 字符/token。

    原 len//4 口径对中文约 7 倍低估（实测同轮 798 vs 真实 5626），
    导致压缩阈值显著滞后、靠 CONTEXT_OVERFLOW 恢复路径兜底多烧调用。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def rough_tokens(messages: list[dict]) -> int:
    """Rough token estimate for a message list（CJK 校准口径，见 _text_tokens）。

    Replace with tiktoken or the Anthropic token-count API for accuracy.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _text_tokens(content)
        elif isinstance(content, list):
            total += sum(_text_tokens(str(item.get("content", ""))) for item in content)
        for tc in msg.get("tool_calls", []):
            total += _text_tokens(str(tc.get("input") or tc.get("function", {}).get("arguments", "")))
    return total


def ensure_app_workdir(user_id: str | int, app_id: str | int) -> Path:
    workdir = get_code_dir(user_id, app_id)
    workdir.mkdir(parents=True, exist_ok=True)
    if workdir.exists():
        print(workdir, "已创建")
    else:
        print(workdir, "创建失败")
    return workdir



def ensure_context_workdir(context: Any) -> Path:
    existing = str(getattr(context, "workdir", "") or "").strip()
    if existing:
        workdir = Path(existing)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = ensure_app_workdir(getattr(context, "user_id", "") or "main", getattr(context, "app_id", "main"))
        setattr(context, "workdir", str(workdir))
    return workdir