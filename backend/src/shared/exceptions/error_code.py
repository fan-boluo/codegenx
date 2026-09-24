from __future__ import annotations

from enum import Enum


class ErrorCode(Enum):
    SUCCESS = (0, "ok")
    PARAMS_ERROR = (40000, "请求参数错误")
    NOT_LOGIN_ERROR = (40100, "未登录")
    NO_AUTH_ERROR = (40101, "无权限")
    FORBIDDEN_ERROR = (40300, "禁止访问")
    NOT_FOUND_ERROR = (40400, "请求数据不存在")
    RATE_LIMIT_ERROR = (42900, "请求过于频繁")
    SYSTEM_ERROR = (50000, "系统内部异常")
    OPERATION_ERROR = (50001, "操作失败")

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message

    def get_code(self) -> int:
        return self.code

    def get_message(self) -> str:
        return self.message


ERROR_MESSAGE_MAP: dict[int, str] = {
    item.get_code(): item.get_message()
    for item in ErrorCode
}