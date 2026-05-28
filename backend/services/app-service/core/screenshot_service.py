from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from shared.constants import get_deploy_dir
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode


class ScreenshotService:
    def __init__(self) -> None:
        self.web_screenshot_service: Any | None = None

    def generate_and_upload_screenshot(self, web_url: str, deploy_key: str) -> str:
        normalized_url = web_url.strip()
        if not normalized_url:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "截图的网址不能为空")
        normalized_deploy_key = deploy_key.strip()
        if not normalized_deploy_key:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "deployKey 不能为空")

        screenshot_service = self._get_web_screenshot_service()

        log.info("start generate deploy screenshot url={} deployKey={}", normalized_url, normalized_deploy_key)
        local_screenshot_path = screenshot_service.save_web_page_screenshot(normalized_url)

        try:
            resource_name = self._persist_screenshot_locally(local_screenshot_path, normalized_deploy_key)
            log.info(
                "deploy screenshot saved locally url={} deployKey={} resource={}",
                normalized_url,
                normalized_deploy_key,
                resource_name,
            )
            return resource_name
        finally:
            self._cleanup_local_file(local_screenshot_path)

    def _persist_screenshot_locally(self, local_screenshot_path: Path, deploy_key: str) -> str:
        if not local_screenshot_path.exists():
            raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "截图文件不存在")

        deploy_dir = get_deploy_dir(deploy_key)
        if not deploy_dir.exists() or not deploy_dir.is_dir():
            raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "部署目录不存在，请先部署应用")

        target_path = deploy_dir / "cover.jpg"
        shutil.copy2(local_screenshot_path, target_path)
        return target_path.name

    def _get_web_screenshot_service(self) -> Any:
        if self.web_screenshot_service is not None:
            return self.web_screenshot_service

        from core.web_screenshot import WebScreenshotService

        self.web_screenshot_service = WebScreenshotService()
        return self.web_screenshot_service

    @staticmethod
    def _cleanup_local_file(local_screenshot_path: Path) -> None:
        temp_dir = local_screenshot_path.parent
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            log.warning("cleanup screenshot temp dir failed path={}", temp_dir)