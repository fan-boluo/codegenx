"""App-facing gateway routes forwarded to app-service or ai-service."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, File, Header, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from proxy.app_proxy import AppProxy


router = APIRouter(prefix="/app", tags=["app"])


# ---------------------------------------------------------------------------
# Special routes that cannot use catch-all passthrough
# (multipart upload, SSE streaming, or binary file response)
# ---------------------------------------------------------------------------


@router.post("/code/file/upload/{app_id}")
async def upload_app_code_file(
    request: Request,
    app_id: int,
    path: str = Query(...),
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    form_data = {"file": (file.filename, await file.read(), file.content_type or "application/octet-stream")}
    return await proxy.request_form(
        method="POST",
        path=f"/api/app/code/file/upload/{app_id}",
        authorization=authorization,
        params={"path": path},
        files=form_data,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/code/run/{app_id}")
async def run_app_code_script(
    request: Request,
    app_id: int,
    payload: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Stream script execution output back to caller (SSE)."""
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    proxy = AppProxy()
    base_url = await proxy.resolve_base_url()
    url = f"{base_url}/api/app/code/run/{app_id}"

    async def stream():
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream")


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
        response = await client.get(url=upstream_url, headers=headers)

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


# ---------------------------------------------------------------------------
# Catch-all passthrough
# Any other /api/app/... request is forwarded to app-service as-is.
# Adding new app-service endpoints no longer requires gateway changes.
# ---------------------------------------------------------------------------


@router.api_route("/{rest_of_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def app_service_passthrough(
    request: Request,
    rest_of_path: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    proxy = AppProxy()
    params: dict[str, Any] = {}
    for key, value in request.query_params.items():
        params[key] = value
    body: dict[str, Any] | None = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
        except Exception:
            body = None
    return await proxy.forward_passthrough(
        method=request.method,
        path=f"/api/app/{rest_of_path}",
        authorization=authorization,
        params=params,
        json_body=body,
        trace_id=getattr(request.state, "trace_id", None),
    )
