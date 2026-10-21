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
- TaskBoardCoordinator（supervisor_board.py)：看板的全部确定性操作

编排状态 OrchestrationState 已退役：看板（按 request 落盘）即唯一共享状态，
执行轨迹观测由后续 trace/hook 模块承担。
"""

from typing import Any

from codegenx.ai_service.agent.action import ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.agents.supervisor_board import TaskBoardCoordinator
from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
from codegenx.ai_service.compact import estimate_tokens
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from codegenx.ai_service.utils.agent_utils import collect_info
from codegenx.ai_service.utils.config import config
from shared.constants import get_code_dir
from shared.utils.log_util import log


class LeaderAgent(BaseReActAgent):
    """lead agent：① 直答 / ② 自干 / ③ 派发子智能体（含 planner），统一工具面。"""
    # name 必须与 config.json agents[].name 一致（resolve_agent_config 按此匹配配置：
    # 模型链、max_steps、tools 白名单）；注册规格名取 profile.name（"leader"）
    name = "leader"
    # 类属性：register() 经 cls.profile 构建注册规格
    #（actions 为空：决策走 tool_calls 分支，JSON Action 链已随工具化退役）
    profile: PromptConfig =PromptConfig(
        role="leader",
        name="leader",
        goal="你是数据分析系统的 lead agent（首席执行与编排智能体）：既能直接调用工具完成操作，"
             "也能派发子智能体并行处理任务，还能把复杂请求交给 planner 拆解为分步计划。"
             "根据用户请求与执行结果自主判断推进方式，多轮推进直至给出最终答复。",
        desc="lead agent：直接执行任务，编排派发子智能体，复杂任务先规划",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            # ── 决策原则：整体从直接到间接，LLM 自主判断 ──
            "自主判断推进方式，整体原则：能用更直接的方式解决就不用更绕的方式——已有足够信息直接回答；自己调工具就能完成的操作自己做；需要并行处理或重计算的任务派发子智能体；任务复杂、步骤间存在依赖、需要先统筹拆解时派发 planner 制定计划。各智能体的职责见可用智能体清单。",
            "直接答复：打招呼、闲聊、无关/违规问题、信息歧义需反问，或已获取的信息足够回答用户时，不调用任何工具直接输出面向用户的文本。",
            "自己执行：查表结构、执行单条 SQL 统计、查看 CSV 清单与描述、简单 Python 计算等操作直接调用工具完成，工具结果回填后基于结果继续决策或汇总作答；派发子智能体的往返开销远大于这类操作，不要为小事派发。",
            "派发子智能体（dispatch_agent）：调用后子智能体立即执行，其最终结果作为工具结果回填对话，你基于结果继续决策。多个相互独立的子任务在同一回复中多次调用 = 并行执行。存在运行时数据依赖的后续任务等前置结果回填后再派发，派发时把已获得的前置结果内联进 task。",
            "派发 planner：任务复杂、需要先拆解为分步计划时，将用户原始请求派发给 planner（task 直接填用户原始请求，禁止附加编排建议；本请求看板已有计划任务时禁止再派发 planner）。planner 只产出任务分解并登记任务看板，每个任务怎么执行由你决定：自己用工具做或派发给合适的子智能体均可；你亲自执行的看板任务完成后调用 task_update 将其标记为 completed 以解锁下游任务。",
            # ── 职责分层与任务自包含 ──
            "对比、汇总、差值计算、结论生成是你的职责：禁止把跨数据源的汇总要求写进派发给执行智能体的 task 里。派发的 task 必须完全自包含：执行智能体只看得到 task 文本本身，看不到对话历史与其它任务，执行所需的全部信息（文件名、数值、统计口径）必须直接写进 task，禁止「结合前文」「上一步的结果」等引用；task 以执行智能体自身的能力与输出规范为准，禁止指定特定输出格式。",
            # ── 防循环约束 ──
            "回传结果出现矛盾时：最多派发一次口径明确的核实任务（一次性取全口径数据，描述必须与原任务不同），核实结果回填后必须直接汇总作答，禁止以格式不符等理由对同一智能体重复派发相同或相似任务。",
            "已派发过的任务不要重复派发：其执行结果已在对话中（工具结果），基于已有结果继续决策或汇总作答即可。",
        ],
        resource_prompt="可用资源如下：\n 有权限文件夹:{{available_file_path}}\n有权限数据库:{{available_db}}",
        expand_prompt=(
            "可用的智能体如下：\n{{agents_list_desc}}\n\n"
            "任务看板：\n{{board_desc}}\n\n"
            "相关长期记忆：\n{{memory_prompt}}\n\n"
            # "历史会话摘要（更早轮次的压缩记忆）：\n{{session_summary}}\n\n"
            # "（历史用户请求、历史轮次的子智能体执行结果与最终答复见对话历史消息；"
            # "本轮子智能体的执行结果以工具结果形式出现在对话中）"
        ),
        examples="",
        out_schema="",
    )

    def __init__(self):
        super().__init__()
        # 看板协调器（看板确定性操作）
        self.board = TaskBoardCoordinator()
        # run 级状态（实例即建即弃，不落会话状态）
        self._session_state: RuntimeSessionState | None = None
        # 记忆/会话摘要注入文本（_prepare_agent 预取，_prepare_prompt_params 消费）
        self._memory_prompt: str = ""
        self._session_summary: str = ""

    # ── prompt 构建与输入组装 ──
    @staticmethod
    def agents_list_desc() -> str:
        """可用智能体清单（含 planner：dispatch_agent 可派发 planner 请求规划）。"""
        skip_roles = {"leader", "supervisor", "intent_recognition"}
        lines = []
        for spec in get_app().agents.all_specs():
            if spec.role in skip_roles:
                continue
            lines.append(f"{spec.name} - {spec.desc}")
        return "\n".join(lines) or "（暂无可用子智能体）"

    async def _prepare_agent(self, session_state: RuntimeSessionState) -> None:
        """stash 会话状态 + 预取记忆与会话摘要（expand 变量注入）后走基类组装。"""
        self._session_state = session_state
        # 以当前用户请求为 query 检索长期记忆；会话摘要由 compact_after_turn 后台产出
        query = str(getattr(session_state.request, "message", "") or "")
        app = get_app()
        self._memory_prompt = await app.memory.load(
            query,
            user_id=session_state.user_id,
            app_id=session_state.app_id,
            session_id=session_state.session_id,
        )
        # 会话摘要为同步文件读取（SessionSummaryService.load 非 async），不能 await
        self._session_summary = app.summary.load(
            user_id=session_state.user_id,
            app_id=session_state.app_id,
            session_id=session_state.session_id,
        )
        await super()._prepare_agent(session_state)
        log.debug(self._system_prompt)

    def _prepare_prompt_params(self) -> dict:
        """统一 prompt 变量：智能体清单（含 planner）+ 任务看板 + 记忆/摘要注入。"""
        return {
            "available_file_path":get_code_dir(self._session_state.user_id,self._session_state.app_id),
            "available_db":self._session_state.db_name,
            "agents_list_desc": self.agents_list_desc(),
            "board_desc": self.board.render_board(self._session_state),
            "memory_prompt": self._memory_prompt or "（暂无相关长期记忆）",
            # "session_summary": self._session_summary or "（暂无历史会话摘要）",
        }

    def _build_history_messages(self, session_state) -> list[dict]:
        """lead 注入会话历史（chat_messages 真相源，跨轮连续）。

        去除末条（本轮用户请求原文，已作为当前输入，避免重复）；
        子智能体执行历史（add_agent_result）与历史轮次回答回归历史消息承载；
        只保留 role/content 供 LLM 对话使用，附带字段（agent/task/success）丢弃。
        超出 token 预算从最旧截断，更早轮次由会话摘要（session_summary）兜底。
        """
        cm = getattr(session_state, "context_manager", None)
        if cm is None or not cm.chat_messages:
            return []
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in cm.chat_messages[:-1]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        return self._apply_history_budget(history)

    @staticmethod
    def _apply_history_budget(history: list[dict]) -> list[dict]:
        """会话历史 token 预算：超限从最旧截断（单次遍历，保留预算内最新消息）。

        历史仅 user/assistant 文本消息（无 tool 消息），microcompact 对其无效，
        直接做最旧截断；预算阈值经 config.compact.leader_history_budget_tokens 配置。
        """
        budget = config.compact.leader_history_budget_tokens
        if estimate_tokens(history) <= budget:
            return history
        kept: list[dict] = []
        total = 0
        for m in reversed(history):
            cost = estimate_tokens([m])
            if total + cost > budget and kept:
                break
            kept.append(m)
            total += cost
        kept.reverse()
        log.info("leader 历史超预算截断：{} 条 -> {} 条（budget={} tokens）",
                 len(history), len(kept), budget)
        return kept

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
