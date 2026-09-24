"""应用服务路由：应用 CRUD + 生成代码文件管理 + 脚本执行。

由原 app-service/app.py 提取（去 Nacos 注册 / TraceId 中间件 / auth_proxy 文件路径加载），
认证直接复用网关 auth 依赖。
"""

from __future__ import annotations

from pathlib import Path as _Path
import shutil
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.ext.asyncio import AsyncSession

from codegenx.app_service.services.app_service import AppService
from codegenx.gateway.middleware.auth import JWTUser, require_login, require_role
from db.mysql.session import get_db_session
from shared import log
from codegenx.user_service.user_enums import UserRole
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from codegenx.app_service.schema.app import AppAddRequest, AppAdminUpdateRequest, AppQueryRequest, AppUpdateRequest, AppVO
from shared.schema.common import BaseResponse, DeleteRequest, PageData
from shared.utils.result_utils import success

router = APIRouter(prefix="/app", tags=["app"])


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


def _system_error(exc: Exception) -> BusinessException:
    return BusinessException(ErrorCode.SYSTEM_ERROR, str(exc))


@router.post("/create", response_model=BaseResponse[int])
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
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("add app failed traceId={} userId={}", trace_id, current_user.user_id)
        raise _system_error(exc) from exc


@router.delete("", response_model=BaseResponse[bool])
async def delete_app(
    app_id: int = Query(alias="appId"),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        result = await AppService(db).delete_app(app_id, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("delete app failed")
        raise _system_error(exc) from exc


@router.get("/{app_id}", response_model=BaseResponse[dict | None])
async def get_app(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[dict | None]:
    try:
        result = await AppService(db).get_app_vo_by_id(app_id, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("get app failed")
        raise _system_error(exc) from exc


@router.get("/get/vo", response_model=BaseResponse[AppVO])
async def get_app_vo(
    id: int = Query(),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[AppVO]:
    try:
        return success(await AppService(db).get_app_vo_by_id(id, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("get app vo failed")
        raise _system_error(exc) from exc


@router.post("/update", response_model=BaseResponse[bool])
async def update_app(
    request: AppUpdateRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).update_app(request, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("update app failed")
        raise _system_error(exc) from exc


@router.post("/admin/update", response_model=BaseResponse[bool])
async def admin_update_app(
    request: AppAdminUpdateRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).admin_update_app(request, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("admin update app failed")
        raise _system_error(exc) from exc


@router.post("/delete", response_model=BaseResponse[bool])
async def delete_app_post(
    request: DeleteRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).delete_app(request.id, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("delete app post failed")
        raise _system_error(exc) from exc


@router.post("/admin/delete", response_model=BaseResponse[bool])
async def admin_delete_app(
    request: DeleteRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        return success(await AppService(db).delete_app_by_admin(request.id, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("admin delete app failed")
        raise _system_error(exc) from exc


@router.post("/my/list/page/vo", response_model=BaseResponse[PageData[AppVO]])
async def list_my_apps(
    request: AppQueryRequest,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[PageData[AppVO]]:
    try:
        return success(await AppService(db).list_my_app_vo_by_page(request, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("list my apps failed")
        raise _system_error(exc) from exc


@router.post("/good/list/page/vo", response_model=BaseResponse[PageData[AppVO]])
async def list_good_apps(
    request: AppQueryRequest,
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[PageData[AppVO]]:
    try:
        return success(await AppService(db).list_good_app_vo_by_page(request))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("list good apps failed")
        raise _system_error(exc) from exc


@router.post("/admin/list/page/vo", response_model=BaseResponse[PageData[AppVO]])
async def list_all_apps_for_admin(
    request: AppQueryRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[PageData[AppVO]]:
    try:
        return success(await AppService(db).list_all_app_vo_by_page(request, current_user))
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("admin list apps failed")
        raise _system_error(exc) from exc


@router.get("/download/{app_id}")
async def download_app_code(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        zip_path = await AppService(db).download_app_code(app_id, current_user)
        background = BackgroundTask(_cleanup_temp_archive, zip_path)
        return FileResponse(path=str(zip_path), filename=f"{app_id}.zip", media_type="application/zip", background=background)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("download app code failed")
        raise _system_error(exc) from exc


@router.get("/code/tree/{app_id}", response_model=BaseResponse[list])
async def get_app_code_tree(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[list]:
    try:
        tree = await AppService(db).get_code_tree(app_id, current_user)
        return success(tree)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("get app code tree failed appId={}", app_id)
        raise _system_error(exc) from exc


@router.get("/code/file/{app_id}", response_model=BaseResponse[str])
async def get_app_code_file(
    app_id: int,
    path: str = Query(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[str]:
    try:
        content = await AppService(db).get_code_file(app_id, path, current_user)
        return success(content)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("get app code file failed appId={} path={}", app_id, path)
        raise _system_error(exc) from exc


@router.post("/code/file/{app_id}", response_model=BaseResponse[bool])
async def save_app_code_file(
    app_id: int,
    path: str = Query(...),
    payload: dict[str, Any] = Body(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        content = payload.get("content", "")
        result = await AppService(db).save_code_file(app_id, path, content, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("save app code file failed appId={} path={}", app_id, path)
        raise _system_error(exc) from exc


@router.post("/code/file/create/{app_id}", response_model=BaseResponse[bool])
async def create_app_code_file(
    app_id: int,
    path: str = Query(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        result = await AppService(db).create_file(app_id, path, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("create app code file failed appId={} path={}", app_id, path)
        raise _system_error(exc) from exc


@router.post("/code/folder/create/{app_id}", response_model=BaseResponse[bool])
async def create_app_code_folder(
    app_id: int,
    path: str = Query(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        result = await AppService(db).create_folder(app_id, path, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("create app code folder failed appId={} path={}", app_id, path)
        raise _system_error(exc) from exc


@router.post("/code/file/upload/{app_id}", response_model=BaseResponse[bool])
async def upload_app_code_file(
    app_id: int,
    path: str = Query(...),
    file: UploadFile = File(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        content = await file.read()
        result = await AppService(db).upload_file(app_id, path, content, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("upload app code file failed appId={} path={}", app_id, path)
        raise _system_error(exc) from exc


@router.delete("/code/{app_id}", response_model=BaseResponse[bool])
async def delete_app_code_node(
    app_id: int,
    path: str = Query(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        result = await AppService(db).delete_node(app_id, path, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("delete app code node failed appId={} path={}", app_id, path)
        raise _system_error(exc) from exc


@router.post("/code/rename/{app_id}", response_model=BaseResponse[bool])
async def rename_app_code_node(
    app_id: int,
    from_param: str = Query(..., alias="from"),
    to: str = Query(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        result = await AppService(db).rename_node(app_id, from_param, to, current_user)
        return success(result)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("rename app code node failed appId={} from={} to={}", app_id, from_param, to)
        raise _system_error(exc) from exc


@router.post("/code/run/{app_id}")
async def run_app_code_script(
    app_id: int,
    payload: dict[str, Any] = Body(...),
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        file_path = payload.get("path", "")
        env = payload.get("env", "model")
        return StreamingResponse(
            AppService(db).run_script(app_id, file_path, env, current_user),
            media_type="text/event-stream",
        )
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("run app code script failed appId={} path={}", app_id, payload.get("path", ""))
        raise _system_error(exc) from exc


@router.get("/db/tables/{app_id}", response_model=BaseResponse[list])
async def get_app_db_tables(
    app_id: int,
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[list]:
    try:
        tables = await AppService(db).get_db_tables(app_id, current_user)
        return success(tables)
    except BusinessException:
        raise
    except Exception as exc:
        log.exception("get app db tables failed appId={}", app_id)
        raise _system_error(exc) from exc
