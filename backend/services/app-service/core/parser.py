from __future__ import annotations

import json
import re

from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.schema.code import CodeFile, HtmlCodeResult, MultiFileCodeResult, VueProjectCodeResult


class CodeParserExecutor:
    @staticmethod
    def detect_code_gen_type(code_content: str, preferred: CodeGenTypeEnum | None = None) -> CodeGenTypeEnum:
        if CodeParserExecutor._looks_like_vue_project(code_content):
            return CodeGenTypeEnum.VUE_PROJECT
        if CodeParserExecutor._looks_like_multi_file(code_content):
            return CodeGenTypeEnum.MULTI_FILE
        if preferred is not None:
            return preferred
        return CodeGenTypeEnum.HTML

    @staticmethod
    def execute_parser(code_content: str, code_gen_type: CodeGenTypeEnum):
        if code_gen_type == CodeGenTypeEnum.HTML:
            return CodeParserExecutor.parse_html_code(code_content)
        if code_gen_type == CodeGenTypeEnum.MULTI_FILE:
            return CodeParserExecutor.parse_multi_file_code(code_content)
        if code_gen_type == CodeGenTypeEnum.VUE_PROJECT:
            return CodeParserExecutor.parse_vue_project_code(code_content)
        raise ValueError(f"Unsupported code generation type: {code_gen_type}")

    @staticmethod
    def parse_html_code(code_content: str) -> HtmlCodeResult:
        html_code = CodeParserExecutor._extract_block(code_content, r"```html\s*\n([\s\S]*?)```")
        css_code = CodeParserExecutor._extract_block(code_content, r"```css\s*\n([\s\S]*?)```")
        js_code = CodeParserExecutor._extract_block(code_content, r"```(?:js|javascript)\s*\n([\s\S]*?)```")
        return HtmlCodeResult(
            htmlCode=(html_code or code_content).strip(),
            cssCode=css_code.strip() if css_code else None,
            jsCode=js_code.strip() if js_code else None,
        )

    @staticmethod
    def parse_multi_file_code(code_content: str) -> MultiFileCodeResult:
        html_code = CodeParserExecutor._extract_block(code_content, r"```html\s*\n([\s\S]*?)```")
        css_code = CodeParserExecutor._extract_block(code_content, r"```css\s*\n([\s\S]*?)```")
        js_code = CodeParserExecutor._extract_block(code_content, r"```(?:js|javascript)\s*\n([\s\S]*?)```")
        files: list[CodeFile] = []
        if html_code:
            files.append(CodeFile(filename="index.html", content=html_code.strip()))
        if css_code:
            files.append(CodeFile(filename="style.css", content=css_code.strip()))
        if js_code:
            files.append(CodeFile(filename="script.js", content=js_code.strip()))
        return MultiFileCodeResult(
            htmlCode=html_code.strip() if html_code else None,
            cssCode=css_code.strip() if css_code else None,
            jsCode=js_code.strip() if js_code else None,
            files=files,
        )

    @staticmethod
    def parse_vue_project_code(code_content: str) -> VueProjectCodeResult:
        files = CodeParserExecutor._extract_named_files(code_content)
        package_json: dict | None = None
        dependencies: dict[str, str] | None = None
        for file in files:
            if file.filename == "package.json":
                try:
                    package_json = json.loads(file.content)
                    dependencies = package_json.get("dependencies")
                except json.JSONDecodeError:
                    package_json = None
                break
        return VueProjectCodeResult(files=files, packageJson=package_json, dependencies=dependencies)

    @staticmethod
    def _extract_block(content: str, pattern: str) -> str | None:
        match = re.search(pattern, content, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _extract_named_files(content: str) -> list[CodeFile]:
        files: list[CodeFile] = []
        for filename, body in re.findall(r"```([^\n`]+)\n([\s\S]*?)```", content):
            normalized = filename.strip()
            if "/" in normalized or "." in normalized:
                files.append(CodeFile(filename=normalized, content=body.strip()))
        return files

    @staticmethod
    def _looks_like_vue_project(content: str) -> bool:
        named_files = CodeParserExecutor._extract_named_files(content)
        normalized_names = {file.filename.strip().lower() for file in named_files}
        if any(
            name == "package.json"
            or name.endswith(".vue")
            or name.startswith("src/")
            or name.startswith("public/")
            or name in {"vite.config.ts", "vite.config.js", "vite.config.mjs", "tsconfig.json"}
            for name in normalized_names
        ):
            return True

        lowered = content.lower()
        return any(marker in lowered for marker in ("```vue", "vite.config", "createapp(", "@vitejs/plugin-vue"))

    @staticmethod
    def _looks_like_multi_file(content: str) -> bool:
        named_files = CodeParserExecutor._extract_named_files(content)
        if named_files:
            return not CodeParserExecutor._looks_like_vue_project(content)

        lowered = content.lower()
        file_markers = ("index.html", "style.css", "script.js", "app.js", "main.js")
        return any(marker in lowered for marker in file_markers)
