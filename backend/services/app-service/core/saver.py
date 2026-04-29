from __future__ import annotations

import json
from pathlib import Path
import subprocess
import shutil
import traceback

from shared.config.log_config import log
from shared.constants import CODE_DEPLOY_ROOT_DIR, get_bot_code_dir, get_bot_deploy_dir
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.schema.code import HtmlCodeResult, MultiFileCodeResult, VueProjectCodeResult


class CodeFileSaverExecutor:
    @staticmethod
    def execute_saver(code_result, code_gen_type: CodeGenTypeEnum, app_id: int) -> Path:
        if code_gen_type == CodeGenTypeEnum.HTML:
            return HtmlCodeFileSaverTemplate().save_code(code_result, app_id)
        if code_gen_type == CodeGenTypeEnum.MULTI_FILE:
            return MultiFileCodeFileSaverTemplate().save_code(code_result, app_id)
        if code_gen_type == CodeGenTypeEnum.VUE_PROJECT:
            return VueProjectCodeFileSaverTemplate().save_code(code_result, app_id)
        raise ValueError(f"Unsupported code generation type: {code_gen_type}")

    @staticmethod
    def deploy_saved_code(source_dir: Path, deploy_key: str) -> Path:
        deploy_dir = get_bot_deploy_dir(deploy_key)
        if deploy_dir.exists():
            shutil.rmtree(deploy_dir)
        shutil.copytree(source_dir, deploy_dir)
        return deploy_dir

    @staticmethod
    def build_vue_project(source_dir: Path) -> Path:
        package_json = source_dir / "package.json"
        if not package_json.exists():
            raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "Vue 项目缺少 package.json")
        npm_command = ["npm.cmd", "install"] if sys_platform_windows() else ["npm", "install"]
        build_command = ["npm.cmd", "run", "build"] if sys_platform_windows() else ["npm", "run", "build"]
        try:
            subprocess.run(npm_command, cwd=source_dir, check=True, capture_output=True, text=True)
            subprocess.run(build_command, cwd=source_dir, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            log.exception("npm command not found while building vue project at {}\n{}", source_dir, traceback.format_exc())
            raise BusinessException(ErrorCode.OPERATION_ERROR, "未找到 npm 命令，无法构建 Vue 项目") from exc
        except subprocess.CalledProcessError as exc:
            log.exception("vue project build failed at {} stdout={} stderr={}\n{}", source_dir, exc.stdout, exc.stderr, traceback.format_exc())
            raise BusinessException(ErrorCode.OPERATION_ERROR, f"Vue 项目构建失败: {exc.stderr or exc.stdout}") from exc
        dist_dir = source_dir / "dist"
        if not dist_dir.exists():
            raise BusinessException(ErrorCode.OPERATION_ERROR, "Vue 项目构建完成但 dist 目录不存在")
        return dist_dir


class CodeFileSaverTemplate:
    def save_code(self, result, app_id: int) -> Path:
        if result is None:
            raise ValueError("code result cannot be empty")
        base_dir = self.build_unique_dir(app_id)
        self.save_files(result, base_dir)
        return base_dir

    def build_unique_dir(self, app_id: int) -> Path:
        path = get_bot_code_dir(app_id)
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_to_file(self, base_dir: Path, filename: str, content: str | None) -> None:
        if not content:
            return
        file_path = base_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    def save_files(self, result, base_dir: Path) -> None:
        raise NotImplementedError

    def get_code_type(self) -> CodeGenTypeEnum:
        raise NotImplementedError


class HtmlCodeFileSaverTemplate(CodeFileSaverTemplate):
    def save_files(self, result: HtmlCodeResult, base_dir: Path) -> None:
        html = result.html_code
        if result.css_code:
            html = html.replace("</head>", f"<style>\n{result.css_code}\n</style>\n</head>") if "</head>" in html else f"<style>\n{result.css_code}\n</style>\n{html}"
        if result.js_code:
            html = html.replace("</body>", f"<script>\n{result.js_code}\n</script>\n</body>") if "</body>" in html else f"{html}\n<script>\n{result.js_code}\n</script>"
        self.write_to_file(base_dir, "index.html", html)

    def get_code_type(self) -> CodeGenTypeEnum:
        return CodeGenTypeEnum.HTML


class MultiFileCodeFileSaverTemplate(CodeFileSaverTemplate):
    def save_files(self, result: MultiFileCodeResult, base_dir: Path) -> None:
        if result.files:
            for file in result.files:
                self.write_to_file(base_dir, file.filename, file.content)
            return
        self.write_to_file(base_dir, "index.html", result.html_code)
        self.write_to_file(base_dir, "style.css", result.css_code)
        self.write_to_file(base_dir, "script.js", result.js_code)

    def get_code_type(self) -> CodeGenTypeEnum:
        return CodeGenTypeEnum.MULTI_FILE


class VueProjectCodeFileSaverTemplate(CodeFileSaverTemplate):
    def save_files(self, result: VueProjectCodeResult, base_dir: Path) -> None:
        wrote_package_json = False
        for file in result.files:
            self.write_to_file(base_dir, file.filename, file.content)
            if file.filename == "package.json":
                wrote_package_json = True
        if not wrote_package_json:
            package_json = result.package_json or {
                "name": "codegenx-vue-app",
                "private": True,
                "version": "1.0.0",
                "type": "module",
                "scripts": {"dev": "vite", "build": "vite build"},
                "dependencies": {"vue": "^3.5.13", "vue-router": "^4.5.0"},
                "devDependencies": {"@vitejs/plugin-vue": "^5.2.1", "vite": "^5.4.10"},
            }
            self.write_to_file(base_dir, "package.json", json.dumps(package_json, ensure_ascii=False, indent=2))

    def get_code_type(self) -> CodeGenTypeEnum:
        return CodeGenTypeEnum.VUE_PROJECT


def sys_platform_windows() -> bool:
    import os

    return os.name == "nt"
