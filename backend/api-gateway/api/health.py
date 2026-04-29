"""Health API."""

from __future__ import annotations

from fastapi import APIRouter

from shared.utils.result_utils import success
from shared.schema.common import BaseResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/", response_model=BaseResponse[str])
async def health_check() -> BaseResponse[str]:
    return success("ok")