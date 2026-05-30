"""Chat history routes forwarded by gateway to app-service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query

from proxy.app_proxy import AppProxy


router = APIRouter(prefix="/chatHistory", tags=["chatHistory"])


@router.get("/app/{app_id}")
async def list_app_chat_history(
    app_id: int,
    session_id: str = Query(default="", alias="sessionId"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    params: dict[str, Any] = {"session_id": session_id}

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
