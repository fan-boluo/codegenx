"""兼容旧导入路径的 schema 聚合导出。"""

from __future__ import annotations

from shared.schema.app import (
    AppAddRequest,
    AppAdminUpdateRequest,
    AppChatRequest,
    AppDeployRequest,
    AppDeployResponse,
    AppQueryRequest,
    AppUpdateRequest,
    AppVO,
)
from shared.schema.chat_history import ChatHistoryQueryRequest, ChatHistoryVO
from shared.schema.common import BaseResponse, CamelBaseModel, LongIdModel, PageRequest

__all__ = [
    "AppAddRequest",
    "AppAdminUpdateRequest",
    "AppChatRequest",
    "AppDeployRequest",
    "AppDeployResponse",
    "AppQueryRequest",
    "AppUpdateRequest",
    "AppVO",
    "BaseResponse",
    "CamelBaseModel",
    "ChatHistoryQueryRequest",
    "ChatHistoryVO",
    "LongIdModel",
    "PageRequest",
]
