"""前缀 ID 生成工具。

user / app 表主键为自增整型时容易混淆，统一改为带类型前缀的字符串：
    user_xxxx / app_xxxx，随机部分为 4 位小写字母+数字。
"""

from __future__ import annotations

import secrets
import string

# id 类型前缀：区分 user / app 实体
USER_ID_PREFIX = "user_"
APP_ID_PREFIX = "app_"

# 随机部分长度（小写字母+数字，36^4 ≈ 168万组合）
_ID_RANDOM_LEN = 4

_ALPHABET = string.ascii_lowercase + string.digits


def generate_prefixed_id(prefix: str) -> str:
    """生成形如 {prefix}xxxx 的 ID，随机部分 4 位。"""
    return prefix + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_RANDOM_LEN))


def generate_user_id() -> str:
    return generate_prefixed_id(USER_ID_PREFIX)


def generate_app_id() -> str:
    return generate_prefixed_id(APP_ID_PREFIX)
