"""User API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from shared.utils.tools import camel_to_snake
from middleware.auth import require_role, require_login, login_and_create_token
from middleware.jwt_auth import JWTUser
from shared.utils.result_utils import success
from shared.constants import DEFAULT_USER_PASSWORD,UserRole
from shared.exceptions.error_code import ErrorCode
from shared.utils.security_utils import encrypt_password
# from infra.mysql.session import get_db_session
from shared.exceptions.business_exception import BusinessException
# from infra.redis.redis_client import get_redis_client
from shared.models.user import User
from proxy.user_proxy import UserProxy
from shared.schema.common import BaseResponse, DeleteRequest, PageData
from shared.schema.user import UserAddRequest, UserLoginRequest, UserRawVO, UserVO, LoginUserVO, UserRegisterRequest, \
    UserUpdateRequest, UserQueryRequest

router = APIRouter(prefix="/user", tags=["user"])

"""
用户登录鉴权过程：

"""
@router.post("/register", response_model=BaseResponse[str])
async def user_register(
    payload: UserRegisterRequest,
) -> BaseResponse[str]:
    proxy = UserProxy()
    user_id = await proxy.register(
        payload.user_account,
        payload.user_password,
        payload.check_password,
        payload.user_name,
    )
    return success(str(user_id))

@router.post("/login", response_model=BaseResponse[str])
async def user_login(
    payload: UserLoginRequest,
    response: Response,
) -> BaseResponse[str]:
    proxy = UserProxy()
    user_dict = await proxy.login(payload.user_account, payload.user_password)
    user_dict = camel_to_snake(user_dict)
    user = User(**user_dict)
    token = await login_and_create_token(response, user)
    return success(token)

@router.get("/get/login", response_model=BaseResponse[LoginUserVO])
async def get_login(
    jwt_user: JWTUser = Depends(require_login),
) -> BaseResponse[LoginUserVO]:
    proxy = UserProxy()
    user_dict = await proxy.get_user(jwt_user.user_id)
    if not user_dict:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "用户不存在")
    user_dict = camel_to_snake(user_dict)
    user = User(**user_dict)
    return success(LoginUserVO.model_validate(user))

@router.post("/logout", response_model=BaseResponse[bool])
async def user_logout() -> BaseResponse[bool]:
    # JWT 无状态，登出由前端处理
    return success(True)

@router.post("/add", response_model=BaseResponse[str])
async def add_user(
    payload: UserAddRequest,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[str]:
    proxy = UserProxy()
    user_id = await proxy.add_user(
        user_account=payload.user_account,
        user_password=encrypt_password(DEFAULT_USER_PASSWORD),
        user_name=payload.user_name,
        user_avatar=payload.user_avatar,
        user_profile=payload.user_profile,
        user_role=payload.user_role,
    )
    return success(str(user_id))

@router.get("/get", response_model=BaseResponse[UserRawVO])
async def get_user_by_id(
    id: int,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[UserRawVO]:
    if id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "请求参数错误")
    proxy = UserProxy()
    user_dict = await proxy.get_user(id)
    if not user_dict or user_dict.get("id") == 0:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "请求数据不存在")
    return success(UserRawVO.model_validate(user_dict))

@router.get("/get/vo", response_model=BaseResponse[UserVO])
async def get_user_vo_by_id(
    id: int,
) -> BaseResponse[UserVO]:
    if id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "请求参数错误")
    proxy = UserProxy()
    user_dict = await proxy.get_user(id)
    if not user_dict or user_dict.get("id") == 0:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "请求数据不存在")
    return success(UserVO.model_validate(user_dict))

@router.post("/delete", response_model=BaseResponse[bool])
async def delete_user(
    payload: DeleteRequest,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[bool]:
    if payload.id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "请求参数错误")
    proxy = UserProxy()
    ok = await proxy.delete_user(payload.id)
    return success(ok)

@router.post("/update", response_model=BaseResponse[bool])
async def update_user(
    payload: UserUpdateRequest,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[bool]:
    proxy = UserProxy()
    ok = await proxy.update_user(
        user_id=payload.id,
        user_name=payload.user_name,
        user_avatar=payload.user_avatar,
        user_profile=payload.user_profile,
        user_role=payload.user_role,
    )
    return success(ok)

@router.post("/list/page/vo", response_model=BaseResponse[PageData[UserVO]])
async def list_user_vo_by_page(
    payload: UserQueryRequest,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[PageData[UserVO]]:
    proxy = UserProxy()
    page_dict = await proxy.list_users_page(
        page_num=payload.page_num,
        page_size=payload.page_size,
        user_id=payload.id,
        user_account=payload.user_account,
        user_name=payload.user_name,
        user_profile=payload.user_profile,
        user_role=payload.user_role,
        sort_field=payload.sort_field,
        sort_order=payload.sort_order,
    )
    page_data = PageData[UserVO].model_validate(page_dict)
    return success(page_data)

