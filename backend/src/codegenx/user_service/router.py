"""用户服务路由：登录 JWT 签发 / 登出 / 用户 CRUD。

合并自原网关显式路由（api/user.py）与动态 gRPC 翻译路由（dynamic_router），
进程内直调 UserService，不再经过 gRPC。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from codegenx.gateway.middleware.auth import login_and_create_token, require_login
from codegenx.gateway.middleware.jwt_auth import JWTAuth, JWTUser
from codegenx.gateway.services.jwt_revocation_service import get_revocation_service
from codegenx.user_service.user_service import UserService
from db.mysql.session import get_db_session
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.common import BaseResponse, DeleteRequest, PageData
from codegenx.user_service.schema.user import (
    LoginUserVO,
    UserAddRequest,
    UserLoginRequest,
    UserQueryRequest,
    UserRegisterRequest,
    UserUpdateRequest,
    UserVO,
)
from shared.utils.result_utils import success

router = APIRouter(prefix="/user", tags=["user"])


class LogoutAllRequest(BaseModel):
    """Request to revoke all user tokens across all devices."""
    revoke_all: bool = False


@router.post("/login", response_model=BaseResponse[str])
async def user_login(
    payload: UserLoginRequest,
    response: Response,
    db=Depends(get_db_session),
) -> BaseResponse[str]:
    user = await UserService(db).login(payload.user_account, payload.user_password)
    token = await login_and_create_token(response, user)
    return success(token)


@router.post("/register", response_model=BaseResponse[str])
async def user_register(
    payload: UserRegisterRequest,
    db=Depends(get_db_session),
) -> BaseResponse[str]:
    user_id = await UserService(db).register(
        payload.user_account,
        payload.user_password,
        payload.check_password,
        payload.user_name,
    )
    return success(str(user_id))


@router.post("/logout", response_model=BaseResponse[bool])
async def user_logout(
    request: Request,
    body: LogoutAllRequest = LogoutAllRequest(),
    jwt_user: JWTUser = Depends(require_login),
) -> BaseResponse[bool]:
    """Logout — revoke current token. Set revoke_all=true to revoke all device tokens."""
    token = JWTAuth.extract_token_from_request(request)
    if token is None:
        raise BusinessException(ErrorCode.NOT_LOGIN_ERROR, "未登录")

    revoke_svc = await get_revocation_service()

    if body.revoke_all:
        # Mass revocation: bump token_version, invalidating ALL user tokens
        await revoke_svc.revoke_all_user_tokens(jwt_user.user_id)
    else:
        # Single token revocation
        await revoke_svc.revoke_token(jwt_user.user_id, token)

    return success(True)


@router.get("/get/login", response_model=BaseResponse[LoginUserVO])
async def get_login(
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[LoginUserVO]:
    user = await UserService(db).get_by_id(jwt_user.user_id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "用户不存在")
    return success(LoginUserVO.model_validate(user))


@router.get("/get", response_model=BaseResponse[UserVO])
async def get_user_by_id(
    id: int = Query(...),
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[UserVO]:
    user = await UserService(db).get_by_id(id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "用户不存在")
    return success(UserService.to_user_vo(user))


@router.get("/get/vo", response_model=BaseResponse[UserVO])
async def get_user_vo_by_id(
    id: int = Query(...),
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[UserVO]:
    user = await UserService(db).get_by_id(id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "用户不存在")
    return success(UserService.to_user_vo(user))


@router.post("/add", response_model=BaseResponse[str])
async def add_user(
    payload: UserAddRequest,
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[str]:
    user_id = await UserService(db).add_user(
        payload.user_account,
        payload.user_password or "",
        payload.user_name,
        payload.user_avatar,
        payload.user_profile,
        payload.user_role,
    )
    return success(str(user_id))


@router.post("/update", response_model=BaseResponse[bool])
async def update_user(
    payload: UserUpdateRequest,
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[bool]:
    ok = await UserService(db).update_user(
        payload.id,
        payload.user_name,
        payload.user_avatar,
        payload.user_profile,
        payload.user_role,
    )
    return success(ok)


@router.post("/delete", response_model=BaseResponse[bool])
async def delete_user(
    payload: DeleteRequest,
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[bool]:
    ok = await UserService(db).delete_user(payload.id)
    return success(ok)


@router.post("/list/page/vo", response_model=BaseResponse[PageData[UserVO]])
async def list_user_vo_page(
    payload: UserQueryRequest,
    jwt_user: JWTUser = Depends(require_login),
    db=Depends(get_db_session),
) -> BaseResponse[PageData[UserVO]]:
    page_data = await UserService(db).list_user_vo_page(
        payload.page_num,
        payload.page_size,
        user_id=payload.id,
        user_account=payload.user_account,
        user_name=payload.user_name,
        user_profile=payload.user_profile,
        user_role=payload.user_role,
        sort_field=payload.sort_field,
        sort_order=payload.sort_order,
    )
    return success(page_data)
