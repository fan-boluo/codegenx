from __future__ import annotations

from shared.enums.code_gen_type import CodeGenTypeEnum

from core.parser import CodeParserExecutor
from core.saver import CodeFileSaverExecutor


class CodePersistenceFacade:
    """Workspace-side code parsing, saving, and build orchestration."""

    def detect_code_gen_type(
        self,
        code_content: str,
        preferred: CodeGenTypeEnum | None = None,
    ) -> CodeGenTypeEnum:
        return CodeParserExecutor.detect_code_gen_type(code_content, preferred=preferred)

    def save_generated_code(
        self,
        code_content: str,
        code_gen_type: CodeGenTypeEnum,
        app_id: int,
    ) -> dict[str, str | None]:
        parsed_result = CodeParserExecutor.execute_parser(code_content, code_gen_type)
        saved_dir = CodeFileSaverExecutor.execute_saver(parsed_result, code_gen_type, app_id)
        build_dir = None
        if code_gen_type == CodeGenTypeEnum.VUE_PROJECT:
            build_dir = CodeFileSaverExecutor.build_vue_project(saved_dir)
        return {
            "savedDir": str(saved_dir),
            "buildDir": str(build_dir) if build_dir else None,
        }


AiCodeGeneratorFacade = CodePersistenceFacade
