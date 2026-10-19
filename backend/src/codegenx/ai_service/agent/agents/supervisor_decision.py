"""Supervisor 决策引擎 —— 单一模板与智能体清单（从 intention_agent.py 拆分）。

P2 工具化：决策协议从「think 输出 JSON → SupervisorAction 解析」改为
function-calling（dispatch_agent / request_plan 决策工具，见 tools/supervisor.py）：
  - dispatch_agent：派发子任务（同轮多次调用 = 批量并行）；
  - request_plan：转 planner 制定计划；
  - 不调用工具直接输出文本 = 终答（direct_answer / finish 合一）。
原 JSON 决策协议（SupervisorActionType / SupervisorDecision / SupervisorAction）
已随工具化退役删除。

P3 模板合一：route/review 双 profile 合并为单一 profile。路由与审查的区分不再
靠 system prompt 切换，而由「本轮输入 + 对话历史」承载：
  - 路由（sender == user）：本轮输入 = 用户请求原文；
  - 审查（sender == 执行智能体/batch）：本轮输入 = 审查框架改写的最新回传结果
    （见 SupervisorAgent._extract_task_text 的 sender 分支）。
request_plan 的可用性由 _exec_request_plan 运行时校验（计划模式下拒绝），
模板层不再区分清单。

SupervisorDecisionEngine 持有单一 PromptConfig 与智能体清单组装，
不持有运行时状态（每次 run 即取即用）。
"""

from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app


class SupervisorDecisionEngine:
    """决策引擎：统一 profile 与智能体清单（无运行时状态）。"""

    # ── 统一 profile（路由与审查共用；场景差异由本轮输入与对话历史承载）──
    profile: PromptConfig = PromptConfig(
        role="supervisor",
        name="supervisor",
        goal="作为数据分析多智能体系统的监管者，根据用户请求与子智能体执行结果进行编排决策："
             "派发执行智能体、请求 planner 制定计划，或直接输出面向用户的答复。",
        desc="入口监管者，负责路由与多轮编排",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            # ── 决策方式（路由与审查共用三选一）──
            "决策方式三选一：直接答复（不调用任何工具，输出面向用户的文本）/ 调用 dispatch_agent 工具派发执行智能体 / 调用 request_plan 工具请求 planner 制定计划。",
            "直接答复的时机：打招呼、闲聊、无关/违规问题、信息歧义需反问、根据已有信息即可回答用户；审查执行结果时，所有任务完成或最新回传结果已包含答案所需数据 → 直接输出汇总答复（基于对话历史中的执行结果与本轮输入），禁止以格式不符等理由对同一智能体重复派发相同或相似任务。",
            # ── dispatch_agent：派发与审查补派 ──
            "dispatch_agent（默认选择）：子任务数量少（通常 1~3 个）且相互独立时逐个派发——各子任务分别取数或各自可独立完成，相互之间没有运行时数据依赖。相互独立的子任务在同一个回复中多次调用 dispatch_agent 批量派发（系统并行执行，效率更高）；存在运行时数据依赖的子任务等前置结果回传后再派发，派发后续任务时可基于已回传的结果构造 task 描述。",
            "direct_reply 判据（仅派发单个任务时生效）：本回复只派发恰好 1 个任务、且其执行结果无需任何跨任务对比/汇总/换算即可作为最终答复时设 true（执行智能体完成即直接答复用户，省去审查环节）；派发 2 个及以上任务时必须全部为 false（执行完回传由你汇总）。",
            # ── request_plan：计划模式 ──
            "request_plan：仅当步骤较多（通常 ≥4 个）且存在复杂依赖链（后续步骤要做什么取决于前面步骤的运行时结果，需要按计划确定性调度）时使用。task 直接填用户的原始请求，禁止附加任何编排建议或执行方案（计划由 planner 自行制定）。计划模式僵局（看板无就绪任务但计划未完成）时：调用 dispatch_agent 指定该智能体，系统会优先派发其看板待办任务；计划中 executor_agent 为 supervisor 的汇总类步骤由你自己完成，在直接输出中给出结果，禁止把 supervisor 作为派发目标。",
            # ── 职责分层与任务自包含 ──
            "对比、汇总、差值计算、结论生成是你的职责：禁止为了汇总多个子任务的结果而调用 request_plan，也禁止把跨数据源的汇总要求写进派发给执行智能体的 task 里。task 只要求执行智能体产出其数据源内的原始统计结果，差值、增长率、对比结论等由你在最终答复中计算。",
            "派发的 task 必须完全自包含：执行智能体只看得到 task 文本本身，看不到对话历史与其它任务，执行所需的全部信息（文件名、数值、统计口径）必须直接写进 task，禁止出现「结合前文」「上一步的结果」等引用；task 以执行智能体自身的能力与输出规范为准，禁止指定特定输出格式。",
            # ── 防循环约束 ──
            "回传结果出现矛盾时：最多派发一次口径明确的核实任务（一次性取全口径数据，例如按年份分组统计全部指标），禁止连环多次核实；核实结果回传后必须直接汇总作答。核实任务的描述必须与原任务不同（新口径），否则会被系统拒绝。",
            "每个子任务只调用一次 dispatch_agent：调用后会收到「已登记任务 #编号」确认，收到确认即代表登记成功（本轮决策结束后统一派发执行），不要再次调用相同任务；全部任务登记完毕后直接输出简短说明文本结束本轮（例如：已派发 N 个任务，等待执行结果），不要再调用 dispatch_agent。",
        ],
        resource_prompt="",
        expand_prompt=(
            "可用的智能体如下：\n{{agents_list_desc}}\n\n"
            "任务看板：\n{{board_desc}}\n\n"
            "（历史用户请求与子 agent 回传结果见对话历史消息，最新回传见本轮输入；"
            "批量派发的批次在本轮输入中含全部子任务结果）"
        ),
        examples="",
        out_schema="",
    )

    @staticmethod
    def agents_list_desc() -> str:
        """可用智能体清单（统一含 planner：request_plan 可用性由运行时校验）。"""
        skip_roles = {"supervisor", "intent_recognition"}
        lines = []
        for spec in get_app().agents.all_specs():
            if spec.role in skip_roles:
                continue
            lines.append(f"{spec.name} - {spec.desc}")
        return "\n".join(lines) or "（暂无可用子智能体）"
