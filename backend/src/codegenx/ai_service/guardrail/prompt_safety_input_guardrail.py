from __future__ import annotations

import re

from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode


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