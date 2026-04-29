from __future__ import annotations

from typing import Any

from pydantic import Field

from shared.schema.chat import ChatMessage
from shared.schema.common import CamelBaseModel


class LLMContextBuildModel(CamelBaseModel):
    """LLM 上下文构建参数。"""

    message: list[ChatMessage] = Field(default_factory=list)
    code_gen_type: str | None = Field(default="html", alias="codeGenType")
    memory: list[dict[str, str]] | None = None
    tools: list[dict[str, Any]] | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    enable_reasoning: bool | None = Field(default=None, alias="enableReasoning")
    stream: bool = False


class CodeFile(CamelBaseModel):
    filename: str
    content: str


class CodeGenerationResponse(CamelBaseModel):
    code: str
    code_gen_type: str = Field(alias="codeGenType")
    files: list[CodeFile] | None = None


class GeneratedCodeSaveRequest(CamelBaseModel):
    app_id: int = Field(alias="appId")
    code_gen_type: str | None = Field(default=None, alias="codeGenType")
    content: str


class HtmlCodeResult(CamelBaseModel):
    html_code: str = Field(alias="htmlCode")
    css_code: str | None = Field(default=None, alias="cssCode")
    js_code: str | None = Field(default=None, alias="jsCode")


class MultiFileCodeResult(CamelBaseModel):
    html_code: str | None = Field(default=None, alias="htmlCode")
    css_code: str | None = Field(default=None, alias="cssCode")
    js_code: str | None = Field(default=None, alias="jsCode")
    files: list[CodeFile] = Field(default_factory=list)


class VueProjectCodeResult(CamelBaseModel):
    files: list[CodeFile] = Field(default_factory=list)
    package_json: dict[str, Any] | None = Field(default=None, alias="packageJson")
    dependencies: dict[str, str] | None = None

