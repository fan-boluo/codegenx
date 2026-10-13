"""多智能体规格注册表（P4，docs/SystemApp架构设计.md §10）。

核心思想（§10.1 红线 3）：智能体差异做成数据（AgentSpec），不做成代码分支——
各智能体在初始化时主动调用 register_spec 注册自身规格，而非从配置文件读取。
全局组件在多智能体下是前提（N 个智能体共享一份基础设施，成本 O(1)），
组件只读 spec 视图，不持有 per-agent 可变字段（红线 1）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from codegenx.ai_service.component import BaseComponent, ComponentType
from shared import log

if TYPE_CHECKING:
    from codegenx.ai_service.utils.config import AgentConfig


@dataclass
class AgentSpec:
    """声明式智能体规格（各智能体初始化时主动注册 ↔ 一个 AgentSpec）。"""
    id: str
    role: str
    name: str   # 唯一标识：planner / data_explore / analyze / visualize ...
    desc: str
    cls: type | None = None  # 智能体类本身，用于后续按需实例化


class AgentRegistry(BaseComponent):
    """AgentSpec 存取 + 启动校验（name 不重复）。

    各智能体通过 register_spec 主动注册自身规格，校验 fail fast，
    拒绝带病启动（§12 风险表）。
    """

    name = ComponentType.AGENT_REGISTRY

    def __init__(self, system_app=None, specs: list[AgentSpec] | None = None,
                 default: AgentSpec | None = None) -> None:
        BaseComponent.__init__(self, system_app)
        self._specs: dict[str, AgentSpec] = {}
        for spec in specs or []:
            self.register_spec(spec)
        self._default = default or AgentSpec(id="default", role="default", name="default", desc="默认智能体")

    def init_app(self, system_app) -> None:
        self.system_app = system_app

    # ------------------------------------------------------------------ lifecycle

    async def async_before_start(self) -> None:
        """启动校验（当前暂无额外校验，预留扩展点）。"""
        pass

    # ------------------------------------------------------------------ 注册

    def register_spec(self, spec: AgentSpec) -> None:
        """智能体主动注册自身规格（name 不重复，fail fast）。"""
        key = self._normalize(spec.name)
        if not key:
            raise ValueError("AgentSpec.name 不能为空")
        if key in self._specs:
            raise ValueError(f"AgentSpec.name 重复: {spec.name}")
        self._specs[key] = spec
        log.info("[AgentRegistry] 注册智能体规格: {} (cls={})", spec.name, spec.cls)

    def set_default(self, spec: AgentSpec) -> None:
        """设置默认智能体规格。"""
        self._default = spec

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

    # ------------------------------------------------------------------ 辅助

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name or "").strip().lower()



def initialize_agents(system_app) -> AgentRegistry:
    """注册智能体规格注册表组件（system_app.initialize_components 调用）。
    各智能体的配置解析与规格注册由 initialize_agent_instances 完成。"""
    return system_app.register(AgentRegistry)
