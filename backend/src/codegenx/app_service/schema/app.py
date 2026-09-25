from __future__ import annotations

from datetime import datetime

from pydantic import Field

from shared.schema.common import CamelBaseModel, LongIdModel, PageRequest, TimeModel


class AppAddRequest(CamelBaseModel):
    app_name: str = Field(alias="appName")
    db_name: str | None = Field(default=None, alias="dbName")


class AppUpdateRequest(CamelBaseModel):
    id: str  # app_xxxx 前缀字符串
    app_name: str | None = Field(default=None, alias="appName")


class AppAdminUpdateRequest(CamelBaseModel):
    id: str  # app_xxxx 前缀字符串
    app_name: str | None = Field(default=None, alias="appName")


class AppQueryRequest(PageRequest):
    id: str | None = None
    app_name: str | None = Field(default=None, alias="appName")


class AppVO(LongIdModel, TimeModel):
    app_name: str | None = Field(default=None, alias="appName")
    owner: str | None = None  # 属主用户ID（user_xxxx）
    owner_name: str | None = Field(default=None, alias="ownerName")  # 属主用户名，前端直接展示
    db_name: str | None = Field(default=None, alias="dbName")


# ---------------------- 项目成员管理 ----------------------

class AppMemberAddRequest(CamelBaseModel):
    app_id: str = Field(alias="appId")
    user_id: str | None = Field(default=None, alias="userId")
    # 邀请时可传用户账号，二选一
    user_account: str | None = Field(default=None, alias="userAccount")


class AppMemberRemoveRequest(CamelBaseModel):
    app_id: str = Field(alias="appId")
    user_id: str = Field(alias="userId")


class AppMemberVO(CamelBaseModel):
    app_id: str = Field(default=None, alias="appId")
    user_id: str = Field(default=None, alias="userId")
    user_name: str | None = Field(default=None, alias="userName")
    user_account: str | None = Field(default=None, alias="userAccount")
    create_time: datetime | None = Field(default=None, alias="createTime")
