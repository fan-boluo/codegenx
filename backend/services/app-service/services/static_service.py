from __future__ import annotations

import mimetypes
from pathlib import Path

from shared.constants import get_code_dir, get_deploy_dir
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.enums.code_gen_type import CodeGenTypeEnum


class StaticResourceService:
    def __init__(self) -> None:
        self.deploy_root_dir = None
        self.output_root_dir = None

    def resolve_resource(self, deploy_key: str, resource_path: str | None = None) -> tuple[Path, str]:
        if not deploy_key.strip():
            raise BusinessException(ErrorCode.PARAMS_ERROR, "deployKey 不能为空")
        base_dir = get_deploy_dir(deploy_key).resolve()
        return self._resolve_file(base_dir, resource_path, not_found_message="部署资源不存在")

    def resolve_preview_resource(self, code_gen_type: str, app_id: int, resource_path: str | None = None) -> tuple[Path, str]:
        if app_id <= 0:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "appId 非法")
        code_type = CodeGenTypeEnum.get_enum_by_value(code_gen_type)
        if code_type is None:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "不支持的代码类型")

        base_dir = get_code_dir(app_id).resolve()
        if code_type == CodeGenTypeEnum.VUE_PROJECT:
            dist_dir = (base_dir / "dist").resolve()
            if dist_dir.exists() and dist_dir.is_dir():
                base_dir = dist_dir

        return self._resolve_file(base_dir, resource_path, not_found_message="预览资源不存在")

    def _resolve_file(self, base_dir: Path, resource_path: str | None, *, not_found_message: str) -> tuple[Path, str]:
        if not base_dir.exists() or not base_dir.is_dir():
            raise BusinessException(ErrorCode.NOT_FOUND_ERROR, not_found_message)

        normalized = (resource_path or "").strip("/")
        if not normalized:
            candidate = self._resolve_default_entry(base_dir)
            return candidate, self._guess_media_type(candidate)
        candidate = (base_dir / normalized).resolve()
        if base_dir not in candidate.parents and candidate != base_dir:
            raise BusinessException(ErrorCode.NO_AUTH_ERROR, "非法资源路径")
        if candidate.is_dir():
            candidate = self._resolve_default_entry(candidate)
        if not candidate.exists() or not candidate.is_file():
            raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "静态资源不存在")
        return candidate, self._guess_media_type(candidate)

    def _resolve_default_entry(self, base_dir: Path) -> Path:
        index_file = (base_dir / "index.html").resolve()
        if index_file.exists() and index_file.is_file():
            return index_file

        html_files = sorted(
            file_path
            for file_path in base_dir.iterdir()
            if file_path.is_file() and file_path.suffix.lower() == ".html"
        )
        if len(html_files) == 1:
            return html_files[0].resolve()

        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "静态资源不存在")

    def _guess_media_type(self, candidate: Path) -> str:
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix in {".html", ".css", ".js", ".json", ".svg"}:
            media_type = f"{media_type}; charset=utf-8"
        return media_type