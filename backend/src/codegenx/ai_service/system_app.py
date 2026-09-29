"""SystemApp —— 全局组件容器（组合根）。

启动时注册全部进程级组件并广播生命周期；项目任意处通过 get_app() 获取。
分层原则与判别法见 docs/SystemApp架构设计.md §2：

┌─────────────────────────────────────────────────────────┐
│ SystemApp（进程级，全局一份，启动装配/关闭回收）              │
│  组件 = 无状态服务 | 注册表 | 客户端池 | 后台任务            │
├─────────────────────────────────────────────────────────┤
│ SessionState（会话级，SessionPool 管理，随会话生灭）          │
├─────────────────────────────────────────────────────────┤
│ TurnState（请求级，已有 ActivateTurn）                      │
└─────────────────────────────────────────────────────────┘

框架（LifeCycle/BaseComponent/组件注册表）在 component.py；本模块是组合根：
- initialize_components() 决定注册清单与顺序（顺序即依赖），末尾冻结 hook 注册表；
- 类型化访问器（get_app().xxx）按 ComponentType 惰性查表，业务代码零感知组件化。

导入约束（防循环）：组件模块在模块级只允许 import 本模块的框架再导出
（SystemApp/BaseComponent/ComponentType）或 component.py 框架本体；本模块
**只在 initialize_components()/访问器内部延迟 import 组件实现类**。
"""
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from shared import log

# 框架层再导出：组件模块统一 `from codegenx.ai_service.system_app import ...` 或
# 直接 `from codegenx.ai_service.component import ...`，两者等价。
from codegenx.ai_service.component import (  # noqa: F401
    BaseComponent,
    ComponentType,
    LifeCycle,
)
from codegenx.ai_service.component import SystemApp as ComponentSystemApp

if TYPE_CHECKING:
    # 仅供类型标注；运行期不产生导入依赖
    from codegenx.ai_service.agent.agent_registry import AgentRegistry
    from codegenx.ai_service.agent.runtime import AgentRuntime
    from codegenx.ai_service.agent.tool_handler import ToolRegistry
    from codegenx.ai_service.chat_message.store import ChatMessageStore
    from codegenx.ai_service.compact.compact import CompactionService
    from codegenx.ai_service.compact.session_summary import SessionSummaryService
    from codegenx.ai_service.context.context_service import ContextService
    from codegenx.ai_service.llm.facade import LLMFacade
    from codegenx.ai_service.memory.memory_manager import MemoryFacade
    from codegenx.ai_service.monitor.maintenance_service import MonitorMaintenanceService
    from codegenx.ai_service.monitor.monitor_pipeline import MonitorPipeline
    from codegenx.ai_service.schedule.memory import MemoryScheduler
    from codegenx.ai_service.session.manager import SessionPersistence
    from codegenx.ai_service.skill.skill_loader import SkillManager
    from codegenx.ai_service.task.task_manager import TaskBoardService


class SystemApp(ComponentSystemApp):
    """进程级容器：持有全部组件并提供类型化访问器。

    组件生命周期钩子实现在各自模块内（BaseComponent 子类），本类只负责
    装配与查表转发；未注册即访问会抛 ValueError（fail fast，宁可显式失败）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._components_ready: bool = False
        self._started: bool = False

    # ── 类型化访问器：惰性 import + 按组件名查表（注册清单见 initialize_components）──

    @property
    def llm(self) -> "LLMFacade":
        from codegenx.ai_service.llm.facade import LLMFacade

        return self.get_component(ComponentType.LLM_FACADE, LLMFacade)

    @property
    def tools(self) -> "ToolRegistry":
        from codegenx.ai_service.agent.tool_handler import ToolRegistry

        return self.get_component(ComponentType.TOOL_REGISTRY, ToolRegistry)

    @property
    def skills(self) -> "SkillManager":
        from codegenx.ai_service.skill.skill_loader import SkillManager

        return self.get_component(ComponentType.SKILL_MANAGER, SkillManager)

    @property
    def agents(self) -> "AgentRegistry":
        from codegenx.ai_service.agent.agent_registry import AgentRegistry

        return self.get_component(ComponentType.AGENT_REGISTRY, AgentRegistry)

    @property
    def context(self) -> "ContextService":
        from codegenx.ai_service.context.context_service import ContextService

        return self.get_component(ComponentType.CONTEXT_SERVICE, ContextService)

    @property
    def session_io(self) -> "SessionPersistence":
        from codegenx.ai_service.session.manager import SessionPersistence

        return self.get_component(ComponentType.SESSION_PERSISTENCE, SessionPersistence)

    @property
    def tasks(self) -> "TaskBoardService":
        from codegenx.ai_service.task.task_manager import TaskBoardService

        return self.get_component(ComponentType.TASK_BOARD, TaskBoardService)

    @property
    def memory(self) -> "MemoryFacade":
        from codegenx.ai_service.memory.memory_manager import MemoryFacade

        return self.get_component(ComponentType.MEMORY_MANAGER, MemoryFacade)

    @property
    def summary(self) -> "SessionSummaryService":
        from codegenx.ai_service.compact.session_summary import SessionSummaryService

        return self.get_component(ComponentType.SESSION_SUMMARY, SessionSummaryService)

    @property
    def compaction(self) -> "CompactionService":
        from codegenx.ai_service.compact.compact import CompactionService

        return self.get_component(ComponentType.COMPACTION, CompactionService)

    @property
    def chat_messages(self) -> "ChatMessageStore":
        from codegenx.ai_service.chat_message.store import ChatMessageStore

        return self.get_component(ComponentType.CHAT_MESSAGE_STORE, ChatMessageStore)

    @property
    def monitor(self) -> "MonitorPipeline":
        from codegenx.ai_service.monitor.monitor_pipeline import MonitorPipeline

        return self.get_component(ComponentType.MONITOR_PIPELINE, MonitorPipeline)

    @property
    def monitor_maintenance(self) -> "MonitorMaintenanceService":
        from codegenx.ai_service.monitor.maintenance_service import MonitorMaintenanceService

        return self.get_component(ComponentType.MONITOR_MAINTENANCE, MonitorMaintenanceService)

    @property
    def memory_scheduler(self) -> "MemoryScheduler":
        from codegenx.ai_service.schedule.memory import MemoryScheduler

        return self.get_component(ComponentType.MEMORY_SCHEDULER, MemoryScheduler)

    @property
    def runtime(self) -> "AgentRuntime":
        from codegenx.ai_service.agent.runtime import AgentRuntime

        return self.get_component(ComponentType.AGENT_RUNTIME, AgentRuntime)


# ── 进程级单容器访问 ─────────────────────────────────────────────────────────

_app: SystemApp | None = None


def get_app() -> SystemApp:
    """进程内任意处获取容器。未初始化时抛错（宁可 fail fast 也不静默兜底）。"""
    if _app is None:
        raise RuntimeError(
            "SystemApp 未初始化：请在应用启动入口调用 init_app()/start_app()"
        )
    return _app


def initialize_components(app: SystemApp) -> None:
    """注册全部组件并冻结 hook 注册表（幂等；顺序即依赖，勿随意调整）。

    每个组件模块提供 initialize_xxx(system_app) 完成构造+注册（DB-GPT 风格），
    构造知识留在组件模块内。注册顺序 = 启动广播顺序：
      llm → tools → skills → agents → context → session_io → tasks
      → memory → summary → compaction → chat_messages → monitor
      → monitor_maintenance → memory_scheduler → runtime
    其中 AgentRuntime 构造即取 ToolRegistry，故必须排在 tools 之后。
    """
    if app._components_ready:
        return

    from codegenx.ai_service.llm.facade import initialize_llm
    from codegenx.ai_service.agent.tool_handler import initialize_tools
    from codegenx.ai_service.skill.skill_loader import initialize_skill
    from codegenx.ai_service.agent.agent_registry import initialize_agents
    from codegenx.ai_service.context.context_service import initialize_context
    from codegenx.ai_service.session.manager import initialize_session_io
    from codegenx.ai_service.task.task_manager import initialize_tasks
    from codegenx.ai_service.memory.memory_manager import initialize_memory
    from codegenx.ai_service.compact.session_summary import initialize_summary
    from codegenx.ai_service.compact.compact import initialize_compaction
    from codegenx.ai_service.chat_message.store import initialize_chat_messages
    from codegenx.ai_service.monitor.monitor_pipeline import initialize_monitor
    from codegenx.ai_service.monitor.maintenance_service import initialize_monitor_maintenance
    from codegenx.ai_service.schedule.memory import initialize_memory_scheduler
    from codegenx.ai_service.agent.runtime import initialize_runtime

    initialize_llm(app)
    initialize_tools(app)
    initialize_skill(app)
    initialize_agents(app)
    initialize_context(app)
    initialize_session_io(app)
    initialize_tasks(app)
    initialize_memory(app)
    initialize_summary(app)
    initialize_compaction(app)
    initialize_chat_messages(app)
    initialize_monitor(app)
    initialize_monitor_maintenance(app)
    initialize_memory_scheduler(app)
    initialize_runtime(app)

    # hook 注册表冻结：@on 监听器随组件模块的 import 链（模块加载即注册）收集完毕，
    # 此处统一校验/拓扑排序/冻结（docs/Hook设计.md §6）。
    # 注意：新增监听器模块必须位于组件依赖图或应用 import 链上，否则收集不到。
    from codegenx.ai_service.hook import hook_manager

    hook_manager.load_and_freeze()

    app._components_ready = True
    log.info("SystemApp 组件装配完成：{} 个组件", len(app.components))


def init_app(app: SystemApp | None = None) -> SystemApp:
    """同步装配（main.py lifespan 前段调用；测试可传入自制实例）：

    创建/安装全局容器 → 注册组件（含 hook 冻结）→ on_init/after_init/before_start。
    异步启动（连接/后台任务/runtime）由 start_app() 在事件循环内完成。
    """
    global _app
    if _app is None:
        _app = app if app is not None else SystemApp()
    initialize_components(_app)
    _app.on_init()
    _app.after_init()
    _app.before_start()
    return _app


async def start_app(app: SystemApp | None = None) -> SystemApp:
    """异步启动（需事件循环，lifespan 中调用）：async_on_init → async_before_start
    → after_start → async_after_start。组件级失败语义由各组件钩子内部决定；
    幂等：已启动的容器重复调用直接返回（防重复拉起后台任务）。"""
    app = app or get_app()
    if app._started:
        return app
    await app.async_on_init()
    await app.async_before_start()
    app.after_start()
    await app.async_after_start()
    app._started = True
    log.info("SystemApp startup completed")
    return app


async def stop_app(app: SystemApp | None = None) -> None:
    """逆序关闭（lifespan shutdown 调用）：组件 async_before_stop → before_stop
    → 基础设施连接池收尾（redis/qdrant/mysql 为 db/ 模块级单例，不属于任何业务组件，
    由容器统一兜底释放；自 main.py lifespan 收编，close 本身幂等）。"""
    app = app or get_app()
    if not app._started:
        # 未成功启动过的容器无需收尾（lifespan 也只在成功 startup 后才调用 shutdown）
        return
    await app.async_before_stop()
    app.before_stop()
    with suppress(Exception):
        from db.redis.redis_client import redis_client

        await redis_client.aclose()
    with suppress(Exception):
        from db.qdrant.client import shutdown_qdrant_client

        await shutdown_qdrant_client()
    with suppress(Exception):
        from db.mysql.session import shutdown_mysql_engine

        await shutdown_mysql_engine()
    app._started = False
    log.info("SystemApp shutdown completed")


def reset_app() -> None:
    """仅测试用：卸载容器，配合 pytest fixture。"""
    global _app
    _app = None


def app_started() -> bool:
    """容器是否已完成启动（诊断/测试用）。"""
    return _app is not None and _app._started
