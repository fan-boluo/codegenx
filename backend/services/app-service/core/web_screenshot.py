from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import uuid

from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode


class WebScreenshotService:
    def __init__(self) -> None:
        self.window_width = int(os.getenv("SCREENSHOT_WINDOW_WIDTH", "1600"))
        self.window_height = int(os.getenv("SCREENSHOT_WINDOW_HEIGHT", "900"))
        self.page_load_timeout_seconds = int(os.getenv("SCREENSHOT_PAGE_LOAD_TIMEOUT_SECONDS", "30"))
        self.implicit_wait_seconds = int(os.getenv("SCREENSHOT_IMPLICIT_WAIT_SECONDS", "10"))
        self.ready_state_timeout_seconds = int(os.getenv("SCREENSHOT_READY_STATE_TIMEOUT_SECONDS", "10"))
        self.post_load_wait_seconds = float(os.getenv("SCREENSHOT_POST_LOAD_WAIT_SECONDS", "2"))
        self.user_agent = os.getenv(
            "SCREENSHOT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )

    def save_web_page_screenshot(self, web_url: str) -> Path:
        normalized_url = web_url.strip()
        if not normalized_url:
            raise BusinessException(ErrorCode.PARAMS_ERROR, "截图的网址不能为空")

        temp_dir = Path(tempfile.mkdtemp(prefix="app-screenshot-"))
        original_path = temp_dir / f"{uuid.uuid4().hex[:8]}.png"
        compressed_path = temp_dir / f"{uuid.uuid4().hex[:8]}_compressed.jpg"
        driver = self._create_driver()

        try:
            driver.get(normalized_url)
            self._wait_for_page_load(driver)
            driver.save_screenshot(str(original_path))
            self._compress_image(original_path, compressed_path)
            if original_path.exists():
                original_path.unlink()
            log.info("web screenshot saved url={} path={}", normalized_url, compressed_path)
            return compressed_path
        except Exception as exc:
            log.exception("capture screenshot failed url={}", normalized_url)
            raise BusinessException(ErrorCode.OPERATION_ERROR, f"生成网页截图失败: {exc}") from exc
        finally:
            try:
                driver.quit()
            except Exception:
                log.warning("close screenshot chrome driver failed url={}", normalized_url)

    def _create_driver(self) -> webdriver.Chrome:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument(f"--window-size={self.window_width},{self.window_height}")
        options.add_argument(f"--user-agent={self.user_agent}")

        chrome_binary = os.getenv("SCREENSHOT_CHROME_BINARY")
        if chrome_binary:
            options.binary_location = chrome_binary

        try:
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(self.page_load_timeout_seconds)
            driver.implicitly_wait(self.implicit_wait_seconds)
            return driver
        except Exception as exc:
            log.exception("initialize chrome driver failed")
            raise BusinessException(ErrorCode.SYSTEM_ERROR, f"初始化 Chrome 浏览器失败: {exc}") from exc

    def _wait_for_page_load(self, driver: webdriver.Chrome) -> None:
        try:
            wait = WebDriverWait(driver, self.ready_state_timeout_seconds)
            wait.until(lambda current_driver: current_driver.execute_script("return document.readyState") == "complete")
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            if self.post_load_wait_seconds > 0:
                time.sleep(self.post_load_wait_seconds)
        except TimeoutException:
            log.warning("wait for page load timed out, continue screenshot")
        except Exception:
            log.warning("wait for page load failed, continue screenshot")

    @staticmethod
    def _compress_image(source_path: Path, target_path: Path) -> None:
        try:
            with Image.open(source_path) as image:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(target_path, format="JPEG", quality=30, optimize=True)
        except Exception as exc:
            log.exception("compress screenshot failed source={} target={}", source_path, target_path)
            raise BusinessException(ErrorCode.SYSTEM_ERROR, f"压缩图片失败: {exc}") from exc