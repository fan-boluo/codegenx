"""Chat history proxy routes — minimal, forward to app-service for internal cleanup only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header

from proxy.app_proxy import AppProxy


router = APIRouter(prefix="/chatHistory", tags=["chatHistory"])


@router.delete("/internal/app/{app_id}")
async def delete_chat_history_by_app_id(
    app_id: int,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="DELETE",
        path=f"/api/chatHistory/internal/app/{app_id}",
        authorization=authorization,
    )
