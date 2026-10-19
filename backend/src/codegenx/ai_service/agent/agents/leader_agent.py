"""
LeaderAgent —— lead agent：统一工具面编排与执行（agent-as-tool，同步派发）

参照主流 agent-as-tool 设计（Anthropic Task 工具 / OpenAI Agents SDK as_tool）：
dispatch_agent 是一个普通工具——调用时同步执行目标子智能体的完整 run()，
其最终结果作为 tool result 回填对话，lead 在同一 ReAct 工具循环内继续决策。
派发不登记看板、不产生跨 agent 消息跳（调度循环只剩 user → lead → user）。


- 子智能体结果直接以 tool 消息进对话上下文——审查即 ReAct 本身
  （看到结果继续思考），无需审查框架/历史注入改写/批信封；
- 子智能体结果同步写入会话历史真相源（add_agent_result），跨轮追问可见；
- 计划链路握手在派发工具内处理：派发任务匹配看板就绪计划任务时复用
  task_id（claim in_progress → 回传 consume 闭环，解锁下游 depends_on DAG）；
- 看板由 LLM 经 task_create/update/get/list 显式管理（亲自执行的计划任务
  完成后 task_update 标记 completed 解锁下游），派发无隐式看板副作用。

职责分层（自原单类实现拆分）：
- LeaderAgent（本文件）：编排流程 + 输入组装 + 会话历史注入 + 终答
- SupervisorDecisionEngine（supervisor_decision.py）：统一模板与智能体清单
- TaskBoardCoordinator（supervisor_board.py)：看板的全部确定性操作

编排状态 OrchestrationState 已退役：看板（按 request 落盘）即唯一共享状态，
执行轨迹观测由后续 trace/hook 模块承担。
"""

from typing import Any

from codegenx.ai_service.agent.action import ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.agents.supervisor_board import TaskBoardCoordinator
from codegenx.ai_service.agent.agents.supervisor_decision import SupervisorDecisionEngine
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.prompt.prompt_manager import PromptConfig


class LeaderAgent(BaseReActAgent):
    """lead agent：① 直答 / ② 自干 / ③ 派发子智能体（含 planner），统一工具面。"""
    # name 必须与 config.json agents[].name 一致（resolve_agent_config 按此匹配配置：
    # 模型链、max_steps、tools 白名单）；注册规格名取 profile.name（"leader"）
    name = "leader"
    # 类属性：register() 经 cls.profile 构建注册规格
    #（actions 为空：决策走 tool_calls 分支，JSON Action 链已随工具化退役）
    profile: PromptConfig = SupervisorDecisionEngine.profile

    def __init__(self):
        super().__init__()
        # 协作组件：决策引擎（统一模板）+ 看板协调器（看板确定性操作）
        self.decision = SupervisorDecisionEngine()
        self.board = TaskBoardCoordinator()
        # run 级状态（实例即建即弃，不落会话状态）
        self._session_state: RuntimeSessionState | None = None

    # ── prompt 构建与输入组装 ──

    async def _prepare_agent(self, session_state: RuntimeSessionState) -> None:
        """stash 会话状态供 _prepare_prompt_params（render_board）使用后走基类组装。"""
        self._session_state = session_state
        await super()._prepare_agent(session_state)

    def _prepare_prompt_params(self) -> dict:
        """统一 prompt 变量：智能体清单（含 planner）+ 任务看板（新请求通常为空看板）。"""
        return {
            "agents_list_desc": self.decision.agents_list_desc(),
            "board_desc": self.board.render_board(self._session_state),
        }

    def _build_history_messages(self, session_state) -> list[dict]:
        """lead 注入会话历史（chat_messages 真相源，跨轮连续）。

        去除末条（本轮用户请求原文，已作为当前输入，避免重复）；
        子智能体执行历史（add_agent_result）与历史轮次回答回归历史消息承载；
        只保留 role/content 供 LLM 对话使用，附带字段（agent/task/success）丢弃。
        """
        cm = getattr(session_state, "context_manager", None)
        if cm is None or not cm.chat_messages:
            return []
        return [
            {"role": m["role"], "content": m["content"]}
            for m in cm.chat_messages[:-1]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

    # ── observation：终答跳（派发已在工具执行内完成，无派发跳组装） ──

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """无工具调用且输出非空文本 = 终答：LLM 最终文本即面向用户的答复
        （direct_answer / finish 合一；子智能体结果已在对话中，汇总即终答）。"""
        final_text = str((llm_response or {}).get("content") or "").strip()
        msg.sender = self.agent_name
        msg.recipient = "user"
        msg.result = final_text or "任务已完成。"
        msg.task_id = None
        msg.step_id = None
        msg.success = True
        return msg
