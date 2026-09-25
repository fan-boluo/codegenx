from __future__ import annotations

import re

from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from codegenx.ai_service.hook import HookContext, HookDecision, HookEvent, on


class PromptSafetyInputGuardrail:
    SENSITIVE_WORDS = [
        "忽略之前的指令",
        "ignore previous instructions",
        "ignore above",
        "破解",
        "hack",
        "绕过",
        "bypass",
        "越狱",
        "jailbreak",
    ]

    INJECTION_PATTERNS = [
        re.compile(r"(?i)ignore\s+(?:previous|above|all)\s+(?:instructions?|commands?|prompts?)"),
        re.compile(r"(?i)(?:forget|disregard)\s+(?:everything|all)\s+(?:above|before)"),
        re.compile(r"(?i)(?:pretend|act|behave)\s+(?:as|like)\s+(?:if|you\s+are)"),
        re.compile(r"(?i)system\s*:\s*you\s+are"),
        re.compile(r"(?i)new\s+(?:instructions?|commands?|prompts?)\s*:"),
    ]

    def validate(self, user_input: str) -> None:
        text = (user_input or "").strip()
        if not text:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "输入内容不能为空")
        if len(text) > 1000:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "输入内容过长，不要超过 1000 字")
        lowered = text.lower()
        for sensitive_word in self.SENSITIVE_WORDS:
            if sensitive_word.lower() in lowered:
                raise BusinessException(ErrorCode.FORBIDDEN_ERROR, "输入包含不当内容，请修改后重试")
        for pattern in self.INJECTION_PATTERNS:
            if pattern.search(text):
                raise BusinessException(ErrorCode.FORBIDDEN_ERROR, "检测到恶意输入，请求被拒绝")


def validate_prompt_safety(user_input: str) -> None:
    PromptSafetyInputGuardrail().validate(user_input)


# ── Hook 监听器：输出安全校验接入事件总线（docs/Hook设计.md §4.2） ───────────


@on(HookEvent.ON_COMPLETE, name="output_safety_check", priority=10)
async def output_safety_check(ctx: "HookContext") -> "HookDecision | None":
    """输出安全校验（on_complete 默认实现）：

    对 turn 最终回复复用输入侧的敏感词/注入模式检测，
    命中则 blocked，由触发方以安全提示替换最终回复。
    """
    text = str(ctx.data.get("final_output") or "")
    if not text.strip():
        return None
    lowered = text.lower()
    for word in PromptSafetyInputGuardrail.SENSITIVE_WORDS:
        if word.lower() in lowered:
            return HookDecision.block("回复内容未通过安全校验（命中敏感词），已替换为安全提示")
    for pattern in PromptSafetyInputGuardrail.INJECTION_PATTERNS:
        if pattern.search(text):
            return HookDecision.block("回复内容未通过安全校验（疑似注入内容），已替换为安全提示")
    return None