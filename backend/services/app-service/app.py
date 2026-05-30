"""App Service API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path as _Path
import shutil
from pathlib import Path
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
LOCAL_SERVICES_ROOT = Path(__file__).resolve().parent / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth_proxy import JWTUser, require_login, require_role
from core.service_registry import AppServiceRegistry
from infra.mysql.session import get_db_session
from shared.config.log_config import log
from shared.constants import UserRole
from shared.schema.app import AppAddRequest, AppAdminUpdateRequest, AppDeployRequest, AppQueryRequest, AppUpdateRequest, AppVO
from shared.schema.common import BaseResponse, DeleteRequest, PageData
from shared.utils.result_utils import success
from app_service import AppService
from static_service import StaticResourceService
from chat_history_api import router as chat_history_router

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await service_registry.startup()
    yield
    await service_registry.shutdown()


app = FastAPI(title="CodeGenX App Service", version="1.0.0", lifespan=_lifespan)
static_resource_service = StaticResourceService()
service_registry = AppServiceRegistry()
app.include_router(chat_history_router)


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        request.state.trace_id = trace_id
        log.info(
            "app-service request start traceId={} method={} path={} client={}",
            trace_id,
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        log.info(
            "app-service request end traceId={} method={} path={} status={}",
            trace_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response


app.add_middleware(TraceIdMiddleware)


@app.post("/api/app/create", response_model=BaseResponse[int])
async def add_app(
    payload: AppAddRequest,
    http_request: Request,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[int]:
    trace_id = getattr(http_request.state, "trace_id", None)
    log.info(
        "app-service add app request traceId={} userId={} promptLen={} preview={}",
        trace_id,
        current_user.user_id,
        len(payload.init_prompt),
        _preview_text(payload.init_prompt),
    )
    try:
        app_id = await AppService(db).create_app(payload, current_user, trace_id=trace_id)
        log.info("app-service add app completed traceId={} userId={} appId={}", trace_id, current_user.user_id, app_id)
        return success(app_id)
    except Exception as exc:
        log.exception("add app failed traceId={} userId={}", trace_id, current_user.user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/deploy", response_model=BaseResponse[dict])
async def deploy_app(
    request: AppDeployRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[dict]:
    try:
        result = await AppService(db).deploy_app(request, current_user)
        return success(result)
    except Exception as exc:
        log.exception("deploy app failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/app", response_model=BaseResponse[bool])
async def delete_app(
    app_id: int = Query(alias="appId"),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        result = await AppService(db).delete_app(app_id, current_user)
        return success(result)
    except Exception as exc:
        log.exception("delete app failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/app/{app_id}", response_model=BaseResponse[dict | None])
async def get_app(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[dict | None]:
    try:
        result = await AppService(db).get_app_vo_by_id(app_id, current_user)
        return success(result)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("get app failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/app/get/vo", response_model=BaseResponse[AppVO])
async def get_app_vo(
    id: int = Query(),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[AppVO]:
    try:
        return success(await AppService(db).get_app_vo_by_id(id, current_user))
    except Exception as exc:
        log.exception("get app vo failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/update", response_model=BaseResponse[bool])
async def update_app(
    request: AppUpdateRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).update_app(request, current_user))
    except Exception as exc:
        log.exception("update app failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/admin/update", response_model=BaseResponse[bool])
async def admin_update_app(
    request: AppAdminUpdateRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).admin_update_app(request, current_user))
    except Exception as exc:
        log.exception("admin update app failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/delete", response_model=BaseResponse[bool])
async def delete_app_post(
    request: DeleteRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).delete_app(request.id, current_user))
    except Exception as exc:
        log.exception("delete app post failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/admin/delete", response_model=BaseResponse[bool])
async def admin_delete_app(
    request: DeleteRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).delete_app_by_admin(request.id, current_user))
    except Exception as exc:
        log.exception("admin delete app failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/my/list/page/vo", response_model=BaseResponse[PageData[AppVO]])
async def list_my_apps(
    request: AppQueryRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[PageData[AppVO]]:
    try:
        return success(await AppService(db).list_my_app_vo_by_page(request, current_user))
    except Exception as exc:
        log.exception("list my apps failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/good/list/page/vo", response_model=BaseResponse[PageData[AppVO]])
async def list_good_apps(
    request: AppQueryRequest,
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[PageData[AppVO]]:
    try:
        return success(await AppService(db).list_good_app_vo_by_page(request))
    except Exception as exc:
        log.exception("list good apps failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/app/admin/list/page/vo", response_model=BaseResponse[PageData[AppVO]])
async def list_all_apps_for_admin(
    request: AppQueryRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[PageData[AppVO]]:
    try:
        return success(await AppService(db).list_all_app_vo_by_page(request, current_user))
    except Exception as exc:
        log.exception("admin list apps failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/app/download/{app_id}")
async def download_app_code(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        zip_path = await AppService(db).download_app_code(app_id, current_user)
        background = BackgroundTask(_cleanup_temp_archive, zip_path)
        return FileResponse(path=str(zip_path), filename=f"{app_id}.zip", media_type="application/zip", background=background)
    except Exception as exc:
        log.exception("download app code failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/app/code/tree/{app_id}", response_model=BaseResponse[list])
async def get_app_code_tree(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[list]:
    try:
        tree = await AppService(db).get_code_tree(app_id, current_user)
        return success(tree)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("get app code tree failed appId={}", app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/app/code/file/{app_id}", response_model=BaseResponse[str])
async def get_app_code_file(
    app_id: int,
    path: str = Query(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[str]:
    try:
        content = await AppService(db).get_code_file(app_id, path, current_user)
        return success(content)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("get app code file failed appId={} path={}", app_id, path)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/static/{deploy_key}")
@app.get("/api/static/{deploy_key}/{resource_path:path}")
async def serve_static_resource(deploy_key: str, resource_path: str = ""):
    try:
        file_path, media_type = static_resource_service.resolve_resource(deploy_key, resource_path)
        return FileResponse(path=str(file_path), media_type=media_type)
    except Exception as exc:
        log.exception("serve static resource failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/preview/{app_id}")
@app.get("/api/preview/{app_id}/{resource_path:path}")
async def serve_preview_resource(app_id: int, resource_path: str = ""):
    try:
        file_path, media_type = static_resource_service.resolve_preview_resource(app_id, resource_path)
        return FileResponse(path=str(file_path), media_type=media_type)
    except Exception as exc:
        log.exception("serve preview resource failed appId={} resourcePath={}", app_id, resource_path)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/app/db/tables/{app_id}", response_model=BaseResponse[list])
async def get_app_db_tables(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[list]:
    try:
        tables = await AppService(db).get_db_tables(app_id, current_user)
        return success(tables)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("get app db tables failed appId={}", app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _cleanup_temp_archive(zip_path: _Path) -> None:
    try:
        temp_dir = zip_path.parent
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        log.exception("cleanup temp archive failed")


def _preview_text(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
