from dataclasses import field, dataclass
from typing import Any, Dict

from agent.agent_schema import AgentEvent, AgentState, AgentEventType
from agent.task.task_manager import TaskManager
from llm.async_client import AsyncLLMClient
from shared.config.log_config import log
from context.assembler import ContextAssembler
from compact import microcompact_messages, estimate_tokens
from compact.compact import CompactionEngine
from compact.large_output import persist_large_output
from memory import SessionMemory
from memory.memory_manager import MemoryManager
from skill.skill_loader import SkillLoader
from tools.base import ToolResult

@dataclass
class SessionContext:
    """
    负责一个session的上下文管理
    """

    session_id: str = ""
    app_id: str = ""
    db_name: str | None = None

    memory: MemoryManager = field(init=False)
    # 压缩，不要类

    # 组装
    assembler:ContextAssembler = field(default_factory=ContextAssembler)

    skill_loader = SkillLoader()
    skills = skill_loader.load_all_skills()

    task : TaskManager = field(init=False)
    _session_memory: SessionMemory = field(init=False)
    _compaction:CompactionEngine = field(init=False)

    system_prompt:str = field(init=False)
    # 一次turn的聊天历史
    chat_messages:list[dict[str, Any]] = field(init=False)

    # tool_registry : ToolRegistry=field(default_factory=ToolRegistry)


    def __post_init__(self) -> None:
        self.memory = MemoryManager(session_id=self.session_id,app_id=self.app_id)
        self.task = TaskManager(app_id=self.app_id, session_id=self.session_id)
        self._session_memory = SessionMemory(session_id=self.session_id,app_id=self.app_id)
        self._compaction = CompactionEngine(session_id=self.session_id,session_memory=self._session_memory,llm_fn=AsyncLLMClient().invoke)
        self.system_prompt = ""
        self.chat_messages = []
        log.info(self.session_id,"SessonContext 启动完毕")
    async def build_system_prompt(self, query:str) -> str:
        """
        每个session要构建的
        """
        await self.assembler.build_workspace(self.app_id, db_name=self.db_name)

        self.assembler.memory_prompt = await self.memory.load(query)
        self.assembler.skill_prompt = await self.skill_loader.build_skill()
        self.assembler.task_prompt = self.task.get_board()

        self.assembler.session_memory_prompt = self._session_memory.load()
        self.system_prompt = self.assembler.prepare_turn_context()
        log.debug("system prompt init success")
        return self.system_prompt

    def get_safe_path(self) -> list|None:
        if self.assembler.workspace_metadata:
            code_dir = self.assembler.workspace_metadata.get("safe_paths",[])
            rm_dirs =  self.assembler.workspace_metadata.get("allowed_rw_dirs",[])

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
        # 将system_prompt和turn的聊天历史组合
        return await self.assembler.assemble(self.system_prompt,self.chat_messages)

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
        return persist_large_output(tool_call=tool_call, output=data,app_id=self.app_id,session_id=self.session_id)

    async def compact_after_step(self):
        """每个 step 后：仅做 token 检查 + full compaction，不触发 session_memory extraction。"""
        self.chat_messages, result = await self._compaction.compact_if_needed(
            self.chat_messages
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
        """整个 turn 结束后：异步触发 session_memory extraction（非阻塞后台任务）。"""
        if self._session_memory.should_extract(self.chat_messages):
            self._session_memory.fire_extract(self.chat_messages)
            log.debug("session memory 后台压缩已触发")

    async def force_compact(self) -> None:
        """强制压缩聊天历史（供 recovery 策略调用）。

        直接调用 CompactionEngine.compact_if_needed，无论 token 是否超阈值都尝试压缩。
        压缩后的 messages 直接替换 self.chat_messages。
        """
        self.chat_messages, result = await self._compaction.compact_if_needed(
            self.chat_messages
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
        from compact import calculate_warning_state
        return calculate_warning_state(messages)

