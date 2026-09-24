"""网关内置路由 — 健康检查。"""

from __future__ import annotations

from fastapi import APIRouter

from shared.utils.result_utils import success

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/")
async def health_check():
    return success("ok")


@router.get("")
async def health_check_no_slash():
    return success("ok")
