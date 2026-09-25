"""多智能体规格注册表（P4，docs/SystemApp架构设计.md §10）。

核心思想（§10.1 红线 3）：智能体差异做成数据（AgentSpec），不做成代码分支——
加一个智能体 = agents 段加一条配置，不改代码。全局组件在多智能体下是前提
（N 个智能体共享一份基础设施，成本 O(1)），组件只读 spec 视图，不持有
per-agent 可变字段（红线 1）。

模型路由解析顺序（§10.4）：
  spec.model_override[scenario]        # 该智能体对该场景的显式配置（最高）
  → model_roles["{agent}:{scenario}"]  # 全局按智能体细分（可选写法）
  → model_roles[scenario]              # 现有全局场景路由（现状不变）
  → [默认模型]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Union

from shared import log

if TYPE_CHECKING:
    from codegenx.ai_service.utils.config import AgentConfig


@dataclass
class MemoryPolicy:
    """智能体的记忆读写策略（hot/warm 仍按 app+user 全局共享，§10.5 起步不分片）。

    防污染/防矛盾靠元数据过滤表达，不动 key：
    - read_types：注入时允许的记忆类型过滤；None=全部
    - write_enabled：纯执行类智能体可关写（False=不产生新记忆）
    - write_types：允许提炼成的记忆类型；None=不限
    """

    read_types: list[str] | None = None
    write_enabled: bool = True
    write_types: list[str] | None = None


@dataclass
class AgentSpec:
    """声明式智能体规格（agents 段一条配置 ↔ 一个 AgentSpec）。"""

    name: str                              # 唯一标识：planner / data_explore / analyze / visualize ...
    description: str = ""                  # 给规划智能体的派活描述（subagent 工具的 description 来源）
    persona: str = ""                      # system prompt 模板；空=默认 DEFAULT_PROMPT_TEMPLATE
    tools: list[str] | None = None         # 工具 allowlist；None=全部（默认智能体行为）
    skills: list[str] | None = None        # skill allowlist；None=全部
    model_override: dict[str, Union[str, list[str]]] | None = None  # 场景→模型/链覆盖（§10.4）
    limits: "AgentConfig | None" = None    # max_steps/max_tool_iterations/temperature 覆盖
    memory: MemoryPolicy = field(default_factory=MemoryPolicy)


class AgentRegistry:
    """AgentSpec 存取 + 启动校验（persona 非空、name 不重复、工具/skill 名存在）。

    校验 fail fast（沿用 hook 冻结校验思路），拒绝带病启动（§12 风险表）。
    """

    def __init__(self, specs: list[AgentSpec] | None = None,
                 default: AgentSpec | None = None) -> None:
        self._specs: dict[str, AgentSpec] = {}
        for spec in specs or []:
            key = self._normalize(spec.name)
            if not key:
                raise ValueError("AgentSpec.name 不能为空")
            if key in self._specs:
                raise ValueError(f"AgentSpec.name 重复: {spec.name}")
            self._specs[key] = spec
        # 默认 spec：不配置任何新字段时与现单智能体行为完全等价（persona 空 → 内置模板）
        self._default = default or AgentSpec(name="default")

    # ------------------------------------------------------------------ 存取

    def get(self, name: str | None) -> AgentSpec:
        """按名取 spec；None/未知名回落 default spec（现单智能体行为）。"""
        if not name:
            return self._default
        return self._specs.get(self._normalize(name), self._default)

    def default(self) -> AgentSpec:
        return self._default

    def all_specs(self) -> list[AgentSpec]:
        return list(self._specs.values())

    # ------------------------------------------------------------------ 启动校验

    def validate_against(self, tool_names: set[str], skill_names: set[str]) -> None:
        """工具/skill allowlist 里的名字必须真实存在（写错=静默失效，故启动期拦截）。"""
        for spec in self._specs.values():
            for tool_name in spec.tools or []:
                if tool_name not in tool_names:
                    raise ValueError(
                        f"AgentSpec '{spec.name}' 引用了不存在的工具: {tool_name}"
                    )
            for skill_name in spec.skills or []:
                if skill_name not in skill_names:
                    raise ValueError(
                        f"AgentSpec '{spec.name}' 引用了不存在的 skill: {skill_name}"
                    )
        log.info("[AgentRegistry] {} 个智能体规格校验通过: {}",
                 len(self._specs), list(self._specs) or "（仅默认）")

    # ------------------------------------------------------------------ 配置装配

    @classmethod
    def from_config(cls, cfg) -> "AgentRegistry":
        """config.agents（AgentConfig 新扩展字段）→ 注册表。

        - defaults=True 的第一条为默认 spec（无则第一个已命名项）；
        - 未命名配置项（纯 LLM 参数行，P4 之前就存在）不构成智能体，跳过；
        - 校验：name 非空/不重复；非默认智能体必须配置 persona。
        """
        from codegenx.ai_service.utils.config import AgentConfig

        specs: list[AgentSpec] = []
        default_spec: AgentSpec | None = None
        first_named: AgentSpec | None = None
        for agent_cfg in (cfg.agents or []):
            if not isinstance(agent_cfg, AgentConfig):
                continue
            name = str(agent_cfg.id or agent_cfg.name or "").strip()
            if not name:
                continue
            memory = agent_cfg.memory
            spec = AgentSpec(
                name=name,
                description=str(agent_cfg.description or ""),
                persona=str(agent_cfg.persona or ""),
                tools=list(agent_cfg.tools) if agent_cfg.tools else None,
                skills=list(agent_cfg.skills) if agent_cfg.skills else None,
                model_override=dict(agent_cfg.model_override) if agent_cfg.model_override else None,
                limits=agent_cfg,
                memory=MemoryPolicy(
                    read_types=list(memory.read_types) if memory and memory.read_types else None,
                    write_enabled=memory.write_enabled if memory else True,
                    write_types=list(memory.write_types) if memory and memory.write_types else None,
                ),
            )
            if getattr(agent_cfg, "defaults", False) and default_spec is None:
                default_spec = spec
            if first_named is None:
                first_named = spec
            specs.append(spec)

        registry = cls(specs, default=default_spec or first_named)
        for spec in specs:
            if spec is registry._default:
                continue  # 默认智能体空 persona = 内置 DEFAULT_PROMPT_TEMPLATE
            if not spec.persona.strip():
                raise ValueError(f"AgentSpec '{spec.name}' 未配置 persona（非默认智能体必填）")
        return registry

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name or "").strip().lower()
