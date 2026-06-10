from __future__ import annotations

import asyncio
import json
from datetime import datetime
from math import ceil
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Any, AsyncGenerator

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config.config import get_settings
from shared.config.log_config import log
from shared.constants import DEFAULT_APP_PRIORITY, PYTHON_ENV, get_code_dir, get_deploy_dir
from shared.exceptions.error_code import ErrorCode
from shared.exceptions.throw_utils import ThrowUtils
from orm.app import App
from shared.schema.app import AppAddRequest, AppAdminUpdateRequest, AppDeployRequest, AppQueryRequest, AppUpdateRequest, AppVO
from shared.schema.common import PageData

from core.auth_proxy import JWTUser
from infra.db_manager import create_project_database, list_database_tables


settings = get_settings()


class AppService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_app(self, app_add_request: AppAddRequest, login_user: JWTUser, trace_id: str | None = None) -> int:
        app_name = app_add_request.app_name.strip()
        init_prompt = app_add_request.init_prompt.strip()
        ThrowUtils.throw_if(not app_name, ErrorCode.PARAMS_ERROR, "项目名称不能为空")
        ThrowUtils.throw_if(len(app_name) > 20, ErrorCode.PARAMS_ERROR, "项目名称不能超过20字")
        ThrowUtils.throw_if(not init_prompt, ErrorCode.PARAMS_ERROR, "初始化 prompt 不能为空")
        db_name = app_add_request.db_name.strip() if app_add_request.db_name else None
        log.info(
            "app-service create app start traceId={} userId={} appName={} dbName={} promptLen={} preview={}",
            trace_id,
            login_user.user_id,
            app_name,
            db_name,
            len(init_prompt),
            _preview_text(init_prompt),
        )
        app = App(
            user_id=login_user.user_id,
            app_name=app_name,
            db_name=db_name,
            init_prompt=init_prompt,
            code_gen_type="",
            priority=DEFAULT_APP_PRIORITY,
        )
        self.db.add(app)
        await self.db.commit()
        await self.db.refresh(app)

        code_dir = get_code_dir(app.id)
        code_dir.mkdir(parents=True, exist_ok=True)
        log.info("app-service create app code dir traceId={} appId={} codeDir={}", trace_id, app.id, code_dir)

        if db_name:
            try:
                create_project_database(db_name)
                log.info("app-service create app db created traceId={} appId={} dbName={}", trace_id, app.id, db_name)
            except Exception as exc:
                log.warning("app-service create app db failed traceId={} appId={} dbName={} error={}", trace_id, app.id, db_name, exc)

        log.info("app-service create app completed traceId={} userId={} appId={}", trace_id, login_user.user_id, app.id)
        return app.id

    # async def deploy_app(self, request: AppDeployRequest, login_user: JWTUser) -> dict[str, str | None]:
    #     app = await self._get_owned_app(request.app_id, login_user)
    #     deploy_key = app.deploy_key or secrets.token_urlsafe(6)[:6]
    #     source_dir = get_code_dir(app.id)
    #     ThrowUtils.throw_if(not source_dir.exists(), ErrorCode.NOT_FOUND_ERROR, "应用代码不存在，请先生成代码")
    #
    #     deploy_dir = get_deploy_dir(deploy_key)
    #     if deploy_dir.exists():
    #         shutil.rmtree(deploy_dir)
    #     shutil.copytree(source_dir, deploy_dir)
    #
    #     deploy_url = self._build_deploy_url(deploy_key)
    #     screenshot_url = None
    #     try:
    #         screenshot_resource = self.screenshot_service.generate_and_upload_screenshot(
    #             self._build_internal_deploy_url(deploy_key),
    #             deploy_key,
    #         )
    #         screenshot_url = self._build_cover_url(deploy_key, screenshot_resource)
    #     except Exception as exc:
    #         log.warning(
    #             "app-service deploy screenshot skipped appId={} deployKey={} deployUrl={} error={}",
    #             app.id,
    #             deploy_key,
    #             deploy_url,
    #             exc,
    #         )
    #     app.deploy_key = deploy_key
    #     app.deployed_time = datetime.utcnow()
    #     if screenshot_url:
    #         app.cover = screenshot_url
    #     await self.db.commit()
    #     log.info(
    #         "app-service deploy completed appId={} deployKey={} deployUrl={} screenshotUrl={}",
    #         app.id,
    #         deploy_key,
    #         deploy_url,
    #         screenshot_url,
    #     )
    #     return {
    #         "deployKey": deploy_key,
    #         "deployUrl": deploy_url,
    #         "screenshotUrl": screenshot_url,
    #     }

    async def delete_app(self, app_id: int, login_user: JWTUser) -> bool:
        await self._get_owned_app(app_id, login_user)
        await self.db.execute(delete(App).where(App.id == app_id))
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
        source_dir = get_code_dir(app.id)
        ThrowUtils.throw_if(not source_dir.exists(), ErrorCode.NOT_FOUND_ERROR, "应用代码不存在，请先生成代码")
        temp_dir = Path(tempfile.mkdtemp(prefix=f"app-download-{app.id}-"))
        archive_base = temp_dir / str(app.id)
        archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=source_dir)
        return Path(archive_path)

    async def get_code_tree(self, app_id: int, login_user: JWTUser) -> list:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id)
        if not code_dir.exists():
            return []
        return AppService._build_file_tree(code_dir, code_dir)

    async def get_code_file(self, app_id: int, file_path: str, login_user: JWTUser) -> str:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / file_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)

        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        ThrowUtils.throw_if(not target.exists() or not target.is_file(), ErrorCode.NOT_FOUND_ERROR, "文件不存在")

        max_size = 500 * 1024
        max_lines = 50
        file_size = target.stat().st_size

        if file_size > max_size:
            content = []
            with target.open("r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    if idx >= max_lines:
                        break
                    content.append(line)
            return "".join(content) + "[文件超过500KB，仅展示前50行]"
        return target.read_text(encoding="utf-8", errors="replace")

    async def save_code_file(self, app_id: int, file_path: str, content: str, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / file_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)
        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return True

    async def create_file(self, app_id: int, file_path: str, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / file_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)
        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        ThrowUtils.throw_if(not file_path.strip(), ErrorCode.PARAMS_ERROR, "文件名不能为空")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=False)
        return True

    async def create_folder(self, app_id: int, dir_path: str, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / dir_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)
        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        ThrowUtils.throw_if(not dir_path.strip(), ErrorCode.PARAMS_ERROR, "文件夹名不能为空")
        target.mkdir(parents=True, exist_ok=False)
        return True

    async def upload_file(self, app_id: int, file_path: str, content: bytes, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / file_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)
        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return True

    async def delete_node(self, app_id: int, node_path: str, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / node_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)
        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        if not target.exists():
            ThrowUtils.throw_if(True, ErrorCode.NOT_FOUND_ERROR, "文件/文件夹不存在")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True

    async def rename_node(self, app_id: int, old_path: str, new_path: str, login_user: JWTUser) -> bool:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        old_target = (code_dir / old_path).resolve()
        new_target = (code_dir / new_path).resolve()
        code_dir_str = str(code_dir)
        if not (str(old_target) == code_dir_str or str(old_target).startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        if not (str(new_target) == code_dir_str or str(new_target).startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "新路径无效")
        ThrowUtils.throw_if(not old_target.exists(), ErrorCode.NOT_FOUND_ERROR, "源文件/文件夹不存在")
        ThrowUtils.throw_if(new_target.exists(), ErrorCode.PARAMS_ERROR, "目标已存在")
        new_target.parent.mkdir(parents=True, exist_ok=True)
        old_target.rename(new_target)
        return True

    async def run_script(self, app_id: int, file_path: str, env: str, login_user: JWTUser) -> AsyncGenerator[str, None]:
        app = await self._get_owned_app(app_id, login_user)
        code_dir = get_code_dir(app.id).resolve()
        target = (code_dir / file_path).resolve()
        code_dir_str = str(code_dir)
        target_str = str(target)
        if not (target_str == code_dir_str or target_str.startswith(code_dir_str + os.sep)):
            ThrowUtils.throw_if(True, ErrorCode.PARAMS_ERROR, "路径无效")
        ThrowUtils.throw_if(not target.exists() or not target.is_file(), ErrorCode.NOT_FOUND_ERROR, "文件不存在")

        python_exe = PYTHON_ENV.get(env)
        if not python_exe:
            yield f"data: {json.dumps({'event_type': 'error', 'data': {'message': f'未知运行环境: {env}'}})}\n\n"
            return

        process = await asyncio.create_subprocess_exec(
            python_exe, str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(code_dir),
        )
        stdout_pipe = process.stdout
        stderr_pipe = process.stderr

        async def read_stream(pipe, tag):
            while True:
                line = await pipe.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                yield f"data: {json.dumps({'event_type': 'output', 'data': {'stream': tag, 'text': text}}, ensure_ascii=False)}\n\n"

        async def drain():
            async for chunk in read_stream(stdout_pipe, "stdout"):
                yield chunk
            async for chunk in read_stream(stderr_pipe, "stderr"):
                yield chunk
            await process.wait()
            yield f"data: {json.dumps({'event_type': 'done', 'data': {'code': process.returncode}}, ensure_ascii=False)}\n\n"

        async for chunk in drain():
            yield chunk

    async def get_db_tables(self, app_id: int, login_user: JWTUser) -> list:
        app = await self._get_owned_app(app_id, login_user)
        if not app.db_name:
            return []
        try:
            return list_database_tables(app.db_name)
        except Exception as exc:
            log.warning("get_db_tables failed appId={} dbName={} error={}", app_id, app.db_name, exc)
            return []

    @staticmethod
    def _build_file_tree(base_dir: Path, current_dir: Path) -> list:
        result = []
        try:
            items = sorted(current_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            for item in items:
                rel = str(item.relative_to(base_dir)).replace("\\", "/")
                if item.is_dir():
                    result.append({
                        "name": item.name,
                        "path": rel,
                        "type": "dir",
                        "children": AppService._build_file_tree(base_dir, item),
                    })
                else:
                    result.append({
                        "name": item.name,
                        "path": rel,
                        "type": "file",
                    })
        except PermissionError:
            pass
        return result

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
                "dbName": app.db_name,
                "createTime": app.create_time,
                "updateTime": app.update_time,
            }
        )

    def _require_admin(self, login_user: JWTUser) -> None:
        ThrowUtils.throw_if(login_user.user_role != "admin", ErrorCode.NO_AUTH_ERROR, "仅管理员可执行该操作")

    def _build_deploy_url(self, deploy_key: str) -> str:
        base_path = settings.app_base_path.rstrip("/")
        return f"http://{settings.app_host}:{settings.app_port}{base_path}/app/static/{deploy_key}/"

    def _build_internal_deploy_url(self, deploy_key: str) -> str:
        return f"http://127.0.0.1:{settings.app_service_http_port}/api/static/{deploy_key}/"

    def _build_cover_url(self, deploy_key: str, resource_name: str) -> str:
        base_path = settings.app_base_path.rstrip("/")
        return f"http://{settings.app_host}:{settings.app_port}{base_path}/app/static/{deploy_key}/{resource_name}"


def _preview_text(text: str, limit: int = 100) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
