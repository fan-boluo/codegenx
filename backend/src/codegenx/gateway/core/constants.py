"""Project constants."""

from __future__ import annotations

from enum import StrEnum

BLACKLIST_IP_KEY = "blacklist:ip"
RATE_LIMIT_IP_PREFIX = "rate_limit:ip:"
RATE_LIMIT_API_PREFIX = "rate_limit:api:"
CHAT_USER_RATE_LIMIT_PER_SECOND = 5
INTERNAL_CHAT_IP_RATE_LIMIT_PER_SECOND = 30
API_RATE_LIMIT_MAX = 100
API_RATE_LIMIT_WINDOW_SECONDS = 60
