from __future__ import annotations

from datetime import datetime
from math import ceil
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config.config import get_settings
from shared.config.log_config import log
from shared.constants import CODE_DEPLOY_HOST, DEFAULT_APP_PRIORITY, get_bot_code_dir
from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.throw_utils import ThrowUtils
from shared.orm.app import App
from shared.schema.app import AppAddRequest, AppAdminUpdateRequest, AppDeployRequest, AppQueryRequest, AppUpdateRequest, AppVO
from shared.schema.common import PageData
from shared.schema.code import GeneratedCodeSaveRequest

from core.ai_client import AiServiceClient
from core.auth_proxy import JWTUser
from core.chat_client import ChatServiceClient
from core.code_generator_facade import CodePersistenceFacade
from core.saver import CodeFileSaverExecutor
from core.screenshot_service import ScreenshotService


settings = get_settings()


class AppService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.ai_service_client = AiServiceClient()
        self.chat_service_client = ChatServiceClient()
        self.code_persistence_facade = CodePersistenceFacade()
        self.screenshot_service = ScreenshotService()

    async def create_app(self, app_add_request: AppAddRequest, login_user: JWTUser, trace_id: str | None = None) -> int:
        init_prompt = app_add_request.init_prompt.strip()
        ThrowUtils.throw_if(not init_prompt, ErrorCode.PARAMS_ERROR, "初始化 prompt 不能为空")
        log.info(
            "app-service create app start traceId={} userId={} promptLen={} preview={}",
            trace_id,
            login_user.user_id,
            len(init_prompt),
            _preview_text(init_prompt),
        )
        app = App(
            user_id=login_user.user_id,
            app_name=init_prompt[:12],
            init_prompt=init_prompt,
            code_gen_type="",
            priority=DEFAULT_APP_PRIORITY,
        )
        self.db.add(app)
        await self.db.commit()
        await self.db.refresh(app)
        log.info("app-service create app completed traceId={} userId={} appId={}", trace_id, login_user.user_id, app.id)
        return app.id

    async def save_generated_code(self, request: GeneratedCodeSaveRequest) -> dict[str, str | None]:
        app = await self._get_existing_app(request.app_id)
        request_type = CodeGenTypeEnum.get_enum_by_value(request.code_gen_type)
        ThrowUtils.throw_if(request.code_gen_type is not None and request_type is None, ErrorCode.PARAMS_ERROR, "应用代码生成类型错误")
        existing_type = CodeGenTypeEnum.get_enum_by_value(app.code_gen_type)
        code_gen_type = self.code_persistence_facade.detect_code_gen_type(
            request.content,
            preferred=request_type or existing_type,
        )
        log.info(
            "app-service persist code start appId={} codeGenType={} contentLen={} preview={}",
            request.app_id,
            code_gen_type.value,
            len(request.content),
            _preview_text(request.content),
        )
        persisted = self.code_persistence_facade.save_generated_code(request.content, code_gen_type, app.id)
        app.code_gen_type = code_gen_type.value
        app.edit_time = datetime.utcnow()
        preview_source_dir = Path(persisted.get("buildDir") or persisted.get("savedDir") or "")
        if preview_source_dir:
            deploy_key = app.deploy_key or secrets.token_urlsafe(6)[:6]
            CodeFileSaverExecutor.deploy_saved_code(preview_source_dir, deploy_key)
            app.deploy_key = deploy_key
            await self.db.commit()
            persisted["deployKey"] = deploy_key
            log.info(
                "app-service preview refreshed appId={} deployKey={} previewSourceDir={}",
                app.id,
                deploy_key,
                preview_source_dir,
            )
        log.info("app-service persist code completed appId={} result={}", app.id, persisted)
        return persisted

    async def deploy_app(self, request: AppDeployRequest, login_user: JWTUser) -> dict[str, str | None]:
        app = await self._get_owned_app(request.app_id, login_user)
        deploy_key = app.deploy_key or secrets.token_urlsafe(6)[:6]
        source_dir = get_bot_code_dir(app.id)
        ThrowUtils.throw_if(not source_dir.exists(), ErrorCode.NOT_FOUND_ERROR, "应用代码不存在，请先生成代码")
        deploy_source = source_dir
        if app.code_gen_type == CodeGenTypeEnum.VUE_PROJECT.value:
            dist_dir = source_dir / "dist"
            if not dist_dir.exists():
                dist_dir = CodeFileSaverExecutor.build_vue_project(source_dir)
            deploy_source = dist_dir
        CodeFileSaverExecutor.deploy_saved_code(deploy_source, deploy_key)
        deploy_url = self._build_deploy_url(deploy_key)
        screenshot_url = None
        try:
            screenshot_resource = self.screenshot_service.generate_and_upload_screenshot(
                self._build_internal_deploy_url(deploy_key),
                deploy_key,
            )
            screenshot_url = self._build_cover_url(deploy_key, screenshot_resource)
        except Exception as exc:
            log.warning(
                "app-service deploy screenshot skipped appId={} deployKey={} deployUrl={} error={}",
                app.id,
                deploy_key,
                deploy_url,
                exc,
            )
        app.deploy_key = deploy_key
        app.deployed_time = datetime.utcnow()
        if screenshot_url:
            app.cover = screenshot_url
        await self.db.commit()
        log.info(
            "app-service deploy completed appId={} deployKey={} deployUrl={} screenshotUrl={}",
            app.id,
            deploy_key,
            deploy_url,
            screenshot_url,
        )
        return {
            "deployKey": deploy_key,
            "deployUrl": deploy_url,
            "screenshotUrl": screenshot_url,
        }

    async def delete_app(self, app_id: int, login_user: JWTUser) -> bool:
        await self._get_owned_app(app_id, login_user)
        await self.db.execute(delete(App).where(App.id == app_id))
        await self.chat_service_client.delete_chat_history_by_app_id(app_id)
        await self.db.commit()
        return True

    async def get_app_by_id(self, app_id: int) -> App | None:
        return await self.db.get(App, app_id)

    async def get_app_vo_by_id(self, app_id: int, login_user: JWTUser) -> AppVO:
        app = await self._get_viewable_app(app_id, login_user)
        return self._to_app_vo(app)

    async def update_app(self, request: AppUpdateRequest, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(request.id, login_user)
        if request.app_name is not None:
            app.app_name = request.app_name
        app.edit_time = datetime.utcnow()
        await self.db.commit()
        return True

    async def admin_update_app(self, request: AppAdminUpdateRequest, login_user: JWTUser) -> bool:
        self._require_admin(login_user)
        app = await self._get_existing_app(request.id)
        if request.app_name is not None:
            app.app_name = request.app_name
        if request.cover is not None:
            app.cover = request.cover
        if request.priority is not None:
            app.priority = request.priority
        app.edit_time = datetime.utcnow()
        await self.db.commit()
        return True

    async def delete_app_by_admin(self, app_id: int, login_user: JWTUser) -> bool:
        self._require_admin(login_user)
        await self._delete_app_by_id(app_id)
        return True

    async def list_my_app_vo_by_page(self, query_request: AppQueryRequest, login_user: JWTUser) -> PageData[AppVO]:
        query_request.user_id = login_user.user_id
        return await self._list_app_vo_by_page(query_request)

    async def list_good_app_vo_by_page(self, query_request: AppQueryRequest) -> PageData[AppVO]:
        query_request.priority = 99
        return await self._list_app_vo_by_page(query_request)

    async def list_all_app_vo_by_page(self, query_request: AppQueryRequest, login_user: JWTUser) -> PageData[AppVO]:
        self._require_admin(login_user)
        return await self._list_app_vo_by_page(query_request)

    async def download_app_code(self, app_id: int, login_user: JWTUser) -> Path:
        app = await self._get_owned_app(app_id, login_user)
        source_dir = get_bot_code_dir(app.id)
        ThrowUtils.throw_if(not source_dir.exists(), ErrorCode.NOT_FOUND_ERROR, "应用代码不存在，请先生成代码")
        temp_dir = Path(tempfile.mkdtemp(prefix=f"app-download-{app.id}-"))
        archive_base = temp_dir / str(app.id)
        archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=source_dir)
        return Path(archive_path)

    async def _get_owned_app(self, app_id: int, login_user: JWTUser) -> App:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用 ID 错误")
        app = await self.db.get(App, app_id)
        ThrowUtils.throw_if(app is None, ErrorCode.NOT_FOUND_ERROR, "应用不存在")
        ThrowUtils.throw_if(app.user_id != login_user.user_id, ErrorCode.NO_AUTH_ERROR, "无权限访问该应用")
        return app

    async def _get_viewable_app(self, app_id: int, login_user: JWTUser) -> App:
        app = await self._get_existing_app(app_id)
        if login_user.user_role != "admin":
            ThrowUtils.throw_if(app.user_id != login_user.user_id, ErrorCode.NO_AUTH_ERROR, "无权限访问该应用")
        return app

    async def _get_existing_app(self, app_id: int) -> App:
        ThrowUtils.throw_if(app_id <= 0, ErrorCode.PARAMS_ERROR, "应用 ID 错误")
        app = await self.db.get(App, app_id)
        ThrowUtils.throw_if(app is None, ErrorCode.NOT_FOUND_ERROR, "应用不存在")
        return app

    async def _delete_app_by_id(self, app_id: int) -> None:
        await self._get_existing_app(app_id)
        await self.db.execute(delete(App).where(App.id == app_id))
        await self.chat_service_client.delete_chat_history_by_app_id(app_id)
        await self.db.commit()

    async def _list_app_vo_by_page(self, query_request: AppQueryRequest) -> PageData[AppVO]:
        page_num = query_request.page_num if query_request.page_num > 0 else 1
        page_size = query_request.page_size if 0 < query_request.page_size <= 20 else 10
        stmt = self._build_query(query_request)
        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total_row = int((await self.db.scalar(total_stmt)) or 0)
        page_stmt = stmt.offset((page_num - 1) * page_size).limit(page_size)
        result = await self.db.execute(page_stmt)
        records = [self._to_app_vo(item) for item in result.scalars().all()]
        return PageData[AppVO](
            records=records,
            pageNumber=page_num,
            pageSize=page_size,
            totalPage=ceil(total_row / page_size) if total_row else 0,
            totalRow=total_row,
            optimizeCountQuery=True,
        )

    def _build_query(self, query_request: AppQueryRequest):
        stmt = select(App)
        if query_request.id is not None:
            stmt = stmt.where(App.id == query_request.id)
        if query_request.app_name:
            stmt = stmt.where(App.app_name.like(f"%{query_request.app_name}%"))
        if query_request.cover:
            stmt = stmt.where(App.cover.like(f"%{query_request.cover}%"))
        if query_request.init_prompt:
            stmt = stmt.where(App.init_prompt.like(f"%{query_request.init_prompt}%"))
        if query_request.code_gen_type:
            stmt = stmt.where(App.code_gen_type == query_request.code_gen_type)
        if query_request.deploy_key:
            stmt = stmt.where(App.deploy_key == query_request.deploy_key)
        if query_request.priority is not None:
            stmt = stmt.where(App.priority == query_request.priority)
        if query_request.user_id is not None:
            stmt = stmt.where(App.user_id == query_request.user_id)
        order_field = getattr(App, query_request.sort_field, None) if query_request.sort_field else App.create_time
        if order_field is None:
            order_field = App.create_time
        stmt = stmt.order_by(order_field if query_request.sort_order == "ascend" else desc(order_field))
        return stmt

    def _to_app_vo(self, app: App) -> AppVO:
        return AppVO.model_validate(
            {
                "id": app.id,
                "appName": app.app_name,
                "cover": app.cover,
                "initPrompt": app.init_prompt,
                "codeGenType": app.code_gen_type,
                "deployKey": app.deploy_key,
                "deployedTime": app.deployed_time,
                "priority": app.priority,
                "userId": app.user_id,
                "createTime": app.create_time,
                "updateTime": app.update_time,
            }
        )

    def _require_admin(self, login_user: JWTUser) -> None:
        ThrowUtils.throw_if(login_user.user_role != "admin", ErrorCode.NO_AUTH_ERROR, "仅管理员可执行该操作")

    def _build_deploy_url(self, deploy_key: str) -> str:
        # return f"{CODE_DEPLOY_HOST.rstrip('/')}/api/static/{deploy_key}/"
        return f"{CODE_DEPLOY_HOST.rstrip('/')}/{deploy_key}/"

    def _build_internal_deploy_url(self, deploy_key: str) -> str:
        # return f"http://127.0.0.1:{settings.app_service_http_port}/api/static/{deploy_key}/"
        return f"http://127.0.0.1:{settings.app_service_http_port}/{deploy_key}/"

    def _build_cover_url(self, deploy_key: str, resource_name: str) -> str:
        base_path = settings.app_base_path.rstrip("/")
        # return f"http://{settings.app_host}:{settings.app_port}{base_path}/app/static/{deploy_key}/{resource_name}"
        return f"http://{settings.app_host}:{settings.app_port}{base_path}/{deploy_key}/{resource_name}"


def _preview_text(text: str, limit: int = 100) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
