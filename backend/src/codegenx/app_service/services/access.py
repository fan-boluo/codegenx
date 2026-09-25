"""项目权限判定收口：owner / member / admin 三级角色。

- owner：app.owner（项目创建人）
- member：app_member 表中未删除记录（本表只存普通成员，无 role 列）
- admin：平台管理员（user.userRole = admin）

参与类操作（查看/对话/文件读写/运行脚本）：owner/member/admin 均可；
管理类操作（改名/成员管理/删除项目）：仅 owner/admin。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.throw_utils import ThrowUtils
from codegenx.app_service.orm.app import App
from codegenx.app_service.orm.app_member import AppMember
from codegenx.gateway.middleware.jwt_auth import JWTUser


async def get_active_member(db: AsyncSession, app_id: str, user_id: str) -> AppMember | None:
    """查询未删除的成员记录。"""
    result = await db.execute(
        select(AppMember).where(
            AppMember.app_id == app_id,
            AppMember.user_id == user_id,
            AppMember.is_delete == 0,
        )
    )
    return result.scalars().first()


async def get_access_role(db: AsyncSession, app: App, login_user: JWTUser) -> str | None:
    """判定当前用户对项目的角色：owner / member / admin；非参与者返回 None。"""
    if login_user.user_role == "admin":
        return "admin"
    if app.owner == login_user.user_id:
        return "owner"
    if await get_active_member(db, app.id, login_user.user_id) is not None:
        return "member"
    return None


async def require_participant(db: AsyncSession, app: App, login_user: JWTUser) -> str:
    """参与类操作校验：owner/member/admin 均可，非成员拒绝。"""
    role = await get_access_role(db, app, login_user)
    ThrowUtils.throw_if(role is None, ErrorCode.NO_AUTH_ERROR, "无权限访问该应用")
    return role


async def require_manager(db: AsyncSession, app: App, login_user: JWTUser) -> str:
    """管理类操作校验：仅 owner/admin。"""
    role = await get_access_role(db, app, login_user)
    ThrowUtils.throw_if(role not in {"owner", "admin"}, ErrorCode.NO_AUTH_ERROR, "仅项目属主或管理员可执行该操作")
    return role


async def require_participant_by_id(db: AsyncSession, app_id: str, login_user: JWTUser) -> App:
    """按项目 id 取记录并校验参与权限（gateway chat / 会话历史接口共用）。"""
    if not app_id:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "appId 错误")
    app = await db.get(App, app_id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "项目不存在")
    if await get_access_role(db, app, login_user) is None:
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限访问该项目")
    return app
