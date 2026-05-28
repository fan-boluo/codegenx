"""App-facing gateway routes forwarded to app-service or ai-service."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response

from infra.redis.redis_client import get_redis_client
from middleware.auth import require_login
from middleware.jwt_auth import JWTUser
from proxy.app_proxy import AppProxy
from proxy.chat_proxy import ChatProxy
from services.rate_limit_service import RateLimitService


router = APIRouter(prefix="/app", tags=["app"])


@router.post("/add")
@router.post("/create")
async def add_app(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/create",
        authorization=authorization,
        json_body=payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/chat/gen/code")
async def chat_to_gen_code_post(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    login_user: JWTUser = Depends(require_login),
    redis: "Redis" = Depends(get_redis_client),  # type: ignore[type-arg]
):
    await RateLimitService(redis).check_user_rate_limit(
        login_user.user_id, "gen_code", 5
    )

    if "userId" not in payload:
        payload["userId"] = str(login_user.user_id)

    proxy = ChatProxy()
    return await proxy.stream_sse(
        method="POST",
        path="/api/ai/codegen/stream",
        authorization=authorization,
        trace_id=getattr(request.state, "trace_id", None),
        json_body=payload,
    )


@router.post("/chat/stop")
async def stop_chat_generation(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    login_user: JWTUser = Depends(require_login),
):
    proxy = ChatProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/ai/codegen/stop",
        authorization=authorization,
        trace_id=getattr(request.state, "trace_id", None),
        json_body=payload,
    )


@router.post("/deploy")
async def deploy_app(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/deploy",
        authorization=authorization,
        json_body=payload,
    )


@router.delete("")
async def delete_app(
    app_id: int = Query(alias="appId"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="DELETE",
        path="/api/app",
        authorization=authorization,
        params={"appId": app_id},
    )


@router.get("/code/tree/{app_id}")
async def get_app_code_tree(
    request: Request,
    app_id: int,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="GET",
        path=f"/api/app/code/tree/{app_id}",
        authorization=authorization,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/code/file/{app_id}")
async def get_app_code_file(
    request: Request,
    app_id: int,
    path: str = Query(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="GET",
        path=f"/api/app/code/file/{app_id}",
        authorization=authorization,
        params={"path": path},
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{app_id}")
async def get_app(
    app_id: int,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="GET",
        path=f"/api/app/{app_id}",
        authorization=authorization,
    )


@router.get("/get/vo")
async def get_app_vo(
    id: int = Query(),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="GET",
        path="/api/app/get/vo",
        authorization=authorization,
        params={"id": id},
    )


@router.get("/admin/get/vo")
async def get_app_vo_by_admin(
    id: int = Query(),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="GET",
        path="/api/app/get/vo",
        authorization=authorization,
        params={"id": id},
    )


@router.post("/update")
async def update_app(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/update",
        authorization=authorization,
        json_body=payload,
    )


@router.post("/admin/update")
async def admin_update_app(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/admin/update",
        authorization=authorization,
        json_body=payload,
    )


@router.post("/delete")
async def delete_app_post(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/delete",
        authorization=authorization,
        json_body=payload,
    )


@router.post("/admin/delete")
async def admin_delete_app(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/admin/delete",
        authorization=authorization,
        json_body=payload,
    )


@router.post("/my/list/page/vo")
async def list_my_apps(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/my/list/page/vo",
        authorization=authorization,
        json_body=payload,
    )


@router.post("/good/list/page/vo")
async def list_good_apps(payload: dict[str, Any]):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/good/list/page/vo",
        json_body=payload,
    )


@router.post("/admin/list/page/vo")
async def list_all_apps_for_admin(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    return await proxy.request_json(
        method="POST",
        path="/api/app/admin/list/page/vo",
        authorization=authorization,
        json_body=payload,
    )


@router.get("/download/{app_id}")
async def download_app_code(
    app_id: int,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    proxy = AppProxy()

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        upstream_url = await proxy.build_url(f"/api/app/download/{app_id}")
        response = await client.get(
            url=upstream_url,
            headers=headers,
        )

    passthrough_headers: dict[str, str] = {}
    content_disposition = response.headers.get("content-disposition")
    if content_disposition:
        passthrough_headers["Content-Disposition"] = content_disposition

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers=passthrough_headers,
    )


@router.get("/static/{deploy_key}")
@router.get("/static/{deploy_key}/{resource_path:path}")
async def serve_static_resource(deploy_key: str, resource_path: str = ""):
    proxy = AppProxy()
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        upstream_path = f"/api/static/{deploy_key}/{resource_path}" if resource_path else f"/api/static/{deploy_key}"
        upstream_url = await proxy.build_url(upstream_path)
        response = await client.get(
            url=upstream_url,
        )

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/octet-stream"),
    )


@router.get("/preview/{code_gen_type}/{app_id}")
@router.get("/preview/{code_gen_type}/{app_id}/{resource_path:path}")
async def serve_preview_resource(code_gen_type: str, app_id: int, resource_path: str = ""):
    proxy = AppProxy()
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        upstream_path = f"/api/preview/{code_gen_type}/{app_id}/{resource_path}" if resource_path else f"/api/preview/{code_gen_type}/{app_id}"
        upstream_url = await proxy.build_url(upstream_path)
        response = await client.get(url=upstream_url)

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/octet-stream"),
    )
