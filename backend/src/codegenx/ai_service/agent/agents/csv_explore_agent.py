"""CSV 表格分析智能体（工具化）

本 agent 通过原生 function calling 自主决策：
1. list_csv_files: 列出当前项目下可用 csv 文件
2. describe_csv:   查看文件结构与采样行
3. run_python:     执行 pandas 分析代码（代码中用 __CSV_DIR__ 占位符表示 csv 目录）

执行循环由 BaseReActAgent 的原生 tool_calls 循环承担：
think →（发起工具调用）→ 工具结果回填 → 继续 think → … → 无工具调用时自由文本作答。
简单任务一轮代码即可自总结答复，复杂任务多轮迭代；执行环境分支（本地/docker）
由 run_python 工具内部处理，本 agent 不感知。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codegenx.ai_service.agent.action import ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.context.common_context import CommonContext
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.schema.ai_schema import UserAIRequest
from codegenx.ai_service.utils.agent_utils import collect_csv_info
from shared import log

# 单个 csv 文件读入元信息的最大字节数（超过则只读头部）
MAX_CSV_BYTES_FOR_META = 1 * 1024 * 1024  # 1MB
# prompt 中每个 csv 展示的样本行数（不含表头）
CSV_SAMPLE_ROWS = 3
# prompt 中允许列出的 csv 文件数量上限
MAX_CSV_FILES_IN_PROMPT = 20


class CsvExploreAgent(BaseReActAgent):
    """ CSV 表格分析（工具化 agent：自主调用 list_csv_files / describe_csv / run_python） """

    name = "csv_explore"

    profile: PromptConfig = PromptConfig(
        role="Data Analyst",
        name="csv_explore",  # 和 config.json 中的 name 对应
        goal="分析当前项目下的 csv 表格文件，基于真实执行结果回答任务并给出结论",
        desc="读取当前项目下的 csv 表格文件，完成数据分析",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "可用工具：list_csv_files（列出可用 csv 文件）、describe_csv（查看文件结构与采样）、run_python（执行 pandas 分析代码）。",
            "csv 文件的结构与样本行已在下方提供，通常可直接编写代码；对不确定的列可先用 describe_csv 确认。",
            "run_python 的 code 中用占位符 __CSV_DIR__ 表示 csv 文件所在目录（框架执行时替换为真实路径），文件名自行拼接，例如：pd.read_csv(__CSV_DIR__ + '/order_2023.csv')。",
            "代码必须通过 print() 将分析结果输出到 stdout；禁止写文件、弹窗、绘图。",
            "仅可使用 pandas / numpy / Python 标准库，禁止引入 sklearn / matplotlib / scipy 等重型依赖。",
            "禁止虚构列名或数据值，列名以下方 csv 元信息与 describe_csv 结果为准；注意数值列的单位与缺失值。",
            "分析目标所需的统计（sum/mean/groupby/排序/筛选等）尽量在一次代码运行内完成，避免多次打印中间结果。",
            "运行环境：Python 3.10.9 / Pandas 2.2.3 / Numpy 1.26.4。",
            "执行完成后必须基于工具返回的真实输出给出结论：直接回答任务问题，简明陈述关键数值与依据，禁止编造数值。",
            "若代码执行报错，根据工具返回的 stderr 修正后重试；多次失败则如实说明原因。",
        ],
        resource_prompt="当前项目下可用的 csv 文件及其结构（列名、样本行）如下:\n{{csv_info}}",
        expand_prompt="",
        examples="",
        out_schema="最终答复直接用自然语言给出：先给结论，再列关键数据依据。不要输出 JSON，不要描述你的执行过程。",
    )

    def __init__(self, context: CommonContext | None = None):
        super().__init__(context=context)

    async def _prepare_prompt_params(self,request) -> dict:
        return {
            "csv_info": await collect_csv_info(request.user_id,request.app_id,MAX_CSV_FILES_IN_PROMPT)}

    async def _prepare_agent(self, request:UserAIRequest):
        """ 先收集 csv 元信息，再 build prompt（顺序与 data_explore 一致） """

        await super()._prepare_agent(request)


    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """观察最终答复：工具化循环结束后 LLM 的自由文本即结论，直接回传调用方。

        工具化模式下 act_output 只是 LLM 响应的封装（无 Action 链）；
        代码执行失败已通过 tool 消息回填由 LLM 自行修正重试，走到这里的
        都是已生成最终答复的正常路径。
        """
        final_text = str(llm_response.get("content") or "").strip()
        if not final_text:
            # 防御：run 循环已兜底空回复，正常不会走到这里
            msg.success = False
            return msg

        # 回传调用方（lead 同步派发执行器消费 result；task_id 原样保留供计划链路闭环）
        target = msg.sender or "user"
        log.info("CsvExploreAgent 完成 | 返回 {} | 答复长度={}", target, len(final_text))
        # 结论写进 result（回传跳 result 承载结果文本，task 保留原任务便于追溯）
        return self._reply(msg, target, final_text)


if __name__ == "__main__":
    import asyncio
    from codegenx.ai_service.context.sub_context import SubContext
    request = UserAIRequest(
        appId="test-app",
        userId="test-user",
        message="分析 sales.csv 的销售情况",
        sessionId="test-session",
        traceId="t1",
        requestId="r1",
    )

    async def test():

        agent = CsvExploreAgent()
        agent.resolve_agent_config()
        await agent._prepare_agent(request)

    asyncio.run(test())
