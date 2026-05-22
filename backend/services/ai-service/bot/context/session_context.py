from dataclasses import field
from typing import Any

from agent.task.task_manager import TaskManager
from agent.tool_executor import ToolExecutor
from agent.tool_handler import ToolRegistry
from assembler import ContextAssembler
from context.context_compaction import ContextCompactor
from memory import SessionMemory
from memory.memory_manager import MemoryManager
from skill.skill_loader import SkillLoader


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
    session_memory: SessionMemory = field(init=False)



    system_prompt:str = field(init=False)
    # 一次turn的聊天历史
    chat_messages:list[dict[str, Any]] = field(init=False)

    # tool_registry : ToolRegistry=field(default_factory=ToolRegistry)


    def __post_init__(self) -> None:
        self.memory = MemoryManager(session_id=self.session_id,app_id=self.app_id)
        self.task = TaskManager(app_id=self.app_id)
        self.session_memory = SessionMemory(session_id=self.session_id,app_id=self.app_id)

    async def build_system_prompt(self, query:str) -> None:
        """
        每个turn要构建的
        """
        await self.assembler.build_workspace(self.app_id)

        self.assembler.memory_prompt = self.memory.load(query)
        self.assembler.skill_prompt = self.skill_loader.build_skill()
        self.assembler.task_prompt = self.task.get_board()

        self.assembler.session_memory_prompt = self.session_memory.load()
        self.system_prompt = self.assembler.prepare_turn_context()
        return system_prompt

    def add_user_message(self,message):
        self.chat_messages.append({"role":"user","content":message})

    def add_assistant_message(self, message):
        self.chat_messages.append({"role": "assistant", "content": message})

    async def assemble(self)-> list[dict[str, Any]]:
        # 将system_prompt和turn的聊天历史组合
        # TODO 聊天历史，在一次turn中交互太多会溢出，后续要进行压缩
        return self.assembler.assemble(self.system_prompt,self.chat_messages)

    async def microcompact(self):
        self.chat_messages = microcompact_messages(self.chat_messages)


    # TODO 上下文还需要做的事情：token计算
    def token_status(self, messages: list[dict]) -> dict:
        """
        Return context window status dict (for status endpoint / UI gauge).
        Delegates to compact.calculate_warning_state().
        """
        from compact import calculate_warning_state
        return calculate_warning_state(messages)

