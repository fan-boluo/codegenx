from __future__ import annotations

from datetime import datetime

from pydantic import Field

from shared.schema.common import CamelBaseModel, LongIdModel, PageRequest, TimeModel


class AppAddRequest(CamelBaseModel):
    app_name: str = Field(alias="appName")
    init_prompt: str = Field(alias="initPrompt")
    db_name: str | None = Field(default=None, alias="dbName")


class AppUpdateRequest(CamelBaseModel):
    id: int
    app_name: str | None = Field(default=None, alias="appName")
    init_prompt: str | None = Field(default=None, alias="initPrompt")


class AppAdminUpdateRequest(CamelBaseModel):
    id: int
    app_name: str | None = Field(default=None, alias="appName")
    init_prompt: str | None = Field(default=None, alias="initPrompt")



class AppChatRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    message: str
    session_id: str | None = Field(default=None, alias="sessionId")
    request_id: str | None = Field(default=None, alias="requestId")
    stream: bool = False


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
    user_name: str | None = Field(default=None, alias="userName")
    db_name: str | None = Field(default=None, alias="dbName")


class AppDeployResponse(CamelBaseModel):
    deploy_key: str = Field(alias="deployKey")
    deploy_url: str = Field(alias="deployUrl")
    screenshot_url: str | None = Field(default=None, alias="screenshotUrl")