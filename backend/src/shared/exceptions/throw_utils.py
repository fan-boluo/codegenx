from __future__ import annotations

from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode


class ThrowUtils:
    @staticmethod
    def throw_if(
        condition: bool,
        error: ErrorCode | BusinessException | int = ErrorCode.SYSTEM_ERROR,
        message: str | None = None,
    ) -> None:
        if not condition:
            return
        if isinstance(error, BusinessException):
            raise error
        raise BusinessException(error, message)