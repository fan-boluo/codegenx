from dataclasses import field
from typing import Any, Dict

from agent.runtime_schema import AgentEvent, AgentState
from agent.task.task_manager import TaskManager
from assembler import ContextAssembler
from compact import microcompact_messages
from compact.compact import CompactionEngine
from compact.large_output import persist_large_output
from memory import SessionMemory
from memory.memory_manager import MemoryManager
from skill.skill_loader import SkillLoader
from tools.base import ToolResult


class SessionContext:
    """
    负责一个session的上下文管理
    """

    session_id: str = ""
    app_id: str = ""

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
        self.task = TaskManager(app_id=self.app_id)
        self._session_memory = SessionMemory(session_id=self.session_id,app_id=self.app_id)
        self._compaction = CompactionEngine(session_id=self.session_id,session_memory=self._session_memory)

    async def build_system_prompt(self, query:str) -> str:
        """
        每个turn要构建的
        """
        await self.assembler.build_workspace(self.app_id)

        self.assembler.memory_prompt = self.memory.load(query)
        self.assembler.skill_prompt = self.skill_loader.build_skill()
        self.assembler.task_prompt = self.task.get_board()

        self.assembler.session_memory_prompt = self.session_memory.load()
        self.system_prompt = self.assembler.prepare_turn_context()
        return self.system_prompt

    def add_user_message(self,message):
        self.chat_messages.append({"role":"user","content":message})

    def add_assistant_message(self, message):
        self.chat_messages.append({"role": "assistant", "content": message})

    def add_tool_message(self, message):
        if isinstance(message,dict):
            self.chat_messages.append(message)
        if isinstance(message,str):
            self.chat_messages.append({"role": "tool", "content": message})

    async def assemble(self)-> list[dict[str, Any]]:
        # 将system_prompt和turn的聊天历史组合
        return self.assembler.assemble(self.system_prompt,self.chat_messages)

    async def microcompact(self):
        self.chat_messages = microcompact_messages(self.chat_messages)

    async def persist_large_output(self,tool_call:Dict[str, Any], output:ToolResult):

        data =  output.data or ""
        persist_large_output(tool_call=tool_call, output=data,app_id=self.app_id,session_id=self.session_id)

    async def compact_after_turn(self):
        # (a) Session-memory extraction — non-blocking background task
        if self._session_memory.should_extract(self.chat_messages):
            self._session_memory.fire_extract(self.chat_messages)

        # (b) Auto-compaction — awaited so messages are updated before next turn
        # 没有传入llm_fn，使用的是session memory快速压缩
        self.chat_messages, result = await self._compaction.compact_if_needed(
            self.chat_messages
        )
        if result is not None:
            yield AgentEvent(
                event_type="CompactEvent",
                data={
                    "path_used": result.path_used,
                    "tokens_before": result.tokens_before,
                    "tokens_after":  result.tokens_after,
                    "messages_removed": result.messages_removed,
                },
                state=AgentState.RUNNING,
            )

    # TODO 上下文还需要做的事情：token计算
    def token_status(self, messages: list[dict]) -> dict:
        """
        Return context window status dict (for status endpoint / UI gauge).
        Delegates to compact.calculate_warning_state().
        """
        from compact import calculate_warning_state
        return calculate_warning_state(messages)

