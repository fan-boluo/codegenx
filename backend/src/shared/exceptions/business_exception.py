"""Business exception types."""

from __future__ import annotations

from shared.exceptions.error_code import ERROR_MESSAGE_MAP, ErrorCode


class BusinessException(Exception):
    def __init__(self, error: ErrorCode | int, message: str | None = None):
        if isinstance(error, ErrorCode):
            code = error.get_code()
            resolved_message = message or error.get_message()
        else:
            code = error
            resolved_message = message or ERROR_MESSAGE_MAP.get(code, "系统内部异常")
        super().__init__(resolved_message)
        self.code = code
        self.message = resolved_message

    def get_code(self) -> int:
        return self.code

    def get_message(self) -> str:
        return self.message
    
