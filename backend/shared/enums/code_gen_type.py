from __future__ import annotations

from enum import StrEnum


class CodeGenTypeEnum(StrEnum):
    HTML = "html"
    MULTI_FILE = "multi_file"
    VUE_PROJECT = "vue_project"

    @classmethod
    def get_enum_by_value(cls, value: str | None) -> "CodeGenTypeEnum | None":
        if not value:
            return None
        for item in cls:
            if item.value == value:
                return item
        return None
