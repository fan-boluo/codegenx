"""
鉴权认证：JWT 版本
复用你原有所有逻辑、结构、风格
只是把 Redis Session 换成 JWT
"""
from fastapi import Request, Response, Depends, HTTPException
from pydantic import BaseModel
from enum import Enum

from middleware.jwt_auth import JWTAuth,JWTUser

from shared.constants import UserRole
# 你的错误码、角色枚举
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.business_exception import BusinessException


# ============== 登录：返回 Token，不种 Cookie ==============
async def login_and_create_token(response: Response, user) -> str:
    """
    登录成功：生成 JWT，返回给前端
    不再使用 session + redis
    """
    jwt_user = JWTUser(
        user_id=user.id,
        user_account=user.user_account,
        user_role=user.user_role
    )
    token = JWTAuth.create_token(jwt_user)
    return token  # 返回给前端存储


# ============== 核心：从 JWT 获取当前用户 ==============
async def get_login_user(
        request: Request,
) -> JWTUser:
    try:
        # 从 Header 取 token，而不是 Cookie
        return await JWTAuth.get_current_user(request)
    except HTTPException as e:
        raise BusinessException(ErrorCode.NOT_LOGIN_ERROR, "未登录")


# ============== 你原来的依赖注入：完全不变！==============
async def require_login(
        request: Request,
) -> JWTUser:
    """登录校验：接口直接用 Depends(require_login)"""
    return await get_login_user(request)


# ============== 你原来的角色校验：完全不变！==============
def require_role(must_role: UserRole):
    async def checker(
            request: Request,
    ) -> JWTUser:
        login_user = await get_login_user(request)

        # 权限判断逻辑完全复用
        if must_role == UserRole.ADMIN and login_user.user_role != UserRole.ADMIN.value:
            raise BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限")

        return login_user

    return checker