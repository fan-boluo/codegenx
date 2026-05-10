from __future__ import annotations

from datetime import datetime

from pydantic import Field

from shared.schema.common import CamelBaseModel, LongIdModel, PageRequest, TimeModel
from shared.schema.user import UserVO


class AppAddRequest(CamelBaseModel):
    init_prompt: str = Field(alias="initPrompt")


class AppUpdateRequest(CamelBaseModel):
    id: int
    app_name: str | None = Field(default=None, alias="appName")


class AppAdminUpdateRequest(CamelBaseModel):
    id: int
    app_name: str | None = Field(default=None, alias="appName")
    cover: str | None = None
    priority: int | None = None


class AppDeployRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")


class AppChatRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    message: str
    session_id: str | None = Field(default=None, alias="sessionId")
    request_id: str | None = Field(default=None, alias="requestId")
    stream: bool = False


class AppChatStopRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    session_id: str = Field(alias="sessionId")
    request_id: str | None = Field(default=None, alias="requestId")
    grace_seconds: float | None = Field(default=None, alias="graceSeconds")
    reason: str | None = None


class AppChatStopResponse(CamelBaseModel):
    accepted: bool
    session_id: str = Field(alias="sessionId")
    stopped_request_count: int = Field(alias="stoppedRequestCount")
    dropped_request_count: int = Field(alias="droppedRequestCount")
    active_request_ids: list[str] = Field(default_factory=list, alias="activeRequestIds")
    dropped_request_ids: list[str] = Field(default_factory=list, alias="droppedRequestIds")
    active_turn_ids: list[str] = Field(default_factory=list, alias="activeTurnIds")


class AppQueryRequest(PageRequest):
    id: int | None = None
    app_name: str | None = Field(default=None, alias="appName")
    cover: str | None = None
    init_prompt: str | None = Field(default=None, alias="initPrompt")
    code_gen_type: str | None = Field(default=None, alias="codeGenType")
    deploy_key: str | None = Field(default=None, alias="deployKey")
    priority: int | None = None
    user_id: int | None = Field(default=None, alias="userId")


class AppVO(LongIdModel, TimeModel):
    app_name: str | None = Field(default=None, alias="appName")
    cover: str | None = None
    init_prompt: str | None = Field(default=None, alias="initPrompt")
    code_gen_type: str | None = Field(default=None, alias="codeGenType")
    deploy_key: str | None = Field(default=None, alias="deployKey")
    deployed_time: datetime | None = Field(default=None, alias="deployedTime")
    priority: int | None = None
    user_id: int | None = Field(default=None, alias="userId")
    user: UserVO | None = None


class AppDeployResponse(CamelBaseModel):
    deploy_key: str = Field(alias="deployKey")
    deploy_url: str = Field(alias="deployUrl")
    screenshot_url: str | None = Field(default=None, alias="screenshotUrl")