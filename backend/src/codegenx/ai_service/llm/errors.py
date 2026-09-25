"""LLM 错误分类（P0-3：替换 str(exc) 关键词匹配）。

基于 OpenAI SDK 类型化异常判定"重试是否有意义"，分四类：
  RETRYABLE         瞬态错误：退避后同模型重试（超时/连接/429/5xx）
  CONTEXT_OVERFLOW  上下文超长：压缩历史后重试
  FATAL             确定性错误：重试无意义（鉴权/参数/404 等），直接失败或走降级
  LOGIC             本地代码 bug：绝不能进重试循环

非 SDK 异常（兼容端点的普通 Exception、自研流级超时）按异常类型 + 文本兜底分类，
关键词集合沿用原 llm_recovery 的行为。
"""
from __future__ import annotations

import asyncio
from enum import Enum

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)


class LLMErrorClass(str, Enum):
    RETRYABLE = "retryable"
    CONTEXT_OVERFLOW = "context_overflow"
    FATAL = "fatal"
    LOGIC = "logic"


# 文本兜底关键词（仅对非 SDK 异常生效；集合沿用原 llm_recovery 行为）
_CONTEXT_TEXT = ("context_length_exceeded", "overlong_prompt", "too long", "maximum context")
_RETRY_TEXT = ("timeout", "rate limit", "rate_limit", "429", "503", "502", "504", "connection", "unavailable")


def classify_llm_error(exc: BaseException) -> LLMErrorClass:
    """把任意异常归入四类，调用方据此决定：重试 / 压缩后重试 / 放弃。"""
    # SDK 类型化异常（子类在前，避免被父类 APIStatusError 提前命中）
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
        return LLMErrorClass.RETRYABLE
    if isinstance(exc, BadRequestError):
        text = str(exc).lower()
        if any(kw in text for kw in _CONTEXT_TEXT):
            return LLMErrorClass.CONTEXT_OVERFLOW
        return LLMErrorClass.FATAL
    if isinstance(exc, (AuthenticationError, PermissionDeniedError, NotFoundError)):
        return LLMErrorClass.FATAL
    if isinstance(exc, APIStatusError):
        # 其余 HTTP 状态：5xx 视为上游瞬态可重试，4xx 视为确定性错误
        return LLMErrorClass.RETRYABLE if getattr(exc, "status_code", 0) >= 500 else LLMErrorClass.FATAL

    # 非 SDK 异常：自研流级超时与 httpx 原生网络错误按瞬态处理
    if isinstance(exc, asyncio.TimeoutError):
        return LLMErrorClass.RETRYABLE
    if isinstance(exc, httpx.HTTPError):
        return LLMErrorClass.RETRYABLE

    # 兼容端点可能抛普通 Exception：按文本兜底分类
    text = str(exc).lower()
    if any(kw in text for kw in _CONTEXT_TEXT) or ("prompt" in text and "long" in text):
        return LLMErrorClass.CONTEXT_OVERFLOW
    if any(kw in text for kw in _RETRY_TEXT):
        return LLMErrorClass.RETRYABLE
    return LLMErrorClass.LOGIC
