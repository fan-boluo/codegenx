"""Project constants."""

from __future__ import annotations

from enum import StrEnum

# 黑名单与限流
BLACKLIST_IP_KEY = "blacklist:ip"
RATE_LIMIT_API_KEY_PREFIX = "rate_limit:api_key:"
RATE_LIMIT_IP_PREFIX = "rate_limit:ip:"
CHAT_API_KEY_RATE_LIMIT_PER_SECOND = 60
INTERNAL_CHAT_IP_RATE_LIMIT_PER_SECOND = 30