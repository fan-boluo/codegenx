"""SessionContext —— 纯会话状态（P2 瘦身，docs/SystemApp架构设计.md §4.2）。

只留真会话状态：ids、system_prompt、chat_messages、每轮组装产物（TurnPrompts）、
压缩熔断器（会话级语义）、摘要阈值状态（SummaryState）。
原成员对象（MemoryManager/TaskManager/SessionSummaryService/CompactionEngine/
ContextAssembler/SkillLoader）全部服务化，经 get_app() 的全局服务委托完成。
"""
from dataclasses import field, dataclass
from typing import Any, Dict

from codegenx.ai_service.agent.agent_schema import AgentEvent, AgentState, AgentEventType
from codegenx.ai_service.context.context_service import TurnPrompts
from codegenx.ai_service.compact.session_summary import SummaryState
from codegenx.ai_service.compact.compact import CompactionService
from codegenx.ai_service.compact import microcompact_messages, estimate_tokens
from codegenx.ai_service.compact.large_output import persist_large_output
from codegenx.ai_service.llm.resilience import CircuitBreaker
from codegenx.ai_service.tools.base import ToolResult
from shared import log


@dataclass
class SessionContext:
    """
    负责一个session的上下文管理（纯状态，行为委托 SystemApp 服务）
    """

    # ── 身份（定位数据与目录）────────────────────────────────
    session_id: str = ""
    app_id: str = ""
    # 会话归属用户，决定 .data/{userId}/{appId} 两级路径
    user_id: str = ""
    db_name: str | None = None
    # P4：会话归属智能体（§10.3 会话归属：RuntimeSessionState.agent_name 落到此处使用）
    agent_name: str = ""

    # ── 真会话状态 ───────────────────────────────────────────
    system_prompt: str = ""
    # 整个会话的聊天历史（唯一大对象）
    chat_messages: list[dict[str, Any]] = field(default_factory=list)
    # 每轮组装产物（原 ContextAssembler 的可变字段）
    prompts: TurnPrompts = field(default_factory=TurnPrompts)
    # 压缩熔断器（会话级语义：一个会话压缩失败不应熔断别的会话；
    # key=compact:{session_id}，韧性层三态 CircuitBreaker，冷却后半开恢复）
    compact_breaker: CircuitBreaker | None = None
    # 会话摘要阈值状态（原 SessionSummaryService 实例字段）
    summary_state: SummaryState = field(default_factory=SummaryState)

    def __post_init__(self) -> None:
        # 会话级压缩熔断器：参数在 CompactionService.make_breaker 统一维护
        self.compact_breaker = CompactionService.make_breaker(self.session_id)
        log.info(self.session_id,"SessonContext 启动完毕")

    # ------------------------------------------------------------------ 每轮组装

    async def build_system_prompt(self, query:str) -> str:
        """每个session要构建的：workspace 元数据 + 记忆 + skill + 任务看板 + 会话摘要。

        P4 §10.3：智能体差异（persona/skill 可见范围/记忆 read_types）由 AgentSpec
        参数化——组件只读 spec 视图，无 per-agent 可变状态。
        """
        from codegenx.ai_service.system_app import get_app

        app = get_app()
        service = app.context
        if service is None:
            raise RuntimeError("SystemApp.context 未装配：请在启动入口 init_app() 后使用")
        spec = app.agents.get(self.agent_name) if app.agents is not None else None

        self.prompts.workspace_metadata = await service.build_workspace_metadata(
            self.user_id, self.app_id, db_name=self.db_name
        )

        self.prompts.memory_prompt = await app.memory.load(
            query, user_id=self.user_id, app_id=self.app_id, session_id=self.session_id,
            read_types=spec.memory.read_types if spec is not None else None,
        )
        self.prompts.skill_prompt = app.skills.build_prompt(
            allowlist=spec.skills if spec is not None else None,
        )
        self.prompts.task_prompt = app.tasks.get_board(
            user_id=self.user_id, app_id=self.app_id, session_id=self.session_id
        )

        self.prompts.session_summary_prompt = app.summary.load(
            user_id=self.user_id, app_id=self.app_id, session_id=self.session_id
        )
        self.system_prompt = service.render_turn_context(
            self.prompts, persona=spec.persona if spec is not None else ""
        )
        log.debug("system prompt init success")
        return self.system_prompt

    def get_safe_path(self) -> list|None:
        if self.prompts.workspace_metadata:
            code_dir = self.prompts.workspace_metadata.get("safe_paths",[])
            rm_dirs =  self.prompts.workspace_metadata.get("allowed_rw_dirs",[])

            return code_dir + rm_dirs
        return []


    def add_user_message(self,message):
        if isinstance(message,dict):
            self.chat_messages.append(message)
        if isinstance(message,str):
            self.chat_messages.append({"role":"user","content":message})

    def add_assistant_message(self, message):
        if isinstance(message,dict):
            self.chat_messages.append(message)
        if isinstance(message,str):
            self.chat_messages.append({"role": "assistant", "content": message})

    def add_tool_message(self, message):
        if isinstance(message,dict):
            self.chat_messages.append(message)
        if isinstance(message,str):
            self.chat_messages.append({"role": "tool", "content": message})

    async def assemble(self)-> list[dict[str, Any]]:
        """将system_prompt和turn的聊天历史组合（组装逻辑在无状态 ContextService）。"""
        from codegenx.ai_service.system_app import get_app

        service = get_app().context
        if service is None:
            raise RuntimeError("SystemApp.context 未装配")
        return await service.assemble(self.system_prompt,self.chat_messages)

    async def micro_compact(self,max_tokens:int):
        log.debug("micro compact 前,{}", estimate_tokens(self.chat_messages))
        self.chat_messages = microcompact_messages(
            self.chat_messages,
            protect_last_n_results=5,
            max_result_tokens=max_tokens,
        )
        log.debug("micro compact 后,{}",estimate_tokens(self.chat_messages))

    async def persist_large_output(self,tool_call:Dict[str, Any], output:ToolResult) -> str:

        data =  output.data or ""
        log.debug("大的输出持久化：{}",len(data))
        return persist_large_output(tool_call=tool_call, output=data,user_id=self.user_id,app_id=self.app_id,session_id=self.session_id)

    # ------------------------------------------------------------------ 压缩

    async def compact_after_step(self):
        """每个 step 后：仅做 token 检查 + full compaction，不触发 session summary extraction。"""
        from codegenx.ai_service.system_app import get_app

        app = get_app()
        self.chat_messages, result = await app.compaction.compact_if_needed(
            self.chat_messages,
            breaker=self.compact_breaker,
            summary_loader=lambda: app.summary.load(
                user_id=self.user_id, app_id=self.app_id, session_id=self.session_id
            ),
        )

        if result is not None:
            log.debug("进行step的压缩了")
            yield AgentEvent(
                event_type=AgentEventType.COMPACT_EVENT,
                data={
                    "path_used": result.path_used,
                    "tokens_before": result.tokens_before,
                    "tokens_after":  result.tokens_after,
                    "messages_removed": result.messages_removed,
                },
                state=AgentState.RUNNING,
            )

    async def compact_after_turn(self):
        """整个 turn 结束后：异步触发会话摘要后台提取（非阻塞任务，属于上下文工程的压缩边界）。"""
        from codegenx.ai_service.system_app import get_app

        summary = get_app().summary
        if summary.should_extract(self.summary_state, self.chat_messages):
            summary.fire_extract(
                self.summary_state,
                self.chat_messages,
                user_id=self.user_id,
                app_id=self.app_id,
                session_id=self.session_id,
            )
            log.debug("session summary 后台提取已触发")

    async def force_compact(self) -> None:
        """强制压缩聊天历史（供 recovery 策略调用）。

        直接调用 CompactionService.compact_if_needed，无论 token 是否超阈值都尝试压缩。
        压缩后的 messages 直接替换 self.chat_messages。
        """
        from codegenx.ai_service.system_app import get_app

        app = get_app()
        self.chat_messages, result = await app.compaction.compact_if_needed(
            self.chat_messages,
            breaker=self.compact_breaker,
            summary_loader=lambda: app.summary.load(
                user_id=self.user_id, app_id=self.app_id, session_id=self.session_id
            ),
        )
        if result is not None:
            log.info(
                "force_compact: path={}, tokens_before={}, tokens_after={}, removed={}",
                result.path_used,
                result.tokens_before,
                result.tokens_after,
                result.messages_removed,
            )
        else:
            log.info("force_compact: compaction skipped (not needed)")

    # TODO 上下文还需要做的事情：token计算
    def token_status(self, messages: list[dict]) -> dict:
        """
        Return context window status dict (for status endpoint / UI gauge).
        Delegates to compact.calculate_warning_state().
        """
        from codegenx.ai_service.compact import calculate_warning_state
        return calculate_warning_state(messages)
