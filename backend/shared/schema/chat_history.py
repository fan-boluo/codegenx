from __future__ import annotations

from datetime import datetime

from pydantic import Field

from shared.schema.common import LongIdModel, PageRequest, TimeModel


class ChatHistoryQueryRequest(PageRequest):
    id: int | None = None
    message: str | None = None
    message_type: str | None = Field(default=None, alias="messageType")
    app_id: int | None = Field(default=None, alias="appId")
    user_id: int | None = Field(default=None, alias="userId")
    last_create_time: datetime | None = Field(default=None, alias="lastCreateTime")


class ChatHistoryVO(LongIdModel, TimeModel):
    message: str | None = None
    message_type: str | None = Field(default=None, alias="messageType")
    app_id: int | None = Field(default=None, alias="appId")
    user_id: int | None = Field(default=None, alias="userId")
