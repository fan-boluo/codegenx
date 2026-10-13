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
    resource_prompt: str = ""
    expand_prompt: str = ""
    now_time: str = ""
    examples: str = ""
    out_schema: str = ""

    def build_default_system_prompt(self,**kwargs) -> str:
        """使用 Jinja2 渲染 _DEFAULT_SYSTEM_TEMPLATE 并返回组装后的 system prompt。

        kwargs 由子类 _prepare_prompt_params() 提供，用于预渲染 PromptConfig
        字段中嵌入的 {{...}} 变量（如 goal 中的 {{type}}、resource_prompt 中的
        {{database_info}}），然后再传入主模板渲染。
        """
        # 1) 预渲染 PromptConfig 字符串字段中的嵌套 {{...}} 变量
        if kwargs:
            resolved = {}
            for field_name in self.__class__.model_fields:
                value = getattr(self, field_name)
                if isinstance(value, str) and "{{" in value:
                    value = Template(value).render(**kwargs)
                resolved[field_name] = value
        else:
            resolved = {f: getattr(self, f) for f in self.__class__.model_fields}

        now_time = resolved.get("now_time") or datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        template = Template(_DEFAULT_SYSTEM_TEMPLATE)
        return template.render(
            role=resolved["role"],
            name=resolved["name"],
            goal=resolved["goal"],
            is_retry_chat=resolved["is_retry_chat"],
            retry_goal=resolved["retry_goal"],
            retry_constraints=resolved["retry_constraints"],
            constraints=resolved["constraints"],
            resource_prompt=resolved["resource_prompt"],
            expand_prompt=resolved["expand_prompt"],
            now_time=now_time,
            examples=resolved["examples"],
            out_schema=resolved["out_schema"],
            **kwargs,
        )
