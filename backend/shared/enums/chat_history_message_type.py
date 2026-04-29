from __future__ import annotations

from enum import StrEnum


class ChatHistoryMessageTypeEnum(StrEnum):
    USER = "user"
    AI = "ai"

    @classmethod
    def get_enum_by_value(cls, value: str | None) -> "ChatHistoryMessageTypeEnum | None":
        if not value:
            return None
        for item in cls:
            if item.value == value:
                return item
        return None
