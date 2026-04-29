from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_SERVICE_ROOT = BACKEND_ROOT / "services" / "app-service"
APP_SERVICE_CORE_ROOT = APP_SERVICE_ROOT / "core"
for candidate in reversed((str(APP_SERVICE_CORE_ROOT), str(APP_SERVICE_ROOT), str(BACKEND_ROOT))):
    while candidate in sys.path:
        sys.path.remove(candidate)
    sys.path.insert(0, candidate)

for module_name in list(sys.modules):
    if module_name == "core" or module_name.startswith("core."):
        sys.modules.pop(module_name, None)

from core.parser import CodeParserExecutor
from core.code_generator_facade import CodePersistenceFacade
from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.schema.code import GeneratedCodeSaveRequest


class AppCodePersistenceContractTest(unittest.TestCase):
    def test_generated_code_save_request_omits_codegen_type_when_not_provided(self) -> None:
        request = GeneratedCodeSaveRequest(appId=123, content="<html></html>")
        self.assertEqual(request.model_dump(by_alias=True, exclude_none=True), {"appId": 123, "content": "<html></html>"})

    def test_detect_code_gen_type_prefers_named_vue_project_files(self) -> None:
        content = """```package.json
{\"name\": \"demo\"}
```
```src/App.vue
<template><div>Hello</div></template>
```"""

        self.assertEqual(CodeParserExecutor.detect_code_gen_type(content), CodeGenTypeEnum.VUE_PROJECT)

    def test_detect_code_gen_type_recognizes_multi_file_layout(self) -> None:
        content = """```index.html
<!doctype html><html><body></body></html>
```
```style.css
body { color: red; }
```
```script.js
console.log('demo')
```"""

        self.assertEqual(CodeParserExecutor.detect_code_gen_type(content), CodeGenTypeEnum.MULTI_FILE)

    def test_detect_code_gen_type_falls_back_to_html_for_single_block(self) -> None:
        content = """```html
<!doctype html><html><body>Hello</body></html>
```"""

        self.assertEqual(CodeParserExecutor.detect_code_gen_type(content), CodeGenTypeEnum.HTML)

    def test_code_persistence_facade_exposes_detection(self) -> None:
        content = """```index.html
<!doctype html><html><body></body></html>
```
```style.css
body { color: red; }
```"""

        facade = CodePersistenceFacade()
        self.assertEqual(facade.detect_code_gen_type(content), CodeGenTypeEnum.MULTI_FILE)


if __name__ == "__main__":
    unittest.main()