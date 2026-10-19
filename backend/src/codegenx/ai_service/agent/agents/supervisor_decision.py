"""Supervisor 决策引擎 —— lead agent 单一模板与智能体清单（从 leader_agent.py 拆分）。

lead agent 统一工具面（config.json 未配置 tools = 全量开放）：业务工具与编排工具
（dispatch_agent）同面平级，何时直答/自干/派发/请求计划由 LLM 在同一 ReAct 工具
循环内自主判断——提示词只给原则性指引与各智能体职责清单，不设步骤数/调用次数
等死板阈值。

agent-as-tool 同步派发：dispatch_agent 调用时子智能体内联执行（完整 run()），
其结果作为 tool result 回填对话——审查即 ReAct 本身（看到结果继续思考）。
派发不登记看板；计划任务看板由 LLM 经 task_* 工具显式管理。
不调用工具直接输出文本 = 终答（direct_answer / finish 合一）。

跨轮上下文由对话历史承载，轮内子智能体结果由工具结果承载，无模式切换。

SupervisorDecisionEngine 持有单一 PromptConfig 与智能体清单组装，
不持有运行时状态（每次 run 即取即用）。
"""

from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app


class SupervisorDecisionEngine:
    """决策引擎：统一 profile 与智能体清单（无运行时状态）。"""

    # ── 统一 profile（lead agent 定位；场景差异由本轮输入与对话历史承载）──
    profile: PromptConfig = PromptConfig(
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
        resource_prompt="",
        expand_prompt=(
            "可用的智能体如下：\n{{agents_list_desc}}\n\n"
            "任务看板：\n{{board_desc}}\n\n"
            "（历史用户请求、历史轮次的子智能体执行结果与最终答复见对话历史消息；"
            "本轮子智能体的执行结果以工具结果形式出现在对话中）"
        ),
        examples="",
        out_schema="",
    )

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
