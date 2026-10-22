"""PromptConfig —— 智能体身份层 prompt 配置与组装。

与 ContextService 的职责划分：
  - PromptConfig：agent 身份层（role / name / goal / constraints / examples / out_schema 等）
  - ContextService：会话/工作区层（workspace metadata / memory / skill / summary 等）

两者正交，_system_prompt() 先调 PromptConfig.build_system_prompt() 拿身份层 prompt，
后续可在 BaseReActAgent 中追加 ContextService 产出的工作区/记忆等上下文段。
"""

from __future__ import annotations

import datetime
from typing import List

from jinja2 import Template
from pydantic import BaseModel, Field

"""
【重要约束】
- 严禁在任务计划中直接调用任何 resource 中的 tool，即使它们在资源列表中被列出。
- 所有 tool 的调用必须通过 ToolExpert agent 实现。
- ToolExpert 的职责是统一管理、代理所有工具的调用，Planner 只应向 ToolExpert 发出工具的使用意图。
"""
# TODO 工具？？
_DEFAULT_SYSTEM_TEMPLATE = """\
你是一个 {{ role }}, {% if name %}名字叫 {{ name }}.
{% endif %}你的目标是 {% if is_retry_chat %}{{ retry_goal }}{% else %}{{ goal }}{% endif %}.\
请一步一步思考完根据下面给出的已知信息和用户问题完成目标，同时请严格遵守下面的约束和规范。
{% if resource_prompt %}\
已知资源信息：
{{ resource_prompt }}
{% endif %}\
{% if expand_prompt %}\
{{ expand_prompt }}
{% endif %}\


{% if memory_prompt %}\
相关记忆：
{{ memory_prompt }} 
{% endif %}\

{% if taskbord_prompt %}\
 任务看板：
{{ taskbord_prompt }} 
{% endif %}\

{% if skill_prompt %}\
可用技能：
{{ skill_prompt }} 
{% endif %}\

*** 重要提醒 ***
请用简体中文进行回答.
当前时间是:{{ now_time }}
{% if is_retry_chat %}\
{% if retry_constraints %}\
{% for retry_constraint in retry_constraints %}\
{{ loop.index }}. {{ retry_constraint }}
{% endfor %}\
{% endif %}\
{% else %}\
{% if constraints %}\
{% for constraint in constraints %}\
{{ loop.index }}. {{ constraint }}
{% endfor %}\
{% endif %}\
{% endif %}\

{% if examples %}\
你也可以参考如下对话示例:
{{ examples }}\
{% endif %}\

{% if out_schema %} {{ out_schema }} {% endif %}\

"""  # noqa


class PromptConfig(BaseModel):
    """智能体 system prompt 配置（对应 _DEFAULT_SYSTEM_TEMPLATE 的全部变量）。"""

    role: str = ""
    name: str = ""
    goal: str = ""
    desc: str = ""
    is_retry_chat: bool = False
    retry_goal: str = ""
    retry_constraints: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    resource_prompt: str = ""  # 项目信息
    expand_prompt: str = ""
    now_time: str = ""
    examples: str = ""
    out_schema: str = ""
    memory_prompt:str = "",
    taskbord_prompt:str = ""
    skill_prompt:str = ""

    def resolve_fields(self, params: dict | None = None) -> dict:
        """预渲染各字段中嵌入的 {{...}} 变量，返回最终字段值。

        供预算层按分区裁剪（resource_prompt / memory_prompt / ...），之后再
        经 render() 做主模板渲染。params 由子类 _prepare_prompt_params() 提供。
        """
        params = params or {}
        resolved = {}
        for field_name in self.__class__.model_fields:
            value = getattr(self, field_name)
            if isinstance(value, str) and "{{" in value and params:
                value = Template(value).render(**params)
            resolved[field_name] = value
        return resolved

    def render(self, resolved: dict) -> str:
        """用已解析的字段值渲染主模板（不再二次解析嵌套变量）。"""
        now_time = resolved.get("now_time") or datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        template = Template(_DEFAULT_SYSTEM_TEMPLATE)
        return template.render(
            role=resolved.get("role", ""),
            name=resolved.get("name", ""),
            goal=resolved.get("goal", ""),
            is_retry_chat=resolved.get("is_retry_chat", False),
            retry_goal=resolved.get("retry_goal", ""),
            retry_constraints=resolved.get("retry_constraints", []),
            constraints=resolved.get("constraints", []),
            resource_prompt=resolved.get("resource_prompt", ""),
            expand_prompt=resolved.get("expand_prompt", ""),
            now_time=now_time,
            examples=resolved.get("examples", ""),
            out_schema=resolved.get("out_schema", ""),
            memory_prompt=resolved.get("memory_prompt", ""),
            taskbord_prompt=resolved.get("taskbord_prompt", ""),
            skill_prompt=resolved.get("skill_prompt", ""),
        )

    def build_default_system_prompt(self, **kwargs) -> str:
        """兼容入口：解析嵌套变量后渲染主模板。"""
        return self.render(self.resolve_fields(kwargs))
