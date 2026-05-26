"""Chat history routes forwarded by gateway to app-service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query

from proxy.app_proxy import AppProxy


router = APIRouter(prefix="/chatHistory", tags=["chatHistory"])


@router.get("/app/{app_id}")
async def list_app_chat_history(
    app_id: int,
    page_size: int = Query(default=10, alias="pageSize"),
    last_create_time: str | None = Query(default=None, alias="lastCreateTime"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    params: dict[str, Any] = {"page_size": page_size}
    if last_create_time:
        params["last_create_time"] = last_create_time

    proxy = AppProxy()
    return await proxy.request_json(
        method="GET",
        path=f"/api/chatHistory/app/{app_id}",
        authorization=authorization,
        params=params,
    )


@router.post("/admin/list/page/vo")
async def list_all_chat_history_by_page_for_admin(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/chatHistory/admin/list/page/vo",
        authorization=authorization,
        json_body=payload,
    )
