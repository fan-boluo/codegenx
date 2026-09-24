"""Response helpers."""

from __future__ import annotations

from typing import TypeVar

from shared.exceptions.error_code import ErrorCode
from shared.schema.common import BaseResponse

T = TypeVar("T")


def success(data: T) -> BaseResponse[T]:
    return BaseResponse(code=ErrorCode.SUCCESS.get_code(), data=data, message="ok")


def error(code: int, message: str | None = None) -> BaseResponse[None]:
    return BaseResponse(
        code=code,
        data=None,
        message=message or "系统内部异常"
    )
