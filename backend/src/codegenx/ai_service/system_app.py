"""SystemApp —— 全局组件容器（组合根）。

启动时装配全部进程级组件并管理生命周期；项目任意处通过 get_app() 获取。
分层原则与判别法见 docs/SystemApp架构设计.md §2：

┌─────────────────────────────────────────────────────────┐
│ SystemApp（进程级，全局一份，启动装配/关闭回收）              │
│  组件 = 无状态服务 | 注册表 | 客户端池 | 后台任务            │
├─────────────────────────────────────────────────────────┤
│ SessionState（会话级，SessionPool 管理，随会话生灭）          │
├─────────────────────────────────────────────────────────┤
│ TurnState（请求级，已有 ActivateTurn）                      │
└─────────────────────────────────────────────────────────┘

约束：只有本模块允许在模块级 import 具体实现类；业务代码一律
`get_app().xxx` 访问（实现类 import 请延迟到方法内部以避免环）。
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shared import log

# 组合根特权：仅本模块在模块级 import 具体实现类（业务代码一律 get_app().xxx）。
# 被引用实现均不得在模块级反向 import 本模块，否则成环。
from codegenx.ai_service.compact.compact import CompactionService
from codegenx.ai_service.compact.session_summary import SessionSummaryService
from codegenx.ai_service.context.context_service import ContextService
from codegenx.ai_service.memory.memory_manager import MemoryFacade
from codegenx.ai_service.agent.agent_registry import AgentRegistry
from codegenx.ai_service.session.manager import SessionPersistence
from codegenx.ai_service.skill.skill_loader import SkillRegistry
from codegenx.ai_service.task.task_manager import TaskBoardService

if TYPE_CHECKING:
    # 仅供类型标注；运行期不产生导入依赖
    from codegenx.ai_service.agent.runtime import AgentRuntime
    from codegenx.ai_service.agent.tool_handler import ToolRegistry
    from codegenx.ai_service.chat_message.store import ChatMessageStore
    from codegenx.ai_service.hook.core import HookManager
    from codegenx.ai_service.monitor.monitor_pipeline import MonitorPipeline
    from codegenx.ai_service.monitor.maintenance_service import MonitorMaintenanceService
    from codegenx.ai_service.schedule.memory import MemoryScheduler
    from codegenx.ai_service.utils.config import Config


# ── LLM 门面：llm/ 调用层的生命周期与观测（不复制状态）────────────────────────


class LLMFacade:
    """LLM 调用层门面。

    调用面保持 llm/ 模块函数（resilient_invoke / get_llm），本门面只负责：
      - startup：可选预热默认模型客户端（构造连接池，免首次请求冷启动）；
      - shutdown：统一释放 provider 级共享连接池（close_llm_clients）；
      - 观测聚合：全部熔断器状态快照（管理端点用）。
    """

    async def preheat_default_model(self) -> None:
        """预热默认模型客户端（仅构造，不发起网络请求；失败不阻断启动）。"""
        with suppress(Exception):
            from codegenx.ai_service.llm.async_client import get_llm
            from codegenx.ai_service.utils.config import config as app_config

            model = app_config.get_default_agent().resolved_model_name
            if model:
                get_llm(model)
                log.info("LLM 默认模型客户端已预热: {}", model)

    async def shutdown(self) -> None:
        from codegenx.ai_service.llm.client_registry import close_llm_clients

        await close_llm_clients()

    def circuit_snapshot(self) -> dict[str, str]:
        from codegenx.ai_service.llm.resilience import circuit_snapshot

        return circuit_snapshot()


# ── SystemApp 容器 ───────────────────────────────────────────────────────────


@dataclass
class SystemApp:
    """进程级全局容器：组件注册、启动装配、关闭回收。"""

    # ── 配置与总线 ────────────────────────────────────────────
    config: "Config"                                  # 引用 utils/config.py 的单例（不复制）
    hooks: "HookManager"                              # hook/core.py 单例

    # ── LLM ──────────────────────────────────────────────────
    llm: LLMFacade = field(default_factory=LLMFacade)  # 生命周期/观测门面，调用面仍是模块函数

    # ── 注册表 ────────────────────────────────────────────────
    tools: "ToolRegistry | None" = None               # 启动扫描一次的 ToolRegistry 单例
    skills: "SkillRegistry" = field(default_factory=SkillRegistry)  # 迁自 SessionContext 类属性
    # P4 §10：多智能体规格（不配置=仅默认 spec，现行为不变）；startup 时按 config.agents 重装+校验
    agents: "AgentRegistry" = field(default_factory=AgentRegistry)

    # ── 无状态服务（ids 作参数；纯 Python 无外部依赖，default_factory 装配）────
    context: "ContextService | None" = None           # workspace 元数据/骨架/组装
    session_io: "SessionPersistence" = field(default_factory=SessionPersistence)
    tasks: "TaskBoardService" = field(default_factory=TaskBoardService)
    memory: "MemoryFacade" = field(default_factory=MemoryFacade)
    summary: "SessionSummaryService" = field(default_factory=SessionSummaryService)
    compaction: "CompactionService" = field(default_factory=CompactionService)

    # ── 存储与后台任务 ────────────────────────────────────────
    chat_messages: "ChatMessageStore | None" = None   # 收编 get_chat_message_store()
    monitor: "MonitorPipeline | None" = None          # 收编 get_monitor_pipeline()
    monitor_maintenance: "MonitorMaintenanceService | None" = None
    memory_scheduler: "MemoryScheduler | None" = None

    # ── 引擎 ─────────────────────────────────────────────────
    runtime: "AgentRuntime | None" = None             # startup 时创建的全局引擎

    _started: bool = False
    _startup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ------------------------------------------------------------------ lifecycle

    async def startup(self) -> None:
        """启动七步（顺序即依赖）；幂等，重复调用直接返回。"""
        async with self._startup_lock:
            if self._started:
                return

            # 1. config 校验（agent/model 必填项），带病配置快速失败
            self._validate_config()

            # 2. 冻结 hook 注册表：内置监听器已随应用 import 链完成 @on 收集（docs/Hook设计.md §6）
            self.hooks.load_and_freeze()

            # 3. 基础设施 warmup：qdrant 预热 + warm 库确保（失败降级不阻断主服务）
            await self._startup_infra()

            # 4. 注册表装载：ToolRegistry 目录扫描一次、SkillRegistry 装载一次、
            #    AgentRegistry 按 config.agents 装配并校验（P4 §10：fail fast 拒绝带病启动）
            from codegenx.ai_service.agent.tool_handler import get_tool_registry

            self.tools = get_tool_registry()
            self.skills.load()
            self.agents = AgentRegistry.from_config(self.config)
            self.agents.validate_against(
                tool_names={t.name for t in self.tools.tools},
                skill_names={s.name for s in self.skills.all()},
            )

            # 5. 服务装配：LLM 生命周期门面（预热默认模型客户端）
            await self.llm.preheat_default_model()

            # 6. 引擎启动：session pool 清理循环 + dispatcher
            from codegenx.ai_service.agent.runtime import AgentRuntime

            self.runtime = AgentRuntime()
            await self.runtime.start()
            log.info("启动runtime完毕")

            # 7. 后台任务：monitor 周期维护 + 记忆离线任务 worker（失败降级不阻断）
            from codegenx.ai_service.monitor.maintenance_service import (
                get_monitor_maintenance_service,
            )
            from codegenx.ai_service.schedule.memory import get_memory_scheduler

            self.monitor_maintenance = get_monitor_maintenance_service()
            await self.monitor_maintenance.start_periodic_maintenance()
            with suppress(Exception):
                self.memory_scheduler = get_memory_scheduler()
                await self.memory_scheduler.startup()

            self._started = True
            log.info("SystemApp startup completed")

    async def shutdown(self) -> None:
        """严格逆序关闭：后台任务 → runtime → LLM 连接池 → 基础设施连接池。"""
        async with self._startup_lock:
            if not self._started and self.runtime is None:
                return
            # 7← 后台任务：monitor 周期维护
            if self.monitor_maintenance is not None:
                with suppress(Exception):
                    await self.monitor_maintenance.stop_periodic_maintenance()
            # 7← 记忆离线任务 worker（宽限 10s，running 任务复位 pending）
            if self.memory_scheduler is not None:
                with suppress(Exception):
                    await self.memory_scheduler.shutdown(grace=10.0)
            # 6← 引擎：dispatcher + session pool
            if self.runtime is not None:
                with suppress(Exception):
                    await self.runtime.stop()
                self.runtime = None
            # 5← LLM 共享连接池（自 main.py lifespan 移入；close_llm_clients 本身幂等）
            with suppress(Exception):
                await self.llm.shutdown()
            # 3← 基础设施：redis / qdrant / mysql
            await self._shutdown_infra()
            self._started = False
            log.info("SystemApp shutdown completed")

    # ------------------------------------------------------------------ steps

    def _validate_config(self) -> None:
        """最小启动校验：默认智能体模型与模型表必须可用，否则拒绝带病启动。"""
        default_agent = self.config.get_default_agent()
        if not (default_agent.model or "").strip():
            raise RuntimeError(
                "config 校验失败：默认智能体 model 未配置（config.json agents[].model）"
            )
        if not self.config.models:
            raise RuntimeError("config 校验失败：models 列表为空，无法路由任何模型")

    async def _startup_infra(self) -> None:
        """基础设施预热；qdrant/MySQL 暂不可用只降级记忆功能，不阻断主服务。"""
        from db.qdrant.client import warm_up_qdrant_client
        from codegenx.ai_service.memory.vector_store import ensure_warm_collection

        with suppress(Exception):
            await warm_up_qdrant_client()
            await ensure_warm_collection()
            log.info("warm_memories collection 已就绪")

    async def _shutdown_infra(self) -> None:
        """基础设施连接池关闭（redis / qdrant / mysql）。"""
        with suppress(Exception):
            from db.redis.redis_client import redis_client

            await redis_client.aclose()
        with suppress(Exception):
            from db.qdrant.client import shutdown_qdrant_client

            await shutdown_qdrant_client()
        with suppress(Exception):
            from db.mysql.session import shutdown_mysql_engine

            await shutdown_mysql_engine()


# ── 进程级单容器访问 ─────────────────────────────────────────────────────────

_app: SystemApp | None = None


def get_app() -> SystemApp:
    """进程内任意处获取容器。未初始化时抛错（宁可 fail fast 也不静默兜底）。"""
    if _app is None:
        raise RuntimeError(
            "SystemApp 未初始化：请在应用启动入口调用 init_app()/startup()"
        )
    return _app


def init_app(app: SystemApp | None = None) -> SystemApp:
    """创建并安装容器（main.py 启动时调用；测试可传入自制实例）。"""
    global _app
    if _app is not None:
        return _app
    if app is None:
        from codegenx.ai_service.hook import hook_manager
        from codegenx.ai_service.utils.config import config as app_config

        app = SystemApp(config=app_config, hooks=hook_manager)
    _app = app
    return app


def reset_app() -> None:
    """仅测试用：卸载容器，配合 pytest fixture。"""
    global _app
    _app = None


def app_started() -> bool:
    """容器是否已完成 startup（诊断/测试用）。"""
    return _app is not None and _app._started
