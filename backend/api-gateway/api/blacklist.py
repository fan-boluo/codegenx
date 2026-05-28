from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis

from infra.redis.redis_client import get_redis_client
from middleware.auth import require_role, JWTUser
from services.blacklist_service import BlacklistService
from shared.constants import UserRole
from shared.schema.blacklist import BlacklistRequest
from shared.schema.common import BaseResponse
from shared.utils.result_utils import success

router = APIRouter(prefix="/admin/blacklist", tags=["blacklist"])


@router.get("/list", response_model=BaseResponse[set[str]])
async def get_blacklist(
    redis: Redis = Depends(get_redis_client),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[set[str]]:
    del login_user
    data = await BlacklistService(redis).get_all_blacklist()
    return success(data)


@router.post("/add", response_model=BaseResponse[bool])
async def add_to_blacklist(
    payload: BlacklistRequest,
    redis: Redis = Depends(get_redis_client),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[bool]:
    del login_user
    await BlacklistService(redis).add_to_blacklist(payload.ip, payload.reason)
    return success(True)


@router.post("/remove", response_model=BaseResponse[bool])
async def remove_from_blacklist(
    payload: BlacklistRequest,
    redis: Redis = Depends(get_redis_client),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[bool]:
    del login_user
    await BlacklistService(redis).remove_from_blacklist(payload.ip)
    return success(True)


@router.get("/check", response_model=BaseResponse[bool])
async def check_blacklist(
    ip: str = Query(...),
    redis: Redis = Depends(get_redis_client),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[bool]:
    del login_user
    blocked = await BlacklistService(redis).is_blocked(ip)
    return success(blocked)


@router.get("/count", response_model=BaseResponse[int])
async def get_blacklist_count(
    redis: Redis = Depends(get_redis_client),
    login_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
) -> BaseResponse[int]:
    del login_user
    count = await BlacklistService(redis).get_blacklist_count()
    return success(count)
